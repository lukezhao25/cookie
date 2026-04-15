import subprocess
import json
import re
import itertools
import pandas as pd
from pathlib import Path

# Grid search parameters
base_min_wall_vols = [4, 6, 8, 10, 12]
take_min_edges = [1.0, 2.0, 3.0, 4.0, 5.0]
passive_sizes = [5, 10, 15, 20]
clear_thresholds = [50, 60, 70]
clear_sizes = [10, 20, 30]
smooth_alphas = [0.25, 0.5]


def write_algo(base_min_wall_vol, take_min_edge, passive_size, clear_threshold, clear_size, smooth_alpha):
    code = f'''import json
import math
from typing import Optional
from datamodel import OrderDepth, TradingState, Order, Symbol

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects, sep=" ", end="\\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state, orders, conversions, traderData) -> None:
        base_length = len(self.to_json([
            self.compress_state(state, ""),
            self.compress_orders(orders),
            conversions, "", "",
        ]))
        max_item_length = (self.max_log_length - base_length) // 3
        print(self.to_json([
            self.compress_state(state, self.truncate(state.traderData, max_item_length)),
            self.compress_orders(orders),
            conversions,
            self.truncate(traderData, max_item_length),
            self.truncate(self.logs, max_item_length),
        ]))
        self.logs = ""

    def compress_state(self, state, traderData):
        return [state.timestamp, traderData,
            self.compress_listings(state.listings),
            self.compress_order_depths(state.order_depths),
            self.compress_trades(state.own_trades),
            self.compress_trades(state.market_trades),
            state.position,
            self.compress_observations(state.observations)]

    def compress_listings(self, listings):
        return [[l.symbol, l.product, l.denomination] for l in listings.values()]

    def compress_order_depths(self, order_depths):
        compressed = {{}}
        for symbol, order_depth in order_depths.items():
            compressed[symbol] = [order_depth.buy_orders, order_depth.sell_orders]
        return compressed

    def compress_trades(self, trades):
        return [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
                for arr in trades.values() for t in arr]

    def compress_observations(self, observations):
        conv = {{}}
        for p, o in observations.conversionObservations.items():
            conv[p] = [o.bidPrice, o.askPrice, o.transportFees,
                       o.exportTariff, o.importTariff, o.sugarPrice, o.sunlightIndex]
        return [observations.plainValueObservations, conv]

    def compress_orders(self, orders):
        return [[o.symbol, o.price, o.quantity] for arr in orders.values() for o in arr]

    def to_json(self, value):
        return json.dumps(value, separators=(",", ":"), default=lambda o: o.__dict__)

    def truncate(self, value, max_length):
        return value if len(value) <= max_length else value[:max_length - 3] + "..."

logger = Logger()


class Trader:
    PRODUCT = "TOMATOES"
    LIMIT = 80

    BASE_MIN_WALL_VOL = {base_min_wall_vol}
    TAKE_MIN_EDGE = {take_min_edge}
    PASSIVE_SIZE = {passive_size}
    CLEAR_THRESHOLD = {clear_threshold}
    CLEAR_SIZE = {clear_size}
    SMOOTH_ALPHA = {smooth_alpha}

    def _load_prev_wall_mid(self, trader_data: str) -> Optional[float]:
        if not trader_data:
            return None
        try:
            payload = json.loads(trader_data)
            value = payload.get("wall_mid")
            if isinstance(value, (int, float)):
                return float(value)
        except Exception:
            return None
        return None

    def _adaptive_wall_threshold(self, vols: list) -> int:
        if not vols:
            return self.BASE_MIN_WALL_VOL
        sorted_vols = sorted(vols)
        idx = int(0.7 * (len(sorted_vols) - 1))
        return max(self.BASE_MIN_WALL_VOL, sorted_vols[idx])

    def _estimate_wall_prices(self, depth: OrderDepth):
        best_bid = max(depth.buy_orders.keys())
        best_ask = min(depth.sell_orders.keys())

        bid_levels = sorted(depth.buy_orders.items(), reverse=True)
        ask_levels = sorted(depth.sell_orders.items())

        bid_thresh = self._adaptive_wall_threshold([max(0, vol) for _, vol in bid_levels])
        ask_thresh = self._adaptive_wall_threshold([max(0, -vol) for _, vol in ask_levels])

        wall_bid = max(bid_levels, key=lambda x: x[1])[0]
        for price, vol in bid_levels:
            if vol >= bid_thresh:
                wall_bid = price
                break

        wall_ask = min(ask_levels, key=lambda x: x[1])[0]
        for price, vol in ask_levels:
            if -vol >= ask_thresh:
                wall_ask = price
                break

        return best_bid, best_ask, wall_bid, wall_ask

    def run(self, state: TradingState):
        result = {{}}
        conversions = 0
        prev_wall_mid = self._load_prev_wall_mid(state.traderData)
        trader_data = ""

        if self.PRODUCT not in state.order_depths:
            logger.flush(state, result, conversions, trader_data)
            return result, conversions, trader_data

        depth = state.order_depths[self.PRODUCT]
        orders = []
        position = state.position.get(self.PRODUCT, 0)

        if not depth.buy_orders or not depth.sell_orders:
            result[self.PRODUCT] = orders
            logger.flush(state, result, conversions, trader_data)
            return result, conversions, trader_data

        best_bid, best_ask, wall_bid, wall_ask = self._estimate_wall_prices(depth)
        raw_wall_mid = (wall_bid + wall_ask) / 2.0
        if prev_wall_mid is None:
            wall_mid = raw_wall_mid
        else:
            wall_mid = (1 - self.SMOOTH_ALPHA) * prev_wall_mid + self.SMOOTH_ALPHA * raw_wall_mid

        spread = best_ask - best_bid
        take_edge = max(self.TAKE_MIN_EDGE, spread * 0.5)

        for ask_price, ask_vol in sorted(depth.sell_orders.items()):
            if wall_mid - ask_price < take_edge:
                break
            buy_cap = self.LIMIT - position
            qty = min(-ask_vol, buy_cap)
            if qty > 0:
                orders.append(Order(self.PRODUCT, ask_price, qty))
                position += qty

        for bid_price, bid_vol in sorted(depth.buy_orders.items(), reverse=True):
            if bid_price - wall_mid < take_edge:
                break
            sell_cap = self.LIMIT + position
            qty = min(bid_vol, sell_cap)
            if qty > 0:
                orders.append(Order(self.PRODUCT, bid_price, -qty))
                position -= qty

        buy_cap = self.LIMIT - position
        sell_cap = self.LIMIT + position

        inventory_skew = 0.02 * position
        fair = wall_mid - inventory_skew
        passive_edge = max(1.0, spread / 3.0)
        quote_bid = min(best_bid + 1, int(math.floor(fair - passive_edge)))
        quote_ask = max(best_ask - 1, int(math.ceil(fair + passive_edge)))

        if quote_bid >= quote_ask:
            quote_bid = best_bid
            quote_ask = best_ask

        if buy_cap > 0:
            bid_qty = min(self.PASSIVE_SIZE, buy_cap)
            orders.append(Order(self.PRODUCT, quote_bid, bid_qty))

        if sell_cap > 0:
            ask_qty = min(self.PASSIVE_SIZE, sell_cap)
            orders.append(Order(self.PRODUCT, quote_ask, -ask_qty))

        fair_round = int(round(wall_mid))
        if position > self.CLEAR_THRESHOLD:
            clear_qty = min(position - self.CLEAR_THRESHOLD, self.CLEAR_SIZE, self.LIMIT + position)
            if clear_qty > 0:
                clear_price = best_bid if best_bid >= fair_round else fair_round
                orders.append(Order(self.PRODUCT, clear_price, -clear_qty))
        elif position < -self.CLEAR_THRESHOLD:
            clear_qty = min((-self.CLEAR_THRESHOLD - position), self.CLEAR_SIZE, self.LIMIT - position)
            if clear_qty > 0:
                clear_price = best_ask if best_ask <= fair_round else fair_round
                orders.append(Order(self.PRODUCT, clear_price, clear_qty))

        result[self.PRODUCT] = orders
        trader_data = json.dumps({{"wall_mid": wall_mid}}, separators=(",", ":"))
        logger.flush(state, result, conversions, trader_data)
        return result, conversions, trader_data
'''
    with open("_grid_algo.py", "w") as f:
        f.write(code)


