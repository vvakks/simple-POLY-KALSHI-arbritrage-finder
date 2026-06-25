"""
Polymarket BTC 5-Minute Up/Down — Live Monitor  v5
Polls every 2 seconds and prints a refreshed dashboard.

Usage:
    pip install requests
    python polymarket_btc_5min.py
"""

import time, json, os, requests
from datetime import datetime, timezone

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"
session   = requests.Session()

def current_window_ts():
    now = int(time.time())
    return now - (now % 300)

def build_slug(ts):
    return f"btc-updown-5m-{ts}"

def fetch_event(slug):
    resp = session.get(f"{GAMMA_API}/events/slug/{slug}", timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "markets" in data:
        return data
    if isinstance(data, list) and data:
        return data[0]
    return None

def parse_market(market):
    raw_tokens  = market.get("clobTokenIds", "[]")
    raw_outcomes = market.get("outcomes", '["Up","Down"]')
    raw_prices  = market.get("outcomePrices", '["0.5","0.5"]')
    token_ids   = json.loads(raw_tokens)   if isinstance(raw_tokens, str)   else raw_tokens
    outcomes    = json.loads(raw_outcomes) if isinstance(raw_outcomes, str)  else raw_outcomes
    prices      = [float(p) for p in (json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices)]
    if len(token_ids) < 2:
        raise ValueError(f"Only {len(token_ids)} token IDs found")
    return {
        "token_ids": token_ids,
        "outcomes":  outcomes,
        "prices":    prices,
        "volume":    float(market.get("volume",    0) or 0),
        "liquidity": float(market.get("liquidity", 0) or 0),
        "question":  market.get("question", ""),
    }

def fetch_clob(token_id):
    try:
        r = session.get(f"{CLOB_API}/price",
                         params={"token_id": token_id, "side": "BUY"}, timeout=6)
        return {"BUY": float(r.json()["price"])} if r.ok else {"BUY": None}
    except Exception:
        return {"BUY": None}

def bar(prob, width=20):
    filled = round(prob * width)
    return "█" * filled + "░" * (width - filled)

def clear():
    os.system("cls" if os.name == "nt" else "clear")

def run_once(iteration):
    window_ts = current_window_ts()
    closes_in = 300 - (int(time.time()) % 300)

    event, slug_used = None, None
    for offset in [0, -300, -600, -900, -1200]:
        slug  = build_slug(window_ts + offset)
        event = fetch_event(slug)
        if event:
            slug_used = slug
            break

    lines = []
    lines.append(f"  Poll #{iteration:<4}")
    lines.append(f"  Window closes in: {closes_in}s")

    if not event:
        lines.append("\n  ⚠  No market data found. Retrying next poll…")
        return "\n".join(lines)

    try:
        mkt = parse_market(event["markets"][0])
    except (ValueError, IndexError, KeyError) as e:
        lines.append(f"\n  ⚠  Parse error: {e}")
        return "\n".join(lines)

    outcomes = mkt["outcomes"]
    tids     = mkt["token_ids"]

    clob_data = {}
    for label, tid in zip(outcomes, tids):
        clob_data[label] = fetch_clob(tid)

    lines.append("\n  ── Live CLOB Best Buys ──────────────────────────────────")
    lines.append(f"  {'Outcome':<8} {'Best Buy':>10}")
    lines.append(f"  {'─'*20}")
    for label in outcomes:
        buy = clob_data[label].get("BUY")
        lines.append(f"  {label:<8} {(f'{buy:.2%}' if buy is not None else 'N/A'):>10}")

    lines.append("\n" + "─" * 60)
    return "\n".join(lines)

def main():
    POLL_INTERVAL = 2  # seconds
    iteration     = 1

    print("Starting Polymarket BTC 5-min monitor. Press Ctrl+C to stop.\n")
    time.sleep(1)

    try:
        while True:
            start = time.time()
            clear()
            output = run_once(iteration)
            print(output)
            iteration += 1
            elapsed = time.time() - start
            time.sleep(max(0, POLL_INTERVAL - elapsed))

    except KeyboardInterrupt:
        print("\n\nMonitor stopped.")

if __name__ == "__main__":
    main()