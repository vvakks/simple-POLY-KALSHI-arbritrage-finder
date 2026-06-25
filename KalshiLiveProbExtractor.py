import requests
import time
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

# Replace with the ticker of your target market
market_ticker = "KXCS2GAME-26JUN251200UNOKEYD-UNO" 

print(f"Tracking buy percentages for {market_ticker}...\n")

# 1-second interval loop
while True:
    yes_pct, no_pct = get_kalshi_buy_percentages(market_ticker)
    
    if yes_pct is not None:
        print(f"YES Buy Percentage: {yes_pct}% | NO Buy Percentage: {no_pct}%")
    
    # Pause for 1 second before querying again
    time.sleep(1)