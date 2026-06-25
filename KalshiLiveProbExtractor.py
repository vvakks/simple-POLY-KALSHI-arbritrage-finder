import requests
import time
import json
import csv
import os
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# A single reused session keeps the TCP connection alive (keep-alive) instead
# of opening a brand new one every second, which is what triggers the server
# to forcibly reset the connection (WinError 10054).
session = requests.Session()
session.headers.update({"User-Agent": "poly-kalshi-arbitrage/1.0"})

# Transparently retry transient connection drops / 5xx responses with a short
# exponential backoff, so a single reset doesn't surface as an error.
retries = Retry(
    total=3,
    backoff_factor=0.3,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",),
)
session.mount("https://", HTTPAdapter(max_retries=retries))

def get_kalshi_buy_percentages(ticker):
    # Kalshi's public REST endpoint for the orderbook
    url = f"https://external-api.kalshi.com/trade-api/v2/markets/{ticker}/orderbook"

    try:
        response = session.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        orderbook = data.get('orderbook_fp', {})

        # The arrays are sorted in ascending order, so the last element [-1]
        # is the highest resting bid on that side.
        # Format is [price_dollars, contract_count].
        yes_bids = orderbook.get('yes_dollars', [])
        best_yes_bid = float(yes_bids[-1][0]) if yes_bids else None

        no_bids = orderbook.get('no_dollars', [])
        best_no_bid = float(no_bids[-1][0]) if no_bids else None

        # The website shows the price to BUY each side (the ask). On Kalshi an
        # ask is the complement of the opposite side's best bid:
        #   buy YES price = 1 - (best NO bid)
        #   buy NO  price = 1 - (best YES bid)
        yes_price = (1.0 - best_no_bid) if best_no_bid is not None else 0.0
        no_price = (1.0 - best_yes_bid) if best_yes_bid is not None else 0.0

        # Convert the dollar price (e.g., 0.65) to a percentage (65.0)
        yes_percent = round(yes_price * 100, 2)
        no_percent = round(no_price * 100, 2)

        return yes_percent, no_percent
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        return None, None
    except (IndexError, ValueError) as e:
        print(f"Error parsing orderbook data: {e}")
        return None, None


# --- Polymarket -------------------------------------------------------------

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


def get_polymarket_market(slug):
    """Resolve a market slug to its (outcomes, token_ids) lists.

    The two lists are aligned by index. Outcome labels are NOT assumed to be
    "Yes"/"No" -- they can be team names, etc. -- so callers should map by
    index. This only needs to be fetched once; the ids never change.
    """
    response = session.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=5)
    response.raise_for_status()
    markets = response.json()
    if not markets:
        raise ValueError(f"No Polymarket market found for slug '{slug}'")

    market = markets[0]
    # Gamma returns these as JSON-encoded strings, aligned by index, e.g.
    # outcomes='["FKS", "SPL"]'  clobTokenIds='["123...", "456..."]'
    outcomes = json.loads(market["outcomes"])
    token_ids = json.loads(market["clobTokenIds"])
    return outcomes, token_ids


def get_polymarket_best_buy(token_id):
    """Best price to BUY a token, as a percentage (e.g. 0.92 -> 92.0).

    The price to buy an outcome is the lowest ASK resting in its order book,
    so we read the book and take min(asks). Reading the book directly avoids
    any ambiguity about what the /price side parameter means.
    """
    response = session.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=5)
    response.raise_for_status()
    asks = response.json().get("asks", [])
    if not asks:
        return None
    best_ask = min(float(level["price"]) for level in asks)
    return round(best_ask * 100, 2)


def get_polymarket_buy_percentages(token_ids):
    try:
        # First outcome maps to the YES column, second to the NO column.
        yes_percent = get_polymarket_best_buy(token_ids[0]) if len(token_ids) > 0 else None
        no_percent = get_polymarket_best_buy(token_ids[1]) if len(token_ids) > 1 else None
        return yes_percent, no_percent
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Polymarket data: {e}")
        return None, None
    except (KeyError, ValueError, TypeError) as e:
        print(f"Error parsing Polymarket data: {e}")
        return None, None


