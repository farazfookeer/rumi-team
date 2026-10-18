#!/usr/bin/env python3
"""Estimate a run's tokens and API-equivalent cost before building, and watch for overruns.

Rumi sizes each requirement (S/M/L/XL) and picks models; this script does the arithmetic,
so estimates are reproducible and their assumptions visible.

  estimate.py make    --plan PLAN.json            estimate the plan; saves it into the plan file and prints it
  estimate.py check   --plan PLAN.json --usage U  record spend from a telemetry snapshot; reports overrun (>= overrunAt x expected)
  estimate.py approve --plan PLAN.json --remaining R-4,R-5
                                                  after the user says "continue": new expected = spent so far + estimate for what's left
  estimate.py record  --plan PLAN.json --usage U  at the end of a run: save actuals to ~/.claude/rumi-team/history.jsonl

PLAN.json (written by Rumi):
  {"project": "decidekit", "pmModel": "opus", "analystModel": "opus", "finalRegression": true,
   "cards": [{"id": "R-1", "title": "...", "size": "M", "devModel": "sonnet", "qaModel": "sonnet"}]}
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pricing import price, rates, resolve  # noqa: E402

HISTORY = os.path.expanduser(os.environ.get("RUMI_HISTORY", "~/.claude/rumi-team/history.jsonl"))
OVERRUN_AT = 1.5

# Starting profile, measured on a real 9-requirement run (decidekit, Oct 2026): tokens processed per agent run
# of a medium (M) requirement, and the share of each token type. Replaced by your own history once it exists.
PROFILE = {
    "dev":     {"tokens": 1_150_000, "runs": 1.4, "mix": {"cacheRead": 0.942, "cacheWrite": 0.050, "out": 0.008}, "cache": "5m"},
    "qa":      {"tokens":   940_000, "runs": 1.2, "mix": {"cacheRead": 0.934, "cacheWrite": 0.060, "out": 0.006}, "cache": "5m"},
    "analyst": {"tokens":   300_000, "runs": 1.0, "mix": {"cacheRead": 0.900, "cacheWrite": 0.080, "out": 0.020}, "cache": "5m"},
    # Rumi's own coordination: re-reading its growing conversation dominates, per requirement plus a fixed planning cost.
    "pm":      {"perCard": 6_000_000, "fixed": 2_000_000, "mix": {"cacheRead": 0.9825, "cacheWrite": 0.0149, "out": 0.0026}, "cache": "1h"},
    "regression": {"tokens": 1_200_000, "mix": {"cacheRead": 0.934, "cacheWrite": 0.060, "out": 0.006}, "cache": "5m"},
}
SIZE = {"S": 0.5, "M": 1.0, "L": 2.0, "XL": 3.5}
PM_SIZE = {"S": 0.75, "M": 1.0, "L": 1.25, "XL": 1.5}
SPREAD = (0.6, 1.8)  # low and high as multiples of expected, until history gives a measured spread


def tokens_by_type(total, mix, cache):
    t = {k: total * v for k, v in mix.items()}
    t["in"] = 0
    t["cacheWrite1h"] = t["cacheWrite"] if cache == "1h" else 0
    return t


def cost_of(total, mix, cache, model):
    return price(model, tokens_by_type(total, mix, cache)) or 0.0


def rng(expected, spread):
    return [round(expected * spread[0]), round(expected), round(expected * spread[1])]


def money(expected_usd, spread):
    return [round(expected_usd * spread[0], 2), round(expected_usd, 2), round(expected_usd * spread[1], 2)]


def load_history():
    rows = []
    try:
        with open(HISTORY, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        pass
    return rows


def calibration(history):
    """Per-size medians of real tokens per requirement (developer + QA), and Rumi's tokens per requirement."""
    cards = [r for r in history if r.get("type") == "card" and r.get("tokens")]
    by_size = {}
    for s in SIZE:
        vals = sorted(r["tokens"] for r in cards if r.get("size") == s)
        if len(vals) >= 3:
            q = statistics.quantiles(vals, n=5) if len(vals) >= 5 else [vals[0], None, None, vals[-1]]
            med = statistics.median(vals)
            by_size[s] = {"median": med, "n": len(vals),
                          "spread": (max(0.3, (q[0] or vals[0]) / med), max(1.2, (q[-1] or vals[-1]) / med))}
    runs = [r for r in history if r.get("type") == "run" and r.get("pmTokensPerCard")]
    pm = statistics.median(r["pmTokensPerCard"] for r in runs) if len(runs) >= 2 else None
    return by_size, pm, len(cards)


