from datamodel import OrderDepth, TradingState, Order
from typing import List, Optional
import json
import math

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects, sep=" ", end="\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders, conversions, traderData) -> None:
        base_length = len(
            self.to_json([
                self.compress_state(state, ""),
                self.compress_orders(orders),
                conversions,
                "",
                "",
            ])
        )
        max_item_length = (self.max_log_length - base_length) // 3
        print(self.to_json([
            self.compress_state(state, self.truncate(state.traderData, max_item_length)),
            self.compress_orders(orders),
            conversions,
            self.truncate(traderData, max_item_length),
            self.truncate(self.logs, max_item_length),
        ]))
        self.logs = ""

    def compress_state(self, state: TradingState, traderData: str) -> list:
        return [
            state.timestamp,
            traderData,
            self.compress_listings(state.listings),
            self.compress_order_depths(state.order_depths),
            self.compress_trades(state.own_trades),
            self.compress_trades(state.market_trades),
            state.position,
            self.compress_observations(state.observations),
        ]

    def compress_listings(self, listings) -> list:
        compressed = []
        for listing in listings.values():
            compressed.append([listing.symbol, listing.product, listing.denomination])
        return compressed

    def compress_order_depths(self, order_depths) -> list:
        # compressed = []
        # for symbol, order_depth in order_depths.items():
        #     compressed.append([symbol, order_depth.buy_orders, order_depth.sell_orders])
        # return compressed
        compressed = {}
        for symbol, order_depth in order_depths.items():
            compressed[symbol] = [order_depth.buy_orders, order_depth.sell_orders]
        return compressed

    def compress_trades(self, trades) -> list:
        compressed = []
        for arr in trades.values():
            for trade in arr:
                compressed.append([trade.symbol, trade.price, trade.quantity,
                                    trade.buyer, trade.seller, trade.timestamp])
        return compressed

    def compress_observations(self, observations) -> list:
        conversion_observations = {}
        for product, observation in observations.conversionObservations.items():
            conversion_observations[product] = [
                observation.bidPrice, observation.askPrice,
                observation.transportFees, observation.exportTariff,
                observation.importTariff, observation.sugarPrice,
                observation.sunlightIndex,
            ]
        return [observations.plainValueObservations, conversion_observations]

    def compress_orders(self, orders) -> list:
        compressed = []
        for arr in orders.values():
            for order in arr:
                compressed.append([order.symbol, order.price, order.quantity])
        return compressed

    def to_json(self, value) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value
        return value[:max_length - 3] + "..."

class ProsperityEncoder(json.JSONEncoder):
    def default(self, o):
        return o.__dict__

logger = Logger()

class Trader:
    PRODUCT = "TOMATOES"
    LIMIT = 80

    # Keep parameters broad and conservative to reduce overfitting risk.
    BASE_MIN_WALL_VOL = 6
    TAKE_MIN_EDGE = 3.0
    PASSIVE_SIZE = 5
    CLEAR_THRESHOLD = 50
    CLEAR_SIZE = 10
    SMOOTH_ALPHA = 0.25

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

    def _adaptive_wall_threshold(self, vols: list[int]) -> int:
        if not vols:
            return self.BASE_MIN_WALL_VOL
        sorted_vols = sorted(vols)
        idx = int(0.7 * (len(sorted_vols) - 1))
        return max(self.BASE_MIN_WALL_VOL, sorted_vols[idx])

    def _estimate_wall_prices(self, depth: OrderDepth) -> tuple[int, int, int, int]:
        best_bid = max(depth.buy_orders.keys())
        best_ask = min(depth.sell_orders.keys())

        bid_levels = sorted(depth.buy_orders.items(), reverse=True)  # high to low
        ask_levels = sorted(depth.sell_orders.items())  # low to high (negative vol)

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
        result: dict[Symbol, list[Order]] = {}
        conversions = 0
        prev_wall_mid = self._load_prev_wall_mid(state.traderData)
        trader_data = ""

        if self.PRODUCT not in state.order_depths:
            logger.flush(state, result, conversions, trader_data)
            return result, conversions, trader_data

        depth = state.order_depths[self.PRODUCT]
        orders: list[Order] = []
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

        # 1) Immediately take favorable liquidity relative to wall_mid.
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

        # 2) Overbid/undercut around fair value estimate.
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

        # 3) Neutralize inventory with zero-edge orders near wall_mid.
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
        trader_data = json.dumps({"wall_mid": wall_mid}, separators=(",", ":"))
        logger.flush(state, result, conversions, trader_data)
        return result, conversions, trader_data
