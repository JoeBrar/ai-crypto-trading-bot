from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import grok_client


logger = logging.getLogger("trade_bot").getChild("ai_client")


class AISignalClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 10.0,
        base_url: Optional[str] = None,
        instructions: str = "",
        reasoning_effort: Optional[str] = None,
    ) -> None:
        self._session = grok_client.create_session(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            instructions=instructions,
            store_messages=True,
        )

    def send_initial_snapshot(
        self,
        symbol: str,
        candles_5m: List[Dict[str, Any]],
        candles_15m: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return grok_client.send_initial_snapshot(
            self._session,
            symbol=symbol,
            candles_5m=candles_5m,
            candles_15m=candles_15m,
        )

    def request_signal(
        self,
        symbol: str,
        candles_5m: List[Dict[str, Any]],
        candles_15m: Optional[List[Dict[str, Any]]],
        trade_status: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return grok_client.request_signal(
            self._session,
            symbol=symbol,
            candles_5m=candles_5m,
            candles_15m=candles_15m,
            trade_status=trade_status,
        )

    def close(self) -> None:
        grok_client.close_session(self._session)

    def __enter__(self) -> "AISignalClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