def parse_output(output: str):
    day_profits = {}
    for match in re.finditer(r"Round \d+ day (-?\d+): ([\d,]+)", output):
        day = int(match.group(1))
        profit = int(match.group(2).replace(",", ""))
        day_profits[day] = profit
    drawdown_match = re.search(r"max_drawdown_pct: ([\d.]+)", output)
    max_dd = float(drawdown_match.group(1)) if drawdown_match else None
    return day_profits, max_dd


results = []
total = len(base_min_wall_vols) * len(take_min_edges) * len(passive_sizes) * len(clear_thresholds) * len(clear_sizes) * len(smooth_alphas)
done = 0

for bmwv, tme, ps, ct, cs, sa in itertools.product(
    base_min_wall_vols, take_min_edges, passive_sizes, clear_thresholds, clear_sizes, smooth_alphas
):
    write_algo(bmwv, tme, ps, ct, cs, sa)

    proc = subprocess.run(
        ["prosperity4btest", "_grid_algo.py", "0", "--no-out"],
        capture_output=True, text=True
    )

    day_profits, max_dd = parse_output(proc.stdout)
    day_neg2 = day_profits.get(-2, 0)
    day_neg1 = day_profits.get(-1, 0)

    results.append({
        "base_min_wall_vol": bmwv,
        "take_min_edge": tme,
        "passive_size": ps,
        "clear_threshold": ct,
        "clear_size": cs,
        "smooth_alpha": sa,
        "day_-2_profit": day_neg2,
        "day_-1_profit": day_neg1,
        "total_profit": day_neg2 + day_neg1,
        "max_drawdown_pct": max_dd,
    })

    done += 1
    if done % 20 == 0:
        print(f"Progress: {done}/{total}")

df = pd.DataFrame(results)
df.to_csv("tomatoes_grid_results.csv", index=False)
print(f"\nDone. Results saved to tomatoes_grid_results.csv")

# Print top 20 by total profit
print("\n=== TOP 50 BY TOTAL PROFIT ===")
print(df.nlargest(50, "total_profit").to_string(index=False))

# Print top 20 by total profit with drawdown filter
print("\n=== TOP 50 BY TOTAL PROFIT (max_drawdown_pct < 5) ===")
filtered = df[df["max_drawdown_pct"] < 10]
print(filtered.nlargest(50, "total_profit").to_string(index=False))

Path("_grid_algo.py").unlink()