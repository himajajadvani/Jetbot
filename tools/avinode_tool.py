import os
import re
import json
import requests
import concurrent.futures
from langchain.tools import tool

AVINODE_AUTH_TOKEN = os.getenv("AVINODE_AUTH_TOKEN")

# ── Persistent caches (process-level, shared across all requests) ──────────────
_airport_cache: dict = {}   # code/name → airport object
_label_cache:   dict = {}   # airport code → human label string


def headers(json_mode=False):
    h = {"x-avinode-web-app": AVINODE_AUTH_TOKEN}
    if json_mode:
        h["Content-Type"] = "application/json"
        h["x-avinode-currency"] = "USD"
    return h


def _build_label(item: dict, code_upper: str) -> str:
    """Build a full human-readable label from an airport object."""
    name    = (item.get("name") or item.get("airportName") or item.get("fullName") or "").strip()
    city    = (item.get("cityName") or item.get("city") or item.get("municipalityName") or "").strip()
    country = (item.get("countryName") or item.get("country") or item.get("countryIso") or "").strip()
    parts   = [p for p in [name, city, country] if p]
    if parts:
        return ", ".join(parts) + f" ({code_upper})"
    raw = (item.get("label") or item.get("displayName") or "").strip()
    if raw and raw.upper() != code_upper:
        raw = re.sub(r'\s*\([^)]*\)\s*$', '', raw).strip()
        if raw:
            return f"{raw} ({code_upper})"
    return code_upper


def resolve_airport(city: str):
    """Resolve a city name or IATA code to an airport object. Cached."""
    if not city:
        return None
    cache_key = city.lower().strip()
    if cache_key in _airport_cache:
        result = _airport_cache[cache_key]
        iata = result.get("code", "").upper()
        if iata and iata not in _label_cache:
            _label_cache[iata] = _build_label(result, iata)
        return result
    try:
        r = requests.get(f"https://apps.avinode.com/webapp/rest/airport?s={city}", headers=headers(), timeout=8)
        if r.status_code != 200:
            return None
        data = r.json().get("data", [])
        if not data:
            return None
        city_upper = city.strip().upper()
        result = next((d for d in data if d.get("code", "").upper() == city_upper), None)
        if not result:
            result = next((d for d in data if d.get("type") == "AIRPORT" and d.get("cityName", "").lower() == cache_key), None)
        if not result:
            result = next((d for d in data if d.get("type") == "AIRPORT"), None)
        if not result:
            result = data[0]

        _airport_cache[cache_key] = result
        iata = result.get("code", "").upper()
        if iata:
            _airport_cache[iata.lower()] = result
            _label_cache[iata] = _build_label(result, iata)
        return result
    except Exception:
        return None


def get_airport_label(code: str) -> str:
    """Get human-readable airport label. Checks caches first, then API."""
    if not code:
        return code
    code_upper = code.upper()

    if code_upper in _label_cache:
        return _label_cache[code_upper]

    for key in [code.lower(), code_upper]:
        cached = _airport_cache.get(key)
        if cached and cached.get("code", "").upper() == code_upper:
            label = _build_label(cached, code_upper)
            _label_cache[code_upper] = label
            return label

    try:
        r = requests.get(f"https://apps.avinode.com/webapp/rest/airport?s={code_upper}", headers=headers(), timeout=6)
        if r.status_code == 200:
            for item in (r.json().get("data") or []):
                if item.get("code", "").upper() == code_upper:
                    label = _build_label(item, code_upper)
                    _label_cache[code_upper] = label
                    _airport_cache[code.lower()] = item
                    return label
    except Exception:
        pass

    _label_cache[code_upper] = code_upper
    return code_upper


def filter_by_pax(hits: list, pax: int) -> list:
    return [h for h in hits if pax <= h.get("maxPax", 999)]