def estimate_cards(cards, by_size):
    out = []
    for c in cards:
        size = (c.get("size") or "M").upper()
        f = SIZE.get(size, 1.0)
        dev_t = PROFILE["dev"]["tokens"] * PROFILE["dev"]["runs"] * f
        qa_t = PROFILE["qa"]["tokens"] * PROFILE["qa"]["runs"] * f
        spread = SPREAD
        if size in by_size:  # your own history for this size replaces the starting profile
            scale = by_size[size]["median"] / (dev_t + qa_t)
            dev_t, qa_t, spread = dev_t * scale, qa_t * scale, by_size[size]["spread"]
        dev_m, qa_m = c.get("devModel") or "sonnet", c.get("qaModel") or "sonnet"
        usd = (cost_of(dev_t, PROFILE["dev"]["mix"], "5m", dev_m) + cost_of(qa_t, PROFILE["qa"]["mix"], "5m", qa_m))
        out.append({"id": c["id"], "title": c.get("title", ""), "size": size, "devModel": dev_m, "qaModel": qa_m,
                    "tokens": rng(dev_t + qa_t, spread), "cost": money(usd, spread)})
    return out


def make(plan):
    history = load_history()
    by_size, pm_hist, n_hist = calibration(history)
    cards = estimate_cards(plan.get("cards", []), by_size)

    pm_model = plan.get("pmModel") or "opus"
    pm_per = pm_hist or PROFILE["pm"]["perCard"]
    pm_t = PROFILE["pm"]["fixed"] + sum(pm_per * PM_SIZE.get(c["size"], 1.0) for c in cards)
    pm = {"model": pm_model, "tokens": rng(pm_t, SPREAD), "cost": money(cost_of(pm_t, PROFILE["pm"]["mix"], "1h", pm_model), SPREAD)}

    an_model = plan.get("analystModel") or "opus"
    an_t = PROFILE["analyst"]["tokens"]
    analyst = {"model": an_model, "tokens": rng(an_t, SPREAD), "cost": money(cost_of(an_t, PROFILE["analyst"]["mix"], "5m", an_model), SPREAD)}

    parts = cards + [pm, analyst]
    if plan.get("finalRegression", True):
        rg_t = PROFILE["regression"]["tokens"]
        reg_model = (cards[0]["qaModel"] if cards else "sonnet")
        regression = {"model": reg_model, "tokens": rng(rg_t, SPREAD), "cost": money(cost_of(rg_t, PROFILE["regression"]["mix"], "5m", reg_model), SPREAD)}
        parts.append(regression)
    else:
        regression = None

    total = {"tokens": [sum(p["tokens"][i] for p in parts) for i in range(3)],
             "cost": [round(sum(p["cost"][i] for p in parts), 2) for i in range(3)]}
    unpriced = sorted({m for m in [pm_model, an_model] + [c["devModel"] for c in cards] + [c["qaModel"] for c in cards] if not resolve(m)})
    r = rates()
    basis = (f"your history ({n_hist} past requirements) for sizes {', '.join(sorted(by_size))}" if by_size
             else "starting profile measured on a real 9-requirement run; it adjusts to your own runs after a few")
    return {
        "currency": "USD", "apiEquivalent": True, "ratesChecked": r["checked"], "ratesSource": r["source"],
        "basis": basis, "overrunAt": OVERRUN_AT,
        "cards": cards, "pm": pm, "analyst": analyst, "regression": regression, "total": total,
        "unpricedModels": unpriced,
        "assumptions": [
            "Sizes: S = half a typical requirement, M = typical, L = double, XL = 3.5x.",
            "Each requirement includes its developer run, its QA pass and an allowance for rework.",
            f"Rumi's own coordination ({pm_model}) is included; it's usually the largest single cost because it re-reads the whole run.",
            "Most tokens are cache reads, which are the cheapest kind.",
            "Low and high show the likely range. Costs are API list prices; on a Pro or Max plan this counts against usage limits instead.",
        ],
    }


