"""Token prices from rates.json, shared by telemetry.py and estimate.py."""
import json
import os
import re

_RATES = None


def rates():
    global _RATES
    if _RATES is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rates.json"), encoding="utf-8") as f:
            _RATES = json.load(f)
    return _RATES


def resolve(model):
    """Model alias or id (with or without a date or [1m] suffix) -> priced model id, or None if unpriced."""
    r = rates()
    m = str(model or "").lower().strip()
    m = r["aliases"].get(m, m)
    m = re.sub(r"\[.*\]$", "", m)
    if m in r["models"]:
        return m
    base = re.sub(r"-\d{8}$", "", m)
    return base if base in r["models"] else None


def price(model, tokens, prompt_tokens=None):
    """USD for one usage row: tokens = {in, out, cacheRead, cacheWrite, cacheWrite1h?}.

    cacheWrite counts all cache writes; cacheWrite1h is the part written for 1 hour (the rest is 5-minute).
    prompt_tokens picks the long-prompt tier where a model has one (pass it per API call when known).
    Returns None when the model has no price in rates.json.
    """
    mid = resolve(model)
    if not mid:
        return None
    p = rates()["models"][mid]
    over = p.get("over")
    if over and prompt_tokens and prompt_tokens > over["promptTokens"]:
        p = over
    cw1h = tokens.get("cacheWrite1h", 0) or 0
    cw5m = max(0, (tokens.get("cacheWrite", 0) or 0) - cw1h)
    usd = ((tokens.get("in", 0) or 0) * p["in"] + cw5m * p["cw5m"] + cw1h * p["cw1h"]
           + (tokens.get("cacheRead", 0) or 0) * p["cr"] + (tokens.get("out", 0) or 0) * p["out"])
    return usd / 1_000_000
