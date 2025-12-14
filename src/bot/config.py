from dataclasses import dataclass
from typing import Optional
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # Centralized defaults to avoid duplication between dataclass fields and from_env
    DEFAULT_SYMBOL = "BTCUSDC"
    DEFAULT_CANDLE_LIMIT = 2
    DEFAULT_POLL_INTERVAL_SECONDS = 1.0
    DEFAULT_REST_CLIENT_TIMEOUT = 10.0
    DEFAULT_BINANCE_REST_URL = "https://fapi.binance.com"
    DEFAULT_USE_TESTNET = False
    DEFAULT_NOTIFY_ON_FILL = True
    DEFAULT_MARGIN_TYPE = "ISOLATED"
    DEFAULT_MARGIN_USAGE_PCT = 0.10
    DEFAULT_OPENAI_MODEL = "gpt-5"
    DEFAULT_OPENAI_REASONING_EFFORT = "high"
    DEFAULT_AI_INITIAL_INSTRUCTIONS = ''' You are a highly profitable professional intraday trader. I want you to act as my trading assistant and help me trade BTCUSDC perpetual futures. I am an intraday trader aiming to maximize profits within the same day.

I will provide you with historical and live candlestick data for both the 15-minute and 5-minute timeframes (including open, close, high, low, and volume). Based on this data, your task is to analyze candle by candle and respond with precise trading actions.

You must think and reason like a real professional trader — relying on intuition, market knowledge, and technical analysis. Do not suggest bots, scripts, or code. Only use trader logic and strategies, like a real pro trader.

Rules & Requirements

Risk–Reward: Every trade must have a minimum risk–reward ratio of 1:2. This means even for the first take profit target, the risk reward ratio should be atleast 1:2 . So take high quality trades.

One Trade at a Time: Only one active position at once. A new trade is suggested only after the current one is closed.

Strategies: You are free to use any valid trading strategies (price action, trend, S/R, indicators, patterns, etc.).

Only use market or limit order. So the "kind" field in json should be "market" or "limit"

Try to avoid choppy markets and take high quality trades.

We have to minimize loses and maximize profits and preserve capital, so make sure to use the usual strategies like trailing stoploss (especially after tp1 is hit).

JSON Schema
{
  "type": "enter | update | exit | wait | hold",
  "side": "long | short",
  "entry": {
    "kind": "market | limit",  // as we are only using market or limit order
    "price": number            // only for limit order
  },
  "sl": number,       // stop loss
  "tp": [
    { "price": number, "size_pct": number }, // 1–3 objects, sum(size_pct) = 100
    { "price": number, "size_pct": number }
  ]
}

Action Types

enter → Open a new trade (must include side, entry, sl, and tp).

update → Modify existing SL or TP. Only include fields to update.

exit → Close the open position immediately.

wait → Do nothing (no trade is recommended at this point). Use this when there is no active trade and no trade needs to be taken.

hold → Hold the current trade but don't do anything right now. No changes are required.

Examples

Enter -

{
  "type": "enter",
  "side": "short",
  "entry": { "kind": "market" },
  "sl": 116480,
  "tp": [
    { "price": 116050, "size_pct": 60 },
    { "price": 115700, "size_pct": 40 }
  ]
}


Update -

{
  "type": "update",
  "sl": 116350,
  "tp": [{ "price": 115900, "size_pct": 100 }]
}


Exit -

{
  "type": "exit"
}


Wait -

{
  "type": "wait"
}

Hold -

{
  "type": "hold"
}

Workflow

I will feed you new 5m and 15m candles as they form.

You analyze them and give me your response followed by the JSON object. 

You should place the JSON object at the end of your response, so any reasoning/explanation should be before the json object. The json object should be the last thing in your response.

Do not search on twitter or X or other social media. As that data is not reliable.

I will follow your instructions exactly and update you on the current trade status.

Think deeply every time before giving a response.

Keep your responses concise.

Along with the 5min/15min candle data, I will send you 2 other objects - activeTradeStatus and activeLimitOrderStatus, having the following schema

activeTradeStatus : {
    "side": "long" | "short" | null,
    "entryKind": "market" | "limit" | null,
    "entryPrice": float | null,
    "sl": float | null,
    "tp1": float | null,
    "tp2": float | null,
    "tp3": float | null
},
activeLimitOrderStatus : {
    "side": "long" | "short" | null,
    "price": float | null
}

They will let you know the current status of the active order (if any) as well as pending limit order (if any). If there is no active trade/order then all the fields of activeTradeStatus will be null and the same goes for activeLimitOrderStatus. Also the individual fields can also be null for example if for an order the tp1 has been hit but tp2 is still active, then tp1 field will be null, while tp2 will have some value.

'''

    DEFAULT_INITIAL_SNAPSHOT_5M = 1300
    DEFAULT_INITIAL_SNAPSHOT_15M = 1200

    binance_api_key: str
    binance_api_secret: str
    openai_api_key: str
    symbol: str = DEFAULT_SYMBOL
    candle_limit: int = DEFAULT_CANDLE_LIMIT
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    rest_client_timeout: float = DEFAULT_REST_CLIENT_TIMEOUT
    binance_rest_url: str = DEFAULT_BINANCE_REST_URL
    use_testnet: bool = DEFAULT_USE_TESTNET
    notify_on_fill: bool = DEFAULT_NOTIFY_ON_FILL
    max_symbol_leverage: Optional[int] = None
    margin_type: str = DEFAULT_MARGIN_TYPE
    margin_usage_pct: float = DEFAULT_MARGIN_USAGE_PCT
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_reasoning_effort: str = DEFAULT_OPENAI_REASONING_EFFORT
    openai_base_url: Optional[str] = None
    ai_initial_instructions: str = DEFAULT_AI_INITIAL_INSTRUCTIONS
    initial_snapshot_5m: int = DEFAULT_INITIAL_SNAPSHOT_5M
    initial_snapshot_15m: int = DEFAULT_INITIAL_SNAPSHOT_15M

    @classmethod
    def from_env(cls) -> "Settings":
        key = os.environ.get("BINANCE_API_KEY", "")
        secret = os.environ.get("BINANCE_API_SECRET", "")
        if not key or not secret:
            raise ValueError("Binance API credentials are required. Set BINANCE_API_KEY and BINANCE_API_SECRET.")

        openai_key = os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            raise ValueError("Grok or chatGPT API key is required. Set XAI_API_KEY or OPENAI_API_KEY.")

        margin_pct_raw = os.environ.get("MARGIN_USAGE_PCT", str(cls.DEFAULT_MARGIN_USAGE_PCT))
        margin_pct = float(margin_pct_raw)
        if margin_pct <= 0 or margin_pct > 1:
            raise ValueError("MARGIN_USAGE_PCT must be between 0 and 1 (exclusive).")

        initial_5m = int(os.environ.get("INITIAL_SNAPSHOT_5M", str(cls.DEFAULT_INITIAL_SNAPSHOT_5M)))
        initial_15m = int(os.environ.get("INITIAL_SNAPSHOT_15M", str(cls.DEFAULT_INITIAL_SNAPSHOT_15M)))
        if initial_5m <= 0 or initial_15m <= 0:
            raise ValueError("Initial snapshot candle counts must be positive integers.")

        instructions = os.environ.get("AI_INITIAL_INSTRUCTIONS", cls.DEFAULT_AI_INITIAL_INSTRUCTIONS)
        reasoning_effort = (os.environ.get("OPENAI_REASONING_EFFORT") or cls.DEFAULT_OPENAI_REASONING_EFFORT).strip().lower() or cls.DEFAULT_OPENAI_REASONING_EFFORT

        return cls(
            binance_api_key=key,
            binance_api_secret=secret,
            openai_api_key=openai_key,
            symbol=os.environ.get("SYMBOL", cls.DEFAULT_SYMBOL),
            candle_limit=int(os.environ.get("CANDLE_LIMIT", str(cls.DEFAULT_CANDLE_LIMIT))),
            poll_interval_seconds=float(os.environ.get("POLL_INTERVAL_SECONDS", str(cls.DEFAULT_POLL_INTERVAL_SECONDS))),
            rest_client_timeout=float(os.environ.get("REST_CLIENT_TIMEOUT", str(cls.DEFAULT_REST_CLIENT_TIMEOUT))),
            binance_rest_url=os.environ.get("BINANCE_REST_URL", cls.DEFAULT_BINANCE_REST_URL),
            use_testnet=os.environ.get("BINANCE_USE_TESTNET", str(cls.DEFAULT_USE_TESTNET)).lower() == "true",
            notify_on_fill=os.environ.get("NOTIFY_ON_FILL", str(cls.DEFAULT_NOTIFY_ON_FILL)).lower() == "true",
            max_symbol_leverage=int(os.environ.get("MAX_SYMBOL_LEVERAGE", "0")) or None,
            margin_type=os.environ.get("MARGIN_TYPE", cls.DEFAULT_MARGIN_TYPE),
            margin_usage_pct=margin_pct,
            openai_model=os.environ.get("XAI_MODEL") or os.environ.get("OPENAI_MODEL", cls.DEFAULT_OPENAI_MODEL),
            openai_reasoning_effort=reasoning_effort,
            openai_base_url=os.environ.get("XAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or None,
            ai_initial_instructions=instructions,
            initial_snapshot_5m=initial_5m,
            initial_snapshot_15m=initial_15m,
        )
