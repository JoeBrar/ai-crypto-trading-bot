# Binance Intraday Futures Bot

Python service that streams BTCUSDC 5m/15m futures candles to OpenAI's ChatGPT (GPT-5 high thinking) for intraday trade direction and mirrors the returned instructions on Binance Futures using maximum allowable leverage sized against a configurable margin slice.

## Features
- Subscribes to Binance USD-M Futures websocket klines (5m & 15m) for push-based updates instead of REST polling.
- Boots the conversation by sending 1300�5m and 1000�15m historical candles plus buffered trade events so the model starts with rich context.
- Sends only the newly closed candle(s) alongside trade execution deltas after the snapshot, keeping the conversation concise.
- Uses OpenAI's Responses API with a strict JSON schema so GPT always replies with executable trading directives.
- Sizes every trade at `MARGIN_USAGE_PCT` of wallet balance multiplied by the exchange maximum leverage (or an override) while managing SL/TP and reduce-only exits.
- Structured logging, graceful signal handling, and automatic websocket reconnection.

## Project Layout
```
src/
  bot/
    ai_client.py       # OpenAI Responses API bridge
    binance_client.py  # REST wrapper for Binance Futures account/order endpoints
    config.py          # Environment-driven runtime configuration
    data_feed.py       # Websocket-based candle streaming + history management
    trade_manager.py   # State machine that mirrors AI instructions on Binance
    main.py            # Entrypoint/glue code
requirements.txt       # Python dependencies
```

## Prerequisites
- Python 3.10+
- Binance Futures API key with trading permissions
- OpenAI API key with access to the GPT-5 �high thinking� model tier

## Installation
```bash
python -m venv .venv
.\.venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

## Configuration
Set these environment variables (a `.env` file is supported via `python-dotenv`):

| Variable | Description |
|----------|-------------|
| `BINANCE_API_KEY` | Binance Futures API key |
| `BINANCE_API_SECRET` | Binance Futures API secret |
| `OPENAI_API_KEY` | OpenAI API key with GPT-5 access |
| `OPENAI_MODEL` | OpenAI model id (default `gpt-5.1-high`) |
| `AI_INITIAL_INSTRUCTIONS` | System instructions prepended before the first snapshot (placeholder by default) |
| `INITIAL_SNAPSHOT_5M` | Number of historical 5m candles sent on startup (default `1300`) |
| `INITIAL_SNAPSHOT_15M` | Number of historical 15m candles sent on startup (default `1000`) |
| `SYMBOL` | Trading symbol (default `BTCUSDC`) |
| `MARGIN_USAGE_PCT` | Fraction of wallet balance to margin each trade (default `0.10` = 10%) |
| `CANDLE_LIMIT` | Number of candles cached per interval for websocket bootstrap (default `2`) |
| `POLL_INTERVAL_SECONDS` | Max wait before re-syncing fills when no candle arrives (default `1`) |
| `REST_CLIENT_TIMEOUT` | Timeout for REST calls (default `10`) |
| `BINANCE_REST_URL` | Override for Binance REST base URL |
| `BINANCE_USE_TESTNET` | Set to `true` to target Binance Futures testnet |
| `MARGIN_TYPE` | `ISOLATED` or `CROSSED` (default `ISOLATED`) |
| `MAX_SYMBOL_LEVERAGE` | Optional explicit leverage cap; otherwise the exchange maximum is used |
| `OPENAI_BASE_URL` | Optional override for the OpenAI API base URL |
| `NOTIFY_ON_FILL` | Emit fill notifications (default `true`) |

Update `AI_INITIAL_INSTRUCTIONS` with your trading prompt before going live.

## Running The Bot
```bash
python -m bot.main
```

Runtime flow:
1. Configure leverage/margin, fetch historical candles, and deliver the initial snapshot + trade events to GPT-5.
2. Maintain a websocket connection for 5m/15m klines; enqueue updates when candles close.
3. Push only the new candle(s) plus buffered trade updates to ChatGPT and parse the JSON signal.
4. Mirror returned instructions on Binance Futures and log outcomes.

## Safety Notes
- High-leverage futures trading is risky. Shake down the bot against Binance Futures testnet before deploying with real funds.
- Secure your OpenAI credentials and monitor usage costs; the large startup snapshot can be token-intensive.
- Consider augmenting with independent risk checks (balance limits, max daily loss, etc.) before production use.
- Watch Binance API rate limits; although candles are streamed, account/order endpoints still have quotas.

## Next Steps
- Add persistence (database, audit logs) for executed trades and AI directives.
- Implement alerting and retry/backoff strategies for REST/websocket faults.
- Introduce balance-aware throttles (daily loss caps, net exposure limits) for additional safety.
