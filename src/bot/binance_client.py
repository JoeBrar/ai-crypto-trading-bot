from __future__ import annotations

import hashlib
import hmac
import math
import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("trade_bot.binance")


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


class BinanceFuturesClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._exchange_info_cache: Optional[Dict[str, Any]] = None

    def _timestamp(self) -> int:
        return int(time.time() * 1000)

    def _sign(self, query: str) -> str:
        return hmac.new(self._api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()

    def _prepare_params(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return dict(params or {})

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        request_params = self._prepare_params(params)
        headers = {"X-MBX-APIKEY": self._api_key}
        if signed:
            request_params["timestamp"] = self._timestamp()
            query_string = str(httpx.QueryParams(request_params))
            request_params["signature"] = self._sign(query_string)
        response = self._client.request(method, path, params=request_params, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            redacted_params = dict(request_params)
            redacted_params.pop("signature", None)
            body = exc.response.text if exc.response is not None else "<no response body>"
            status = exc.response.status_code if exc.response is not None else "unknown"
            logger.error(
                "Binance request failed: %s %s status=%s body=%s params=%s",
                method,
                path,
                status,
                body,
                redacted_params,
            )
            raise
        return response.json()

    def ping(self) -> None:
        self._client.get("/fapi/v1/ping").raise_for_status()

    def get_exchange_info(self) -> Dict[str, Any]:
        if self._exchange_info_cache is None:
            self._exchange_info_cache = self._request("GET", "/fapi/v1/exchangeInfo")
        return self._exchange_info_cache

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        info = self.get_exchange_info()
        for sym in info.get("symbols", []):
            if sym.get("symbol") == symbol:
                tick_size = 0.0
                step_size = 0.0
                min_qty = 0.0
                min_notional = 0.0
                for filt in sym.get("filters", []):
                    if filt["filterType"] == "PRICE_FILTER":
                        tick_size = float(filt["tickSize"])
                    elif filt["filterType"] == "LOT_SIZE":
                        step_size = float(filt["stepSize"])
                        min_qty = float(filt["minQty"])
                    elif filt["filterType"] == "MIN_NOTIONAL":
                        min_notional = float(filt.get("notional", 0))
                if not tick_size or not step_size:
                    raise ValueError(f"Symbol {symbol} lacks price or lot size filters")
                return SymbolFilters(
                    tick_size=tick_size,
                    step_size=step_size,
                    min_qty=min_qty,
                    min_notional=min_notional,
                )
        raise ValueError(f"Symbol {symbol} not found in exchange info")

    def get_klines(self, symbol: str, interval: str, limit: int = 150) -> List[List[Any]]:
        params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
        return self._request("GET", "/fapi/v1/klines", params=params)

    def get_symbol_price(self, symbol: str) -> float:
        data = self._request("GET", "/fapi/v1/ticker/price", params={"symbol": symbol})
        return float(data["price"])

    def get_account_information(self) -> Dict[str, Any]:
        return self._request("GET", "/fapi/v2/account", signed=True)

    def get_leverage_brackets(self, symbol: str) -> List[Dict[str, Any]]:
        data = self._request("GET", "/fapi/v1/leverageBracket", params={"symbol": symbol}, signed=True)
        return data[0]["brackets"]

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        params = {"symbol": symbol, "leverage": leverage}
        return self._request("POST", "/fapi/v1/leverage", params=params, signed=True)

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        params = {"symbol": symbol, "marginType": margin_type.upper()}
        try:
            return self._request("POST", "/fapi/v1/marginType", params=params, signed=True)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                return exc.response.json()
            raise

    def place_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/fapi/v1/order", params=params, signed=True)

    def cancel_order(self, symbol: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        elif client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        return self._request("DELETE", "/fapi/v1/order", params=params, signed=True)

    def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        return self._request("GET", "/fapi/v1/openOrders", params={"symbol": symbol}, signed=True)

    def get_position_risk(self, symbol: str) -> Dict[str, Any]:
        positions = self._request("GET", "/fapi/v2/positionRisk", signed=True)
        for pos in positions:
            if pos.get("symbol") == symbol:
                return pos
        raise ValueError(f"No position data returned for {symbol}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BinanceFuturesClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
