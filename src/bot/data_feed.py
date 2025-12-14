from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Dict, List, Optional, Tuple

import websocket

from .binance_client import BinanceFuturesClient
from .models import Candle


class CandleFeed:
    def __init__(
        self,
        client: BinanceFuturesClient,
        symbol: str,
        candle_limit: int,
        use_testnet: bool,
    ) -> None:
        self.client = client
        self.symbol = symbol.upper()
        self.candle_limit = candle_limit
        self.use_testnet = use_testnet
        self._logger = logging.getLogger("trade_bot.feed")
        self._lock = threading.Lock()
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._ws_app: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._latest_closed: Dict[str, Optional[Candle]] = {"5m": None, "15m": None}
        self._last_sent_open_time: Dict[str, Optional[int]] = {"5m": None, "15m": None}

    def start(self) -> None:
        self._load_initial_state()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="candle-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws_app is not None:
            try:
                self._ws_app.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def get_updates(
        self, timeout: Optional[float] = None
    ) -> Optional[Tuple[Optional[List[Dict[str, float]]], Optional[List[Dict[str, float]]]]]:
        try:
            interval = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

        intervals = {interval}
        while True:
            try:
                intervals.add(self._queue.get_nowait())
            except queue.Empty:
                break

        include_15m = "15m" in intervals
        include_5m = "5m" in intervals or include_15m

        with self._lock:
            payload_5m: Optional[List[Dict[str, float]]] = None
            payload_15m: Optional[List[Dict[str, float]]] = None
            latest_5m = self._latest_closed["5m"]
            latest_15m = self._latest_closed["15m"]

            if include_15m and latest_15m:
                if latest_15m.open_time != self._last_sent_open_time["15m"]:
                    payload_15m = [latest_15m.to_payload()]
                    self._last_sent_open_time["15m"] = latest_15m.open_time

            if include_5m and latest_5m:
                if include_15m:
                    payload_5m = [latest_5m.to_payload()]
                    self._last_sent_open_time["5m"] = latest_5m.open_time
                elif latest_5m.open_time != self._last_sent_open_time["5m"]:
                    payload_5m = [latest_5m.to_payload()]
                    self._last_sent_open_time["5m"] = latest_5m.open_time

        return payload_5m, payload_15m

    def _load_initial_state(self) -> None:
        limit = max(1, min(self.candle_limit, 1500))
        for interval in ("5m", "15m"):
            rows = self.client.get_klines(self.symbol, interval, limit=limit)
            if rows:
                last = Candle.from_kline(rows[-1])
                with self._lock:
                    self._latest_closed[interval] = last
                    self._last_sent_open_time[interval] = last.open_time
        self._logger.info("Loaded initial candle baselines")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            ws_url = self._build_stream_url()
            self._logger.info("Connecting to Binance websocket %s", ws_url)
            self._ws_app = websocket.WebSocketApp(
                ws_url,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            try:
                self._ws_app.run_forever(ping_interval=180, ping_timeout=20)
            except Exception as exc:
                self._logger.error("Websocket run error: %s", exc)
            if not self._stop_event.is_set():
                self._logger.info("Reconnecting websocket after delay")
                self._stop_event.wait(3)

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self._logger.warning("Failed to decode websocket message")
            return
        data = payload.get("data", {})
        kline = data.get("k", {})
        if not kline.get("x"):
            return  # Only emit closed candles
        interval = kline.get("i")
        if interval not in ("5m", "15m"):
            return
        candle = Candle(
            open_time=int(kline["t"]),
            open=float(kline["o"]),
            high=float(kline["h"]),
            low=float(kline["l"]),
            close=float(kline["c"]),
            volume=float(kline["v"]),
            close_time=int(kline["T"]),
        )
        with self._lock:
            previous_open_time = self._latest_closed[interval].open_time if self._latest_closed[interval] else None
            self._latest_closed[interval] = candle
        if previous_open_time != candle.open_time:
            try:
                self._queue.put_nowait(interval)
            except queue.Full:
                self._logger.warning("Candle update queue full; dropping interval %s", interval)

    def _on_error(self, _ws: websocket.WebSocketApp, error: Exception) -> None:
        self._logger.error("Websocket error: %s", error)

    def _on_close(self, _ws: websocket.WebSocketApp, _status_code: int, _msg: str) -> None:
        self._logger.info("Websocket closed")

    def _build_stream_url(self) -> str:
        base = "wss://fstream.binance.com"
        if self.use_testnet:
            base = "wss://fstream.binancefuture.com"
        stream_symbol = self.symbol.lower()
        return f"{base}/stream?streams={stream_symbol}@kline_5m/{stream_symbol}@kline_15m"
