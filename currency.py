import os
import time
import requests
import functools
import logging

logger = logging.getLogger(__name__)

API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")

SUPPORTED = ["USD", "INR", "EUR", "GBP", "CHF", "AED", "JPY", "CAD", "AUD"]

SYMBOLS = {
    "USD": "$",
    "INR": "₹",
    "EUR": "€",
    "GBP": "£",
    "CHF": "CHF ",
    "AED": "AED ",
    "JPY": "¥",
    "CAD": "CA$",
    "AUD": "A$",
}


@functools.lru_cache(maxsize=1)
def _get_rates_cached(hour_bucket: int) -> dict:
    """Fetch rates once per hour — hour_bucket changes every 3600s to bust cache."""
    try:
        r = requests.get(
            f"https://v6.exchangerate-api.com/v6/{API_KEY}/latest/USD",
            timeout=5
        )
        if r.status_code == 200:
            return r.json().get("conversion_rates", {})
        logger.warning("ExchangeRate API returned %s", r.status_code)
    except Exception as e:
        logger.warning("ExchangeRate fetch failed: %s", e)
    return {}


def get_rates() -> dict:
    return _get_rates_cached(int(time.time() // 3600))


def convert_all(usd_amount: float) -> dict:
    """Return a dict of {currency: formatted_string} for all supported currencies."""
    rates = get_rates()
    result = {}
    for cur in SUPPORTED:
        rate = rates.get(cur, 1.0)
        converted = usd_amount * rate
        symbol = SYMBOLS.get(cur, "")
        result[cur] = f"{symbol}{converted:,.0f} {cur}"
    return result


def format_price(usd_amount: float, currency: str) -> str:
    """Return a single formatted price string for the given currency."""
    if currency == "USD" or not usd_amount:
        return f"${usd_amount:,.0f} USD"
    rates = get_rates()
    rate = rates.get(currency, 1.0)
    converted = usd_amount * rate
    symbol = SYMBOLS.get(currency, "")
    return f"{symbol}{converted:,.0f} {currency}"