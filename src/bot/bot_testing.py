from __future__ import annotations

import json
import math
import sys
import types
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:  # Allow running without installing httpx (not needed for the mock client)
    import httpx  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - only used when dependency missing
    class _HTTPStatusError(Exception):
        def __init__(self, *args: Any, response: Optional[Any] = None, request: Optional[Any] = None) -> None:
            super().__init__(*args)
            self.response = response
            self.request = request

    class _StubQueryParams(dict):
        def __str__(self) -> str:  # pragma: no cover - simple fallback behaviour
            return ""

    class _StubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("httpx.Client is stubbed out in bot_testing")

        get = post = request

        def close(self) -> None:
            pass

    httpx = types.SimpleNamespace(Client=_StubClient, QueryParams=_StubQueryParams, HTTPStatusError=_HTTPStatusError)
    sys.modules["httpx"] = httpx

from .trade_manager import TradeManager


@dataclass
class SymbolFilters:
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float

    def normalize_price(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        steps = math.floor(price / self.tick_size)
        return round(steps * self.tick_size, 8)

    def normalize_qty(self, qty: float) -> float:
        if self.step_size <= 0:
            return qty
        steps = math.floor(qty / self.step_size)
        return round(steps * self.step_size, 8)


class MockBinanceFuturesClient:
    """In-memory stand-in for the Binance client so tests can run without network calls."""

    def __init__(self, symbol: str, mark_price: float = 68000.0) -> None:
        self.symbol = symbol
        self._mark_price = mark_price
        self._position_qty = 0.0
        self._entry_price = mark_price
        self._order_id = 0
        self.margin_type: Optional[str] = None
        self.leverage: Optional[int] = None
        self.order_log: List[Dict[str, Any]] = []
        self._open_orders: Dict[int, Dict[str, Any]] = {}
        self._filters = SymbolFilters(
            tick_size=0.1,
            step_size=0.001,
            min_qty=0.001,
            min_notional=5.0,
        )

    # --- Binance REST compatibility layer -------------------------------------------------
    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        if symbol != self.symbol:
            raise ValueError(f"Mock client configured for {self.symbol} but asked for {symbol}")
        return self._filters

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        self.margin_type = margin_type.upper()
        return {"symbol": symbol, "marginType": self.margin_type}

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        self.leverage = leverage
        return {"symbol": symbol, "leverage": leverage}

    def get_leverage_brackets(self, symbol: str) -> List[Dict[str, Any]]:
        # Simple single-bracket response good enough for testing
        return [{"initialLeverage": self.leverage or 20}]

    def get_account_information(self) -> Dict[str, Any]:
        balance = 1000.0
        return {"totalWalletBalance": str(balance), "availableBalance": str(balance)}

    def get_symbol_price(self, symbol: str) -> float:
        return self._mark_price

    def get_position_risk(self, symbol: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "positionAmt": f"{self._position_qty:.8f}",
            "entryPrice": f"{self._entry_price:.2f}",
        }

    def place_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._order_id += 1
        symbol = params.get("symbol", self.symbol)
        order_type = params.get("type", "MARKET")
        side = params.get("side", "BUY")
        qty_raw = params.get("quantity") or params.get("origQty") or "0"
        qty = float(qty_raw)
        reduce_only = bool(params.get("reduceOnly"))
        close_position = bool(params.get("closePosition"))
        price_value = float(params.get("price") or params.get("stopPrice") or self._mark_price)

        if order_type == "MARKET":
            if reduce_only:
                if side == "SELL":
                    self._position_qty -= qty
                else:
                    self._position_qty += qty
                if abs(self._position_qty) < 1e-8:
                    self._position_qty = 0.0
            else:
                if side == "BUY":
                    self._position_qty += qty
                else:
                    self._position_qty -= qty
                self._entry_price = price_value
        elif order_type == "LIMIT":
            # Pending limit orders reserve quantity but do not change the position immediately
            pass
        elif order_type == "TAKE_PROFIT_MARKET" and reduce_only:
            # TP orders reserve quantity but do not change position until triggered
            pass
        elif order_type == "STOP_MARKET" and close_position:
            # Stop market orders are protective; no immediate position change
            pass

        status = "FILLED" if order_type == "MARKET" else "NEW"
        executed_qty = qty if status == "FILLED" and not close_position else 0.0
        response = {
            "orderId": self._order_id,
            "clientOrderId": f"mock_{self._order_id}",
            "status": status,
            "type": order_type,
            "side": side,
            "executedQty": f"{executed_qty:.8f}",
            "avgPrice": f"{price_value:.2f}",
            "price": f"{price_value:.2f}",
        }
        self.order_log.append({"request": dict(params), "response": dict(response)})
        if order_type != "MARKET":
            self._open_orders[self._order_id] = {
                "symbol": symbol,
                "orderId": self._order_id,
                "clientOrderId": response["clientOrderId"],
                "type": order_type,
                "side": side,
                "status": status,
            }
        return response

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if order_id is not None:
            self._open_orders.pop(order_id, None)
        elif client_order_id is not None:
            for key, open_order in list(self._open_orders.items()):
                if open_order.get("clientOrderId") == client_order_id:
                    self._open_orders.pop(key, None)
                    break
        return {
            "symbol": symbol,
            "orderId": order_id or -1,
            "clientOrderId": client_order_id or "",
            "status": "CANCELED",
        }

    def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        if symbol != self.symbol:
            return []
        return list(self._open_orders.values())

    # --- Helpers --------------------------------------------------------------------------
    def set_mark_price(self, price: float) -> None:
        self._mark_price = price


def generate_mock_ai_signals(mark_price: float) -> List[Dict[str, Any]]:
    enter_signal = {
        "type": "enter",
        "side": "long",
        "entry": {"kind": "market"},
        "sl": round(mark_price * 0.99, 2),
        "tp": [
            {"price": round(mark_price * 1.01, 2), "size_pct": 60},
            {"price": round(mark_price * 1.02, 2), "size_pct": 40},
        ],
    }
    update_signal = {
        "type": "update",
        "sl": round(mark_price * 0.995, 2),
        "tp": [
            {"price": round(mark_price * 1.0125, 2), "size_pct": 50},
            {"price": round(mark_price * 1.025, 2), "size_pct": 50},
        ],
    }
    exit_signal = {"type": "exit"}
    return [enter_signal, update_signal, exit_signal]


def replay_mock_ai_flow() -> None:
    symbol = "BTCUSDC"
    mock_price = 68000.0
    client = MockBinanceFuturesClient(symbol=symbol, mark_price=mock_price)
    manager = TradeManager(
        client=client,
        symbol=symbol,
        leverage=20,
        margin_type="ISOLATED",
        margin_usage_pct=0.1,
    )
    manager.initialize()

    print(f"Running mock AI flow for {symbol}\n")
    for step, payload in enumerate(generate_mock_ai_signals(mock_price), start=1):
        if step == 2:
            client.set_mark_price(client.get_symbol_price(symbol) * 1.001)
        print(f"--- Signal {step}: {payload['type']} ---")
        print(json.dumps(payload, indent=2))
        manager.handle_signal(payload)
        manager.sync_state()

    print("Final position snapshot:")
    print(json.dumps(client.get_position_risk(symbol), indent=2))
    print("\nOrders placed:")
    print(json.dumps(client.order_log, indent=2))


if __name__ == "__main__":
    replay_mock_ai_flow()
