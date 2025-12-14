from __future__ import annotations

import json
import logging
import signal
import sys
from typing import List, Dict, Any

import httpx

from .ai_client import AISignalClient
from .binance_client import BinanceFuturesClient
from .config import Settings
from .data_feed import CandleFeed
from .models import Candle
from .trade_manager import TradeManager


logger = logging.getLogger("trade_bot")


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def build_binance_client(settings: Settings) -> BinanceFuturesClient:
    base_url = settings.binance_rest_url
    if settings.use_testnet:
        base_url = "https://testnet.binancefuture.com"
    return BinanceFuturesClient(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
        base_url=base_url,
        timeout=settings.rest_client_timeout,
    )


def _klines_to_payload(rows: List[List[Any]]) -> List[Dict[str, Any]]:
    return [Candle.from_kline(row).to_payload() for row in rows]


def run_bot() -> None:
    configure_logging()
    logger.info("Loading settings")
    settings = Settings.from_env()
    logger.info("Connecting to Binance Futures")
    binance_client = build_binance_client(settings)
    ai_client = AISignalClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout=settings.rest_client_timeout,
        base_url=settings.openai_base_url,
        instructions=settings.ai_initial_instructions,
        reasoning_effort=settings.openai_reasoning_effort,
    )
    candle_feed = CandleFeed(
        binance_client,
        settings.symbol,
        settings.candle_limit,
        settings.use_testnet,
    )
    trade_manager = TradeManager(
        client=binance_client,
        symbol=settings.symbol,
        leverage=settings.max_symbol_leverage,
        margin_type=settings.margin_type,
        margin_usage_pct=settings.margin_usage_pct,
    )
    trade_manager.initialize()


    logger.info(
        "Fetching historical context for AI (5m=%s, 15m=%s)",
        settings.initial_snapshot_5m,
        settings.initial_snapshot_15m,
    )
    snapshot_5m_rows = binance_client.get_klines(settings.symbol, "5m", limit=settings.initial_snapshot_5m)
    snapshot_15m_rows = binance_client.get_klines(settings.symbol, "15m", limit=settings.initial_snapshot_15m)
    if not snapshot_5m_rows or not snapshot_15m_rows:
        raise RuntimeError("Failed to retrieve initial candle snapshot for AI")

    snapshot_5m = _klines_to_payload(snapshot_5m_rows)
    snapshot_15m = _klines_to_payload(snapshot_15m_rows)

    logger.info(
        "Sending initial snapshot to AI (5m candles: %s, 15m candles: %s)",
        len(snapshot_5m),
        len(snapshot_15m),
    )
    try:
        initial_response = ai_client.send_initial_snapshot(
            symbol=settings.symbol,
            candles_5m=snapshot_5m,
            candles_15m=snapshot_15m
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text if exc.response is not None else "<no response body>"
        logger.error("Initial AI snapshot failed: %s | body=%s", exc, body)
    except Exception as exc:
        logger.error("Initial AI snapshot failed: %s", exc)
    else:
        logger.info("Initial signal received: %s", json.dumps(initial_response))
        try:
            trade_manager.handle_signal(initial_response)
        except Exception as exc:
            logger.exception("Failed to handle initial signal: %s", exc)


    candle_feed.start()

    should_run = True

    def handle_stop(*_: object) -> None:
        nonlocal should_run
        if should_run:
            should_run = False
            logger.info("Shutdown requested")
            candle_feed.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_stop)

    logger.info("Starting main loop")

    try:
        while should_run:
            try:
                trade_manager.sync_state()
                update = candle_feed.get_updates(timeout=settings.poll_interval_seconds)
                if update is None:
                    continue

                candles_5m, candles_15m = update
                if not candles_5m and not candles_15m:
                    continue

                logger.info(
                    "Dispatching candles to AI (5m: %s, 15m: %s)",
                    bool(candles_5m),
                    bool(candles_15m),
                )

                logger.info("5m candles: %s", candles_5m)
                logger.info("15m candles: %s", candles_15m)

                status_payload = trade_manager.build_status_payload()

                try:
                    response = ai_client.request_signal(
                        symbol=settings.symbol,
                        candles_5m=candles_5m or [],
                        candles_15m=candles_15m,
                        trade_status=status_payload,
                    )
                except Exception as exc:
                    logger.error("AI signal request failed: %s", exc)
                else:
                    logger.info("Signal received: %s", json.dumps(response))
                    try:
                        trade_manager.handle_signal(response)
                    except Exception as exc:
                        logger.exception("Failed to handle signal: %s", exc)
            except KeyboardInterrupt:
                handle_stop()
            except Exception as exc:
                logger.exception("Unexpected error: %s", exc)
    finally:
        logger.info("Shutting down")
        candle_feed.stop()
        ai_client.close()
        binance_client.close()


if __name__ == "__main__":
    run_bot()
