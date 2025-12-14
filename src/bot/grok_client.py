import json
import re
import logging
from typing import Any, Dict
from openai import OpenAI


logger = logging.getLogger("trade_bot").getChild("grok_client")

_DEFAULT_BASE_URL = "https://api.x.ai/v1"


def create_session(
    api_key,
    model,
    base_url=None,
    timeout=220,
    instructions="",
    store_messages=True,
):
    base = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    client = OpenAI(api_key=api_key, base_url=base, timeout=timeout)
    return {
        "client": client,
        "model": model,
        "store_messages": bool(store_messages),
        "previous_response_id": None,
        "system_message": instructions.strip(),
    }


def send_initial_snapshot(session, symbol, candles_5m, candles_15m):
    if session.get("previous_response_id"):
        raise RuntimeError("Initial snapshot already sent for this session")
    payload = {
        "phase": "initial_snapshot",
        "symbol": symbol,
        "snapshot": {
            "interval_5m": candles_5m,
            "interval_15m": candles_15m,
        },
    }
    return _dispatch(session, payload, include_system=True)


def request_signal(session, symbol, candles_5m, candles_15m=None, trade_status=None):
    next_candles: Dict[str, Any]
    if candles_15m is None:
        next_candles = {
            "5m": candles_5m or []
        }
    else:
        next_candles = {
            "5m": candles_5m or [],
            "15m": candles_15m
        }

    payload = {
        "nextCandles": next_candles,
        "activeTradeStatus": (trade_status or {}).get("activeTradeStatus", {}),
        "activeLimitOrderStatus": (trade_status or {}).get("activeLimitOrderStatus", {}),
    }
    return _dispatch(session, payload, include_system=False)


def close_session(session):
    client = session.get("client")
    if client:
        try:
            client.close()
        except Exception:
            pass


def _dispatch(session, payload, include_system):
    message_text = json.dumps(payload, ensure_ascii=False)

    messages = []
    system_message = session.get("system_message")
    if include_system and system_message:
        messages.append(_build_text_message("system", system_message))
    messages.append(_build_text_message("user", message_text))

    #for testing -
    # messages=[]
    # if include_system and system_message:
    #     messages.append(_build_text_message("system", system_message))
    # messages.append(_build_text_message("user", "abcdef"))

    request_body = {
        "model": session["model"],
        "input": messages,
        "store": session.get("store_messages", True)
        #"response_format": {"type": "json_object"},
    }
    previous_id = session.get("previous_response_id")
    if previous_id:
        request_body["previous_response_id"] = previous_id

    try:
        logger.info("Sending payload to Grok: %s", request_body)
        response = session["client"].responses.create(**request_body)
    except Exception as exc:
        logger.error("Grok request failed: %s", exc)
        raise

    session["previous_response_id"] = getattr(response, "id", None) or session.get("previous_response_id")
    logger.info("Grok response ID - %s", session["previous_response_id"])
    logger.info("Grok response - %s", response)
    try:
        content_text = _extract_text_content(response)
        json_text = _extract_trailing_json(content_text)
    except ValueError as exc:
        logger.error("Grok response missing text content: %s", _safe_dump_response(response))
        raise

    logger.info("Grok raw text: %s", content_text)
    logger.info("Grok raw json: %s", json_text)

    try:
        return json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.error("Grok returned invalid JSON: %s", content_text)
        raise ValueError("Grok response was not valid JSON") from exc


def _build_text_message(role, text):
    return {
        "role": role,
        "content": text
    }

def _extract_text_content(response):
    text_value = getattr(response, "output_text", None)
    if isinstance(text_value, list):
        text_value = "".join(text_value)
    if isinstance(text_value, str) and text_value.strip():
        return text_value

    if hasattr(response, "model_dump"):
        data = response.model_dump()
    elif hasattr(response, "to_dict"):
        data = response.to_dict()
    elif hasattr(response, "dict"):
        data = response.dict()
    else:
        data = response

    output = data.get("output") or []
    for item in output:
        content_items = item.get("content") or []
        for content_item in content_items:
            if content_item.get("type") == "output_text" and content_item.get("text"):
                return content_item["text"]

    text_fallback = data.get("output_text") or data.get("response_text")
    if isinstance(text_fallback, str) and text_fallback.strip():
        return text_fallback

    raise ValueError("Empty or invalid Grok response content")

def _extract_trailing_json(text):
    if not isinstance(text, str):
        return None
    trimmed = re.sub(r'\s+$', '', text)
    m = re.search(r'[}\]]\s*$', trimmed)
    if not m:
        return None
    end = m.start()
    stack = [trimmed[end]]
    in_string = False
    escaped = False
    i = end - 1
    while i >= 0:
        ch = trimmed[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in '}]':
                stack.append(ch)
            elif ch in '{[':
                expected = '}' if ch == '{' else ']'
                if not stack or stack[-1] != expected:
                    return None
                stack.pop()
                if not stack:
                    s = trimmed[i:end+1]
                    try:
                        json.loads(s)
                        return s
                    except Exception:
                        return None
        i -= 1
    return None


def _safe_dump_response(response):
    if hasattr(response, "model_dump"):
        return json.dumps(response.model_dump(), ensure_ascii=False)[:1500]
    if hasattr(response, "to_dict"):
        return json.dumps(response.to_dict(), ensure_ascii=False)[:1500]
    if hasattr(response, "dict"):
        return json.dumps(response.dict(), ensure_ascii=False)[:1500]
    try:
        return str(response)[:1500]
    except Exception:
        return "<unprintable response>"
