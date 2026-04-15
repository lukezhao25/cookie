from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects, sep=" ", end="\n") -> None:
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
        return [[s, od.buy_orders, od.sell_orders] for s, od in order_depths.items()]

    def compress_trades(self, trades):
        return [[t.symbol, t.price, t.quantity, t.buyer, t.seller, t.timestamp]
                for arr in trades.values() for t in arr]

    def compress_observations(self, observations):
        conv = {}
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

    def bid(self):
        return 15

    def run(self, state: TradingState):
        result = {}
        conversions = 0
        traderData = ""

        FAIR_VALUE = 10000
        NORMAL_BID = 9993
        NORMAL_ASK = 10007
        CLEAR_PRICE_BID = 10000
        CLEAR_PRICE_ASK = 10000
        POSITION_LIMIT = 80
        CLEAR_THRESHOLD = 5
        CLEAR_QUANTITY = 25

        for product in state.order_depths:
            if product != "EMERALDS":
                continue

            orders = []
            position = state.position.get(product, 0)
            order_depth = state.order_depths[product]

            for ask_price, ask_vol in sorted(order_depth.sell_orders.items()):
                if ask_price < FAIR_VALUE:
                    buy_qty = min(-ask_vol, POSITION_LIMIT - position)
                    if buy_qty > 0:
                        orders.append(Order(product, ask_price, buy_qty))
                        position += buy_qty

            for bid_price, bid_vol in sorted(order_depth.buy_orders.items(), reverse=True):
                if bid_price > FAIR_VALUE:
                    sell_qty = min(bid_vol, POSITION_LIMIT + position)
                    if sell_qty > 0:
                        orders.append(Order(product, bid_price, -sell_qty))
                        position -= sell_qty

            buy_capacity = POSITION_LIMIT - position
            sell_capacity = POSITION_LIMIT + position

            if position > CLEAR_THRESHOLD:
                if sell_capacity > 0:
                    orders.append(Order(product, CLEAR_PRICE_ASK, -min(CLEAR_QUANTITY, sell_capacity)))
            elif position < -CLEAR_THRESHOLD:
                if buy_capacity > 0:
                    orders.append(Order(product, CLEAR_PRICE_BID, min(CLEAR_QUANTITY, buy_capacity)))
            else:
                if buy_capacity > 0:
                    orders.append(Order(product, NORMAL_BID, buy_capacity))
                if sell_capacity > 0:
                    orders.append(Order(product, NORMAL_ASK, -sell_capacity))

            result[product] = orders

        logger.flush(state, result, conversions, traderData)
        return result, conversions, traderData
