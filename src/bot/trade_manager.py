from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from .binance_client import BinanceFuturesClient, SymbolFilters

@dataclass
class ManagedOrder:
    order_id: int
    client_order_id: str
    side: str
    order_type: str
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    target_index: Optional[int] = None

@dataclass
class ActiveTrade:
    side: str  # long or short
    entry_kind: str  # market or limit
    quantity: float
    tp_targets: List[Dict[str, float]]
    sl_price: float
    position_open: bool = False
    entry_price: Optional[float] = None
    entry_order: Optional[ManagedOrder] = None
    stop_loss_order: Optional[ManagedOrder] = None
    take_profit_orders: List[ManagedOrder] = field(default_factory=list)

class TradeManager:
    def __init__(
        self,
        client: BinanceFuturesClient,
        symbol: str,
        leverage: Optional[int],
        margin_type: str,
        margin_usage_pct: float,
    ) -> None:
        self.client = client
        self.symbol = symbol
        self.explicit_leverage = leverage
        self.margin_type = margin_type
        self.margin_usage_pct = margin_usage_pct
        self.filters: SymbolFilters = self.client.get_symbol_filters(symbol)
        self.margin_asset = self._determine_margin_asset()
        self.trade: Optional[ActiveTrade] = None
        self.leverage: Optional[int] = None
        self._last_position_size = 0.0

    def initialize(self) -> None:
        self.client.set_margin_type(self.symbol, self.margin_type)
        leverage = self.explicit_leverage or self._fetch_max_leverage()
        self.client.set_leverage(self.symbol, leverage)
        self.leverage = leverage

    def _fetch_max_leverage(self) -> int:
        brackets = self.client.get_leverage_brackets(self.symbol)
        return max(int(b["initialLeverage"]) for b in brackets)

    def _determine_margin_asset(self) -> str:
        info = self.client.get_exchange_info()
        for sym in info.get("symbols", []):
            if sym.get("symbol") == self.symbol:
                asset = sym.get("marginAsset")
                if asset:
                    return str(asset)
                break
        raise ValueError(f"Margin asset for {self.symbol} not found in exchange info")

    def _calculate_margin_allocation(self) -> float:
        account = self.client.get_account_information()

        def select_balance(wallet: float, available: float) -> float:
            balance_reference = wallet if wallet > 0 else available
            if available > 0:
                balance_reference = min(balance_reference, available)
            return balance_reference

        wallet_balance = float(account.get("totalWalletBalance", 0.0))
        available = float(account.get("availableBalance", wallet_balance))
        balance_reference = select_balance(wallet_balance, available)

        if balance_reference <= 0:
            margin_asset_entry = next(
                (asset for asset in account.get("assets", []) if asset.get("asset") == self.margin_asset),
                None,
            )
            if margin_asset_entry:
                asset_wallet = float(margin_asset_entry.get("walletBalance", 0.0))
                asset_available = float(margin_asset_entry.get("availableBalance", asset_wallet))
                balance_reference = select_balance(asset_wallet, asset_available)

        if balance_reference <= 0:
            raise ValueError("Account balance is non-positive; cannot size trade")

        margin_allocation = balance_reference * self.margin_usage_pct
        return margin_allocation

    def _compute_order_quantity(self, reference_price: float) -> float:
        if reference_price <= 0:
            raise ValueError("Invalid reference price for sizing")

        leverage = self.leverage or self._fetch_max_leverage()
        margin_allocation = self._calculate_margin_allocation()
        notional = margin_allocation * leverage
        if self.filters.min_notional and notional < self.filters.min_notional:
            raise ValueError("Calculated notional below Binance minimum notional requirement")

        raw_qty = notional / reference_price
        normalized = self.filters.normalize_qty(raw_qty)

        if normalized < self.filters.min_qty:
            raise ValueError("Calculated order quantity below Binance minimum lot size")

        return normalized

    def _place_market_entry(self, side: str, quantity: float) -> ManagedOrder:
        params = {
            "symbol": self.symbol,
            "side": "BUY" if side == "long" else "SELL",
            "type": "MARKET",
            "quantity": f"{quantity:.8f}",
        }
        order = self.client.place_order(params)
        executed_qty = float(order["executedQty"])
        entry_price = float(order.get("avgPrice") or order.get("price") or 0)
        managed = ManagedOrder(
            order_id=int(order["orderId"]),
            client_order_id=order["clientOrderId"],
            side=params["side"],
            order_type="MARKET",
            quantity=executed_qty,
            price=entry_price,
        )
        return managed

    def _place_limit_entry(self, side: str, quantity: float, limit_price: float) -> ManagedOrder:
        if self.trade and self.trade.entry_order and not self.trade.position_open:
            raise RuntimeError("Pending limit entry already exists; cancel it before placing another.")

        limit_price = self.filters.normalize_price(limit_price)

        params = {
            "symbol": self.symbol,
            "side": "BUY" if side == "long" else "SELL",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": f"{quantity:.8f}",
            "price": f"{limit_price:.8f}",
        }

        order = self.client.place_order(params)

        managed = ManagedOrder(
            order_id=int(order["orderId"]),
            client_order_id=order["clientOrderId"],
            side=params["side"],
            order_type="LIMIT",
            quantity=float(order.get("origQty", quantity)),
            price=limit_price,
        )

        return managed

    def _place_stop_loss(self, side: str, stop_price: float) -> ManagedOrder:
        stop_price = self.filters.normalize_price(stop_price)
        params = {
            "symbol": self.symbol,
            "side": "SELL" if side == "long" else "BUY",
            "type": "STOP_MARKET",
            "stopPrice": f"{stop_price:.8f}",
            "closePosition": True,
            "workingType": "MARK_PRICE",
        }

        order = self.client.place_order(params)

        managed = ManagedOrder(
            order_id=int(order["orderId"]),
            client_order_id=order["clientOrderId"],
            side=params["side"],
            order_type="STOP_MARKET",
            quantity=0.0,
            stop_price=stop_price,
        )

        return managed

    def _place_take_profits(self, side: str, quantity: float, targets: List[Dict[str, float]]) -> List[ManagedOrder]:
        orders: List[ManagedOrder] = []
        allocated = 0.0
        for idx, target in enumerate(targets):
            price = self.filters.normalize_price(float(target["price"]))
            size_pct = float(target.get("size_pct", 0))

            if idx == len(targets) - 1:
                qty = self.filters.normalize_qty(max(quantity - allocated, 0.0))
            else:
                qty = self.filters.normalize_qty(quantity * size_pct / 100.0)
                allocated += qty

            if qty <= 0:
                continue

            params = {
                "symbol": self.symbol,
                "side": "SELL" if side == "long" else "BUY",
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": f"{price:.8f}",
                "quantity": f"{qty:.8f}",
                "reduceOnly": True,
            }

            order = self.client.place_order(params)

            managed = ManagedOrder(
                order_id=int(order["orderId"]),
                client_order_id=order["clientOrderId"],
                side=params["side"],
                order_type="TAKE_PROFIT_MARKET",
                quantity=qty,
                stop_price=price,
                target_index=idx,
            )
            orders.append(managed)
        return orders

    def _cancel_order(self, order: ManagedOrder) -> None:
        self.client.cancel_order(self.symbol, order_id=order.order_id)

    def _cancel_protective_orders(self) -> None:
        if not self.trade:
            return

        for order_ref in list(self.trade.take_profit_orders):
            try:
                self._cancel_order(order_ref)
            except Exception:
                pass

        self.trade.take_profit_orders.clear()

        if self.trade.stop_loss_order:
            try:
                self._cancel_order(self.trade.stop_loss_order)
            except Exception:
                pass
            self.trade.stop_loss_order = None

    def _reconcile_take_profits(self) -> None:
        if not self.trade or not self.trade.take_profit_orders:
            return

        try:
            open_orders = {
                int(order["orderId"])
                for order in self.client.get_open_orders(self.symbol)
            }
        except Exception:
            return

        active_orders: List[ManagedOrder] = []
        for managed in self.trade.take_profit_orders:
            if managed.order_id in open_orders:
                active_orders.append(managed)
                continue

            target_idx = managed.target_index
            if (target_idx is not None and 0 <= target_idx < len(self.trade.tp_targets)):
                self.trade.tp_targets[target_idx] = None

        self.trade.take_profit_orders = active_orders

    def handle_signal(self, signal: Dict[str, Any]) -> None:
        action = signal.get("type")
        if action == "enter":
            self._handle_enter(signal)
        elif action == "update":
            self._handle_update(signal)
        elif action == "exit":
            self._handle_exit()
        elif action in {"cancel_limit_order", "cancel_entry_order", "cancel_stop_order"}:
            self._handle_cancel_entry()
        elif action in {"wait", "hold"}:
            pass
        else:
            raise ValueError(f"Unsupported signal action: {action}")

    def _handle_enter(self, signal: Dict[str, Any]) -> None:
        if self.trade and (self.trade.position_open or self.trade.entry_order):
            raise RuntimeError("Existing trade in progress. Cannot open a new one.")

        side = signal["side"]
        entry = signal.get("entry") or {}
        sl_value = signal.get("sl")
        if sl_value is None:
            raise ValueError("Enter signal missing sl price")

        sl_price = float(sl_value)
        targets = signal.get("tp") or []
        entry_kind = entry.get("kind", "market")
        if entry_kind not in {"market", "limit"}:
            raise ValueError(f"Unsupported entry kind: {entry_kind}")

        if entry_kind == "limit":
            limit_price_value = entry.get("price")
            if limit_price_value is None:
                raise ValueError("Limit entry requires a price")
            price_reference = float(limit_price_value)
        else:
            price_reference = self.client.get_symbol_price(self.symbol)

        quantity = self._compute_order_quantity(price_reference)
        self.trade = ActiveTrade(
            side=side,
            entry_kind=entry_kind,
            quantity=quantity,
            tp_targets=targets,
            sl_price=sl_price,
        )

        if entry_kind == "market":
            managed_order = self._place_market_entry(side, quantity)
            self.trade.position_open = True
            self.trade.entry_price = managed_order.price or price_reference
            self.trade.entry_order = managed_order
            self._setup_post_entry_orders()
        else:  # limit entry
            limit_price_value = entry.get("price")
            if limit_price_value is None:
                raise ValueError("Limit entry requires a price")
            limit_price = float(limit_price_value)
            self.trade.entry_order = self._place_limit_entry(side, quantity, limit_price)
            self.trade.entry_price = limit_price

    def _handle_update(self, signal: Dict[str, Any]) -> None:
        if not self.trade:
            return

        sl_value = signal.get("sl")
        if sl_value is not None:
            new_sl = float(sl_value)
            self.trade.sl_price = new_sl
            if self.trade.stop_loss_order:
                self._cancel_order(self.trade.stop_loss_order)
            if self.trade.position_open:
                self.trade.stop_loss_order = self._place_stop_loss(self.trade.side, new_sl)

        tp_value = signal.get("tp")

        if tp_value is not None:
            for order in list(self.trade.take_profit_orders):
                self._cancel_order(order)
            self.trade.take_profit_orders.clear()
            self.trade.tp_targets = tp_value or []
            if self.trade.position_open:
                self.trade.take_profit_orders = self._place_take_profits(
                    self.trade.side, self.trade.quantity, self.trade.tp_targets
                )

        entry = signal.get("entry")
        if entry and self.trade.entry_order and not self.trade.position_open:
            price_value = entry.get("price")
            kind_value = entry.get("kind") or self.trade.entry_kind
            if kind_value != "limit":
                raise ValueError("Pending entry updates must specify a limit order")
            if price_value is None:
                raise ValueError("Limit entry update requires a price")
            self._cancel_order(self.trade.entry_order)
            self.trade.entry_order = None
            limit_price = float(price_value)
            self.trade.entry_kind = "limit"
            self.trade.entry_order = self._place_limit_entry(
                self.trade.side, self.trade.quantity, limit_price
            )
            self.trade.entry_price = limit_price

    def _handle_exit(self) -> None:
        if not self.trade:
            return
        if self.trade.entry_order and not self.trade.position_open:
            self._cancel_order(self.trade.entry_order)
            self.trade.entry_order = None
        self._close_position()
        self._clear_trade()

    def _handle_cancel_entry(self) -> None:
        if self.trade and self.trade.entry_order and not self.trade.position_open:
            self._cancel_order(self.trade.entry_order)
            self.trade.entry_order = None

    def _close_position(self) -> None:
        position = self.client.get_position_risk(self.symbol)
        qty = float(position["positionAmt"])
        if abs(qty) < 1e-8:
            return

        side = "SELL" if qty > 0 else "BUY"
        params = {
            "symbol": self.symbol,
            "side": side,
            "type": "MARKET",
            "quantity": f"{abs(qty):.8f}",
            "reduceOnly": True,
        }

        order = self.client.place_order(params)
        self._cancel_protective_orders()

    def _setup_post_entry_orders(self) -> None:
        if not self.trade or not self.trade.position_open:
            return
        self.trade.stop_loss_order = self._place_stop_loss(self.trade.side, self.trade.sl_price)
        self.trade.take_profit_orders = self._place_take_profits(
            self.trade.side, self.trade.quantity, self.trade.tp_targets
        )

    def _clear_trade(self) -> None:
        self.trade = None
        self._last_position_size = 0.0

    def build_status_payload(self) -> Dict[str, Dict[str, Optional[Any]]]:
        active_trade_status: Dict[str, Optional[Any]] = {
            "side": None,
            "entryKind": None,
            "entryPrice": None,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
        }

        active_limit_order_status: Dict[str, Optional[Any]] = {
            "side": None,
            "price": None,
        }

        if not self.trade:
            return {
                "activeTradeStatus": active_trade_status,
                "activeLimitOrderStatus": active_limit_order_status,
            }

        if self.trade.position_open:
            active_trade_status["side"] = self.trade.side
            active_trade_status["entryKind"] = self.trade.entry_kind
            active_trade_status["entryPrice"] = self.trade.entry_price
            active_trade_status["sl"] = self.trade.sl_price

            for idx, target in enumerate(self.trade.tp_targets[:3], start=1):
                price_value = target.get("price") if isinstance(target, dict) else None
                active_trade_status[f"tp{idx}"] = (
                    float(price_value) if price_value is not None else None
                )

        entry_order = self.trade.entry_order
        if entry_order and entry_order.order_type == "LIMIT" and not self.trade.position_open:
            active_limit_order_status["side"] = self.trade.side
            active_limit_order_status["price"] = entry_order.price

        return {
            "activeTradeStatus": active_trade_status,
            "activeLimitOrderStatus": active_limit_order_status,
        }

    def sync_state(self) -> None:
        position = self.client.get_position_risk(self.symbol)
        qty = float(position["positionAmt"])
        entry_price = float(position["entryPrice"])
        if not self.trade:
            self._last_position_size = qty
            return

        if self.trade.take_profit_orders:
            self._reconcile_take_profits()

        if abs(qty) < 1e-8:
            if self.trade.stop_loss_order or self.trade.take_profit_orders:
                self._cancel_protective_orders()
            if self.trade.position_open:
                self.trade.position_open = False
            self._clear_trade()
            return

        if not self.trade.position_open:
            self.trade.position_open = True
            self.trade.entry_price = entry_price
            self.trade.quantity = abs(qty)
            if self.trade.entry_order and self.trade.entry_order.order_type == "LIMIT":
                self.trade.entry_order = None
            self._setup_post_entry_orders()
        elif abs(qty) != abs(self._last_position_size):
            self.trade.quantity = abs(qty)

        self._last_position_size = qty