# Replace with the ticker of your target market
market_ticker = "KXVALORANTGAME-26JUN251330FKSPL-PL"

# Replace with the slug of the matching Polymarket market (leave "" to skip).
# e.g. "will-eth-be-above-3000-on-jun-27"
polymarket_slug = "val-pl-fks1-2026-06-25"

# Resolve the Polymarket outcomes + token ids once, up front.
poly_token_ids = None
poly_outcomes = None
if polymarket_slug:
    try:
        poly_outcomes, poly_token_ids = get_polymarket_market(polymarket_slug)
        print(f"Polymarket outcomes (YES, NO) -> {poly_outcomes}")
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        print(f"Could not load Polymarket market '{polymarket_slug}': {e}")

# --- CSV logging ------------------------------------------------------------

LOG_CSV = "prices_log.csv"          # every tick
ARB_CSV = "arb_opportunities.csv"   # only when an arbitrage condition is met
ARB_THRESHOLD = 45.0                # percent

LOG_HEADER = ["timestamp", "kalshi_yes", "kalshi_no", "poly_yes", "poly_no"]
ARB_HEADER = ["timestamp", "opportunity", "kalshi_yes", "kalshi_no",
              "poly_yes", "poly_no", "total_cost"]


def open_csv(path, header):
    """Open a CSV in append mode, writing the header row only if it's new."""
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    f = open(path, "a", newline="")
    writer = csv.writer(f)
    if is_new:
        writer.writerow(header)
        f.flush()
    return f, writer


log_file, log_writer = open_csv(LOG_CSV, LOG_HEADER)
arb_file, arb_writer = open_csv(ARB_CSV, ARB_HEADER)

print(f"Tracking buy percentages for {market_ticker}...")
print(f"Logging every tick to {LOG_CSV}; arbitrage hits to {ARB_CSV}\n")

# 1-second interval loop
try:
    while True:
        k_yes, k_no = get_kalshi_buy_percentages(market_ticker)

        if k_yes is not None:
            line = f"Kalshi  -> YES: {k_yes}% | NO: {k_no}%"

            p_yes, p_no = (None, None)
            if poly_token_ids:
                p_yes, p_no = get_polymarket_buy_percentages(poly_token_ids)
                line += f"   ||   Poly -> YES: {p_yes}% | NO: {p_no}%"

            timestamp = datetime.now().isoformat(timespec="seconds")

            # Log every tick.
            log_writer.writerow([timestamp, k_yes, k_no, p_yes, p_no])
            log_file.flush()

            # Arbitrage A: buy YES on Polymarket + NO on Kalshi, both < threshold.
            if (p_yes is not None and k_no is not None
                    and p_yes < ARB_THRESHOLD and k_no < ARB_THRESHOLD):
                total = round(p_yes + k_no, 2)
                arb_writer.writerow([timestamp, "poly_yes+kalshi_no",
                                     k_yes, k_no, p_yes, p_no, total])
                arb_file.flush()
                line += f"   <== ARB poly_YES+kalshi_NO = {total}%"

            # Arbitrage B: buy NO on Polymarket + YES on Kalshi, both < threshold.
            if (p_no is not None and k_yes is not None
                    and p_no < ARB_THRESHOLD and k_yes < ARB_THRESHOLD):
                total = round(p_no + k_yes, 2)
                arb_writer.writerow([timestamp, "poly_no+kalshi_yes",
                                     k_yes, k_no, p_yes, p_no, total])
                arb_file.flush()
                line += f"   <== ARB poly_NO+kalshi_YES = {total}%"

            print(line)

        # Pause for 1 second before querying again
        time.sleep(1)
finally:
    log_file.close()
    arb_file.close()