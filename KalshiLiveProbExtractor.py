import requests
import time
import json
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


def get_polymarket_tokens(slug):
    """Resolve a market slug to its outcome -> CLOB token id mapping.

    This only needs to be fetched once; the token ids never change for a
    given market.
    """
    response = session.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=5)
    response.raise_for_status()
    markets = response.json()
    if not markets:
        raise ValueError(f"No Polymarket market found for slug '{slug}'")

    market = markets[0]
    # Gamma returns these as JSON-encoded strings, aligned by index, e.g.
    # outcomes='["Yes", "No"]'  clobTokenIds='["123...", "456..."]'
    outcomes = json.loads(market["outcomes"])
    token_ids = json.loads(market["clobTokenIds"])
    return dict(zip(outcomes, token_ids))


def get_polymarket_best_buy(token_id):
    """Best price to BUY a token = the lowest ask in its order book.

    Returns the price as a percentage (e.g. 0.92 -> 92.0), or None if the
    book has no asks.
    """
    response = session.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=5)
    response.raise_for_status()
    asks = response.json().get("asks", [])
    if not asks:
        return None
    # Use min() so we don't depend on the book's sort order.
    best_ask = min(float(level["price"]) for level in asks)
    return round(best_ask * 100, 2)


def get_polymarket_buy_percentages(tokens):
    try:
        yes_percent = get_polymarket_best_buy(tokens["Yes"]) if "Yes" in tokens else None
        no_percent = get_polymarket_best_buy(tokens["No"]) if "No" in tokens else None
        return yes_percent, no_percent
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Polymarket data: {e}")
        return None, None
    except (KeyError, ValueError) as e:
        print(f"Error parsing Polymarket data: {e}")
        return None, None


# Replace with the ticker of your target market
market_ticker = "KXVALORANTGAME-26JUN251330FKSPL-FKS"

# Replace with the slug of the matching Polymarket market (leave "" to skip).
# e.g. "will-eth-be-above-3000-on-jun-27"
polymarket_slug = "val-pl-fks1-2026-06-25"

# Resolve the Polymarket token ids once, up front.
poly_tokens = None
if polymarket_slug:
    try:
        poly_tokens = get_polymarket_tokens(polymarket_slug)
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        print(f"Could not load Polymarket market '{polymarket_slug}': {e}")

print(f"Tracking buy percentages for {market_ticker}...\n")

# 1-second interval loop
while True:
    k_yes, k_no = get_kalshi_buy_percentages(market_ticker)

    if k_yes is not None:
        line = f"Kalshi  -> YES: {k_yes}% | NO: {k_no}%"

        if poly_tokens:
            p_yes, p_no = get_polymarket_buy_percentages(poly_tokens)
            line += f"   ||   Poly -> YES: {p_yes}% | NO: {p_no}%"

        print(line)

    # Pause for 1 second before querying again
    time.sleep(1)