def clean_hit(hit: dict) -> dict:
    aircraft_name = (hit.get("uniqueName") or "").strip() or "Charter Aircraft"

    raw_price = hit.get("rawPrice") or hit.get("originalRawPrice") or 0
    price_str = f"${raw_price:,.0f} USD" if raw_price else hit.get("price", "N/A")

    min_pax  = hit.get("minPax", 1)
    max_pax  = hit.get("maxPax", "?")
    capacity = f"{min_pax}-{max_pax} passengers"

    segments = hit.get("segments") or []
    dep_label, arr_label, flight_time = "", "", "N/A"
    if segments:
        seg      = segments[0]
        dep_code = seg.get("start", "")
        arr_code = seg.get("end", "")
        dep_human = (seg.get("startAsHumanText") or "").strip()
        arr_human = (seg.get("endAsHumanText") or "").strip()

        def full_label(human, code, seg_airport_obj=None):
            if human and code:
                return f"{human} ({code})"
            if seg_airport_obj and code:
                lbl = _build_label(seg_airport_obj, code.upper())
                if lbl != code.upper():
                    _label_cache[code.upper()] = lbl
                    _airport_cache[code.lower()] = seg_airport_obj
                    return lbl
            if code:
                return get_airport_label(code)
            return "Unknown"

        dep_airport_obj = seg.get("startAirport") or hit.get("startAirport") or {}
        arr_airport_obj = seg.get("endAirport") or hit.get("endAirport") or {}

        dep_label = full_label(dep_human, dep_code, dep_airport_obj)
        arr_label = full_label(arr_human, arr_code, arr_airport_obj)

        raw_ft = seg.get("flightTime") or ""
        if raw_ft:
            parts = raw_ft.split(":")
            if len(parts) == 2:
                h, m = parts
                flight_time = f"{int(h)}h {int(m)}m" if int(m) else f"{int(h)}h"
            else:
                flight_time = raw_ft

    return {
        "aircraft_name":     aircraft_name,
        "capacity":          capacity,
        "price_usd":         price_str,
        "flight_time":       flight_time,
        "departure_airport": dep_label or "Unknown",
        "arrival_airport":   arr_label or "Unknown",
    }


@tool
def search_flights(
    departure_city: str,
    destination_city: str,
    date: str,
    pax: str,
) -> str:
    """
    Search private jet flights via Avinode.
    pax: number of passengers as string e.g. "5"
    """
    try:
        pax_int = int(pax)
    except (ValueError, TypeError):
        return json.dumps({"error": f"Invalid pax value: {pax}"})

    def resolve_with_fallback(city: str):
        result = resolve_airport(city)
        if result:
            return result
        if re.match(r'^[A-Za-z]{3}$', city.strip()):
            return resolve_airport(city.strip().upper())
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_dep  = executor.submit(resolve_with_fallback, departure_city)
        fut_dest = executor.submit(resolve_with_fallback, destination_city)
        dep  = fut_dep.result()
        dest = fut_dest.result()

    if not dep:
        return json.dumps({"error": f"Could not resolve departure airport for '{departure_city}'. Try using the IATA code directly (e.g. HND for Tokyo)."})
    if not dest:
        return json.dumps({"error": f"Could not resolve arrival airport for '{destination_city}'. Try using the IATA code directly (e.g. ICN for Seoul)."})

    payload = {
        "segments": [{
            "startAirportId":          int(dep["id"]),
            "startAirportSearch":      dep["code"],
            "endAirportId":            int(dest["id"]),
            "endAirportSearch":        dest["code"],
            "date":                    date,
            "time":                    "09:00",
            "paxCount":                str(pax_int),
            "numberOfDaysFlexibility": "0"
        }]
    }

    try:
        response = requests.post(
            "https://apps.avinode.com/webapp/rest/search",
            json=payload,
            headers=headers(json_mode=True),
            timeout=15,
        )
    except requests.exceptions.Timeout:
        return json.dumps({"error": "Search timed out. Please try again."})

    if response.status_code != 200:
        return json.dumps({"error": response.text})

    hits = response.json().get("data", {}).get("searchHits", [])
    hits = filter_by_pax(hits, pax_int)

    if not hits:
        return json.dumps({"message": "No aircraft found for this route and passenger count."})

    cleaned = [clean_hit(h) for h in hits]

    dep_label  = _build_label(dep, dep["code"].upper()) if dep.get("code") else ""
    dest_label = _build_label(dest, dest["code"].upper()) if dest.get("code") else ""
    for ac in cleaned:
        if dep_label and (not ac["departure_airport"] or ac["departure_airport"] == dep.get("code", "")):
            ac["departure_airport"] = dep_label
        if dest_label and (not ac["arrival_airport"] or ac["arrival_airport"] == dest.get("code", "")):
            ac["arrival_airport"] = dest_label

    return json.dumps({
        "total_results": len(cleaned),
        "aircraft":      cleaned,
    }, indent=2)