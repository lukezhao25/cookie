from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

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
        CLEAR_THRESHOLD = 75
        CLEAR_QUANTITY = 20

        for product in state.order_depths:
            if product != "EMERALDS":
                continue

            orders: List[Order] = []
            position = state.position.get(product, 0)
            order_depth: OrderDepth = state.order_depths[product]

            # Take mispriced orders
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

            # Passive quoting
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