def read_json(path):
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(os.path.expanduser(path), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "check", "approve", "record"])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--usage")
    ap.add_argument("--remaining", default="")
    a = ap.parse_args()
    plan = read_json(a.plan)

    if a.cmd == "make":
        est = make(plan)
        plan["estimate"] = est
        plan["expected"] = est["total"]["cost"][1]
        plan.setdefault("sessions", {})
        write_json(a.plan, plan)
        print(json.dumps(est, separators=(",", ":")))
        return

    usage = read_json(a.usage) if a.usage else {}
    if usage.get("session"):
        plan.setdefault("sessions", {})[usage["session"]] = usage.get("cost", 0)
    spent = round(sum(plan.get("sessions", {}).values()), 2)

    if a.cmd == "check":
        expected = plan.get("expected") or plan["estimate"]["total"]["cost"][1]
        threshold = round(expected * plan.get("estimate", {}).get("overrunAt", OVERRUN_AT), 2)
        write_json(a.plan, plan)
        print(json.dumps({"spent": spent, "expected": expected, "threshold": threshold,
                          "ratio": round(spent / expected, 2) if expected else None,
                          "overrun": bool(expected) and spent >= threshold}))
    elif a.cmd == "approve":
        left = {x.strip() for x in a.remaining.split(",") if x.strip()}
        rest = make({**plan, "cards": [c for c in plan.get("cards", []) if c["id"] in left]})
        plan["expected"] = round(spent + rest["total"]["cost"][1] - rest["analyst"]["cost"][1] - PROFILE_PM_FIXED_COST(plan), 2)
        plan.setdefault("approvals", []).append({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "spent": spent, "newExpected": plan["expected"]})
        write_json(a.plan, plan)
        print(json.dumps({"spent": spent, "expected": plan["expected"], "threshold": round(plan["expected"] * OVERRUN_AT, 2)}))
    elif a.cmd == "record":
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        by_card, by_card_cost = usage.get("byCard", {}), usage.get("byCardCost", {})
        pm = (usage.get("byAgent", {}) or {}).get("pm", {})
        pm_tokens = sum(pm.get(k, 0) for k in ("in", "out", "cacheRead", "cacheWrite"))
        n = 0
        with open(HISTORY, "a", encoding="utf-8") as f:
            for c in plan.get("cards", []):
                if c["id"] in by_card:
                    f.write(json.dumps({"type": "card", "date": now, "project": plan.get("project"), "id": c["id"],
                                        "size": c.get("size"), "devModel": c.get("devModel"), "qaModel": c.get("qaModel"),
                                        "tokens": by_card[c["id"]], "cost": by_card_cost.get(c["id"])}) + "\n")
                    n += 1
            if n:
                f.write(json.dumps({"type": "run", "date": now, "project": plan.get("project"), "cards": n,
                                    "pmTokensPerCard": round(pm_tokens / n), "cost": spent,
                                    "estimated": plan.get("estimate", {}).get("total", {}).get("cost")}) + "\n")
        print(json.dumps({"recorded": n, "spent": spent, "estimated": plan.get("estimate", {}).get("total", {}).get("cost")}))


def PROFILE_PM_FIXED_COST(plan):
    """The planning part of Rumi's cost is already spent when re-estimating the remainder."""
    return round(cost_of(PROFILE["pm"]["fixed"], PROFILE["pm"]["mix"], "1h", plan.get("pmModel") or "opus"), 2)


if __name__ == "__main__":
    main()
