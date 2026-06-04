import os
import re
import json
import requests
from langchain.tools import tool
from typing import Union
from currency import convert_all

AVINODE_AUTH_TOKEN = os.getenv("AVINODE_AUTH_TOKEN")

# ── Persistent caches ──────────────────────────────────────────────────────────
_airport_cache: dict = {}
_label_cache:   dict = {}


def _coerce_bool(value) -> bool:
    if isinstance(value, bool): return value
    if isinstance(value, str):  return value.strip().lower() in ("true", "1", "yes")
    if isinstance(value, int):  return value != 0
    return False


def headers(json_mode=False):
    h = {"x-avinode-web-app": AVINODE_AUTH_TOKEN}
    if json_mode:
        h["Content-Type"] = "application/json"
        h["x-avinode-currency"] = "USD"
    return h


def _build_label(item: dict, code_upper: str) -> str:
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


def resolve_airports(city: str) -> list:
    """Return Avinode's airport results for a city, trusting Avinode's own ordering."""
    if not city: return []
    cache_key = f"__list__{city.lower().strip()}"
    if cache_key in _airport_cache:
        return _airport_cache[cache_key]
    try:
        r = requests.get(
            f"https://apps.avinode.com/webapp/rest/airport?s={city}",
            headers=headers(), timeout=8
        )
        if r.status_code != 200: return []
        data = r.json().get("data", [])
        usable = [d for d in data if d.get("code") and d.get("id")]
        # Sort by quality score so index 0 is always the best airport, not Avinode's arbitrary order
        usable = sorted(usable, key=_airport_score)
        for item in usable:
            code = (item.get("code") or "").strip().upper()
            if code:
                _airport_cache[code.lower()] = item
                _label_cache[code] = _build_label(item, code)
        _airport_cache[cache_key] = usable
        return usable
    except Exception:
        return []


_RESORT_NOISE = re.compile(
    r'\b(resort|lodge|ranch|estate|golf|casino|spa|park|camp|'
    r'private|helipad|heliport|strip|ultralight|seaplane|float)\b',
    re.IGNORECASE
)

_MAJOR_KEYWORDS = re.compile(
    r'\b(international|intl|national|charles|de gaulle|heathrow|gatwick|'
    r'schiphol|frankfurt|orly|cointrin|linate|malpensa|fiumicino|barajas|'
    r'zurich|geneva|milan|rome|madrid|dubai|doha|changi|narita|haneda)\b',
    re.IGNORECASE
)

_IATA_RE = re.compile(r'^[A-Z]{3}$')


def _airport_score(item: dict) -> int:
    """Lower score = more preferred. Penalise resort/private strips; reward major international airports."""
    score = 0
    name    = (item.get("name") or item.get("airportName") or item.get("fullName") or "").strip()
    city    = (item.get("cityName") or item.get("city") or "").strip()
    country = (item.get("countryName") or item.get("country") or "").strip()
    code    = (item.get("code") or "").strip().upper()
    full    = f"{name} {city} {country}"

    if _RESORT_NOISE.search(full):
        score += 50                         # heavy penalty for resorts / private strips
    if _MAJOR_KEYWORDS.search(full):
        score -= 20                         # reward for known major hubs
    if _IATA_RE.match(code):
        score -= 10                         # standard 3-letter IATA = real commercial airport
    elif len(code) == 4:
        score += 5                          # 4-letter ICAO-only = smaller/private field
    if country.lower() in ("united states", "us", "usa"):
        score += 2                          # mild preference for international over US domestic

    return score


def resolve_airport(city: str):
    """Return the best Avinode airport result for a city (prefers major intl over resorts)."""
    if not city: return None
    cache_key = city.lower().strip()
    if cache_key in _airport_cache and not cache_key.startswith("__list__"):
        return _airport_cache[cache_key]
    results = resolve_airports(city)
    if not results:
        return None
    ranked = sorted(results, key=_airport_score)
    best = ranked[0]
    _airport_cache[cache_key] = best
    return best


def get_airport_label(code: str) -> str:
    if not code: return code
    code_upper = code.strip().upper()
    if code_upper in _label_cache:
        return _label_cache[code_upper]
    for key in [code.lower(), code_upper]:
        cached = _airport_cache.get(key)
        if cached and (cached.get("code") or "").upper() == code_upper:
            label = _build_label(cached, code_upper)
            _label_cache[code_upper] = label
            return label
    try:
        r = requests.get(
            f"https://apps.avinode.com/webapp/rest/airport?s={code_upper}",
            headers=headers(), timeout=6
        )
        if r.status_code == 200:
            for item in (r.json().get("data") or []):
                if (item.get("code") or "").upper() == code_upper:
                    label = _build_label(item, code_upper)
                    _label_cache[code_upper] = label
                    _airport_cache[code.lower()] = item
                    return label
    except Exception:
        pass
    _label_cache[code_upper] = code_upper
    return code_upper


def filter_by_pax(hits: list, pax: int) -> list:
    # Tier 1 — ideal fit: not oversized (maxPax between pax and pax×2)
    ideal    = [h for h in hits if pax <= h.get("maxPax", 999) <= pax * 2]
    # Tier 2 — relaxed: slightly larger jets (maxPax between pax×2 and pax×3)
    relaxed  = [h for h in hits if pax * 2 < h.get("maxPax", 999) <= pax * 3]
    # Tier 3 — fallback: anything that physically fits, no upper cap
    fallback = [h for h in hits if h.get("maxPax", 999) > pax * 3 and pax <= h.get("maxPax", 999)]

    # Combine in priority order: best-fit first, oversized last
    # This ensures perfectly sized jets are never discarded in favour of bigger ones
    combined = ideal + relaxed + fallback
    return combined


def _is_turboprop(hit: dict) -> bool:
    for field in ("aircraftCategory", "category", "aircraftType", "type",
                  "categoryName", "typeName", "aircraftClass", "className"):
        val = (hit.get(field) or "").lower()
        if val and any(kw in val for kw in ("turbo", "turboprop", "prop")):
            return True
    for obj_key in ("aircraft", "aircraftInfo", "aircraftDetails"):
        for field in ("category", "type", "aircraftCategory", "aircraftType", "className"):
            val = ((hit.get(obj_key) or {}).get(field) or "").lower()
            if val and any(kw in val for kw in ("turbo", "turboprop", "prop")):
                return True
    name = (hit.get("uniqueName") or hit.get("aircraftName") or "").lower()
    return bool(re.search(
        r'\bturbo\b|\bturboprop\b|\bprop\b|'
        r'\bking.?air\b|\bpc.?12\b|\btbm\b|\bpilatus\b|'
        r'\bcaravan\b|\bkodiak\b|\bquest\b|'
        r'\bbeech.?1900\b|\batr\b|\bdash.?8\b|\bq400\b|'
        r'\bsocata\b|\btbm.?9\b|\bpa.?46\b|\bpiaggio\b|\bavanti\b',
        name
    ))


def _select_top5(hits: list, pax: int) -> list:
    filtered = filter_by_pax(hits, pax)

    price_key  = lambda h: h.get("rawPrice") or h.get("originalRawPrice") or 999999
    jets       = sorted([h for h in filtered if not _is_turboprop(h)], key=price_key)
    turboprops = sorted([h for h in filtered if     _is_turboprop(h)], key=price_key)

    # ── Selection Logic ───────────────────────────────────────────────────────
    # If turboprops exist, we want the cheapest one to occupy slot #5.
    # To do this, we MUST provide the top 4 jets first.
    if turboprops:
        # If we have at least 4 jets, take 4 + the turboprop (Total 5, TP is 5th)
        if len(jets) >= 4:
            return jets[:4] + [turboprops[0]]
        # If we have fewer than 4 jets, just show all jets followed by the turboprop.
        # Note: it will be in (len(jets)+1)th place, which is the "last" of the top results.
        return jets + [turboprops[0]]
    
    # If no turboprops, just show top 5 jets.
    return jets[:5]


def clean_hit(hit: dict) -> dict:
    aircraft_name = (hit.get("uniqueName") or "").strip() or "Charter Aircraft"
    raw_price     = hit.get("rawPrice") or hit.get("originalRawPrice") or 0
    price_str     = f"${raw_price:,.0f} USD" if raw_price else hit.get("price", "N/A")
    prices        = convert_all(float(raw_price)) if raw_price else {}
    capacity      = f"{hit.get('minPax', 1)}-{hit.get('maxPax', '?')} passengers"

    segments = hit.get("segments") or []
    dep_label, arr_label, flight_time = "", "", "N/A"
    if segments:
        seg       = segments[0]
        dep_code  = seg.get("start", "")
        arr_code  = seg.get("end", "")
        dep_human = (seg.get("startAsHumanText") or "").strip()
        arr_human = (seg.get("endAsHumanText") or "").strip()

        def full_label(human, code, obj=None):
            if human and code: return f"{human} ({code})"
            if obj and code:
                lbl = _build_label(obj, code.upper())
                if lbl != code.upper():
                    _label_cache[code.upper()] = lbl
                    _airport_cache[code.lower()] = obj
                    return lbl
            return get_airport_label(code) if code else "Unknown"

        dep_label = full_label(dep_human, dep_code, seg.get("startAirport") or hit.get("startAirport") or {})
        arr_label = full_label(arr_human, arr_code, seg.get("endAirport")   or hit.get("endAirport")   or {})

        raw_ft = seg.get("flightTime") or seg.get("flight_time") or hit.get("flightTime") or ""
        if raw_ft:
            parts = str(raw_ft).split(":")
            if len(parts) == 2:
                try:
                    h_val, m_val = int(parts[0]), int(parts[1])
                    flight_time = f"{h_val}h {m_val}m" if m_val else f"{h_val}h"
                except ValueError:
                    flight_time = str(raw_ft)
            else:
                flight_time = str(raw_ft)

    return {
        "aircraft_name":     aircraft_name,
        "capacity":          capacity,
        "price_usd":         price_str,
        "prices":            prices,
        "flight_time":       flight_time,
        "departure_airport": dep_label or "Unknown",
        "arrival_airport":   arr_label or "Unknown",
        "is_turbo":          _is_turboprop(hit),
    }


@tool
def search_flights(
    departure_city: str,
    destination_city: str,
    date: str,
    pax: str,
    use_alternative_airport: Union[bool, str] = False,
) -> str:
    """
    Search private jet flights via Avinode.
    pax: number of passengers as string e.g. "5"
    date: departure date in YYYY-MM-DD format (LLM converts all user date formats before calling)
    use_alternative_airport: boolean — true only when user explicitly asked for
      alternative airport options. Also accepts "true"/"false" strings safely via _coerce_bool.
    Returns top 5 results: jets cheapest-first, turboprops listed last.
    Each aircraft includes a 'prices' dict with conversions for USD, INR, EUR, GBP, CHF, AED, JPY, CAD, AUD.
    """
    use_alt = _coerce_bool(use_alternative_airport)

    try:
        pax_int = int(pax)
    except (ValueError, TypeError):
        return json.dumps({"error": f"Invalid pax value: {pax}"})

    dep_list  = resolve_airports(departure_city)
    dest_list = resolve_airports(destination_city)

    if not dep_list and re.match(r'^[A-Za-z]{3,4}$', departure_city.strip()):
        single = resolve_airport(departure_city.strip().upper())
        dep_list = [single] if single else []
    if not dest_list and re.match(r'^[A-Za-z]{3,4}$', destination_city.strip()):
        single = resolve_airport(destination_city.strip().upper())
        dest_list = [single] if single else []

    if not dep_list:
        return json.dumps({"error": f"Could not resolve departure airport for '{departure_city}'."})
    if not dest_list:
        return json.dumps({"error": f"Could not resolve arrival airport for '{destination_city}'."})

    # ── Airport selection ─────────────────────────────────────────────────────
    # When use_alt=True: pick dep_list[1] if available, dest_list[1] if available.
    # Falls back to index 0 for whichever side has no alternative.
    has_alt_dep  = len(dep_list)  > 1
    has_alt_dest = len(dest_list) > 1

    dep  = dep_list [1 if (use_alt and has_alt_dep)  else 0]
    dest = dest_list[1 if (use_alt and has_alt_dest) else 0]

    payload = {"segments": [{
        "startAirportId":          int(dep["id"]),
        "startAirportSearch":      dep["code"],
        "endAirportId":            int(dest["id"]),
        "endAirportSearch":        dest["code"],
        "date":                    date,
        "time":                    "09:00",
        "paxCount":                str(pax_int),
        "numberOfDaysFlexibility": "0"
    }]}

    try:
        response = requests.post(
            "https://apps.avinode.com/webapp/rest/search",
            json=payload, headers=headers(json_mode=True), timeout=15,
        )
    except requests.exceptions.Timeout:
        return json.dumps({"error": "Search timed out. Please try again."})

    if response.status_code != 200:
        return json.dumps({"error": response.text})

    hits = response.json().get("data", {}).get("searchHits", [])
    top5 = _select_top5(hits, pax_int)

    if not top5:
        return json.dumps({"message": "No aircraft found for this route and passenger count."})

    cleaned   = [clean_hit(h) for h in top5]
    dep_label = _build_label(dep,  (dep.get("code")  or "").upper()) if dep.get("code")  else ""
    dst_label = _build_label(dest, (dest.get("code") or "").upper()) if dest.get("code") else ""

    for ac in cleaned:
        if dep_label and ac["departure_airport"] in ("", dep.get("code", ""),  "Unknown"):
            ac["departure_airport"] = dep_label
        if dst_label and ac["arrival_airport"]   in ("", dest.get("code", ""), "Unknown"):
            ac["arrival_airport"] = dst_label

    # ── Probe for alternative airports (only on the primary search) ───────────
    # Logic:
    #   Both alt dep + alt dest exist → probe dep_list[1] → dest_list[1]
    #   Only alt dep exists           → probe dep_list[1] → dest_list[0]  (original dest)
    #   Only alt dest exists          → probe dep_list[0] → dest_list[1]  (original dep)
    #   Neither exists                → no probe, no offer
    alt_available     = False
    alt_dep_label     = ""
    alt_dest_label    = ""

    if not use_alt and (has_alt_dep or has_alt_dest):
        probe_dep  = dep_list [1 if has_alt_dep  else 0]
        probe_dest = dest_list[1 if has_alt_dest else 0]
        try:
            alt_payload = {"segments": [{
                "startAirportId":          int(probe_dep["id"]),
                "startAirportSearch":      probe_dep["code"],
                "endAirportId":            int(probe_dest["id"]),
                "endAirportSearch":        probe_dest["code"],
                "date":                    date,
                "time":                    "09:00",
                "paxCount":                str(pax_int),
                "numberOfDaysFlexibility": "0"
            }]}
            alt_resp = requests.post(
                "https://apps.avinode.com/webapp/rest/search",
                json=alt_payload, headers=headers(json_mode=True), timeout=10,
            )
            if alt_resp.status_code == 200:
                alt_hits = alt_resp.json().get("data", {}).get("searchHits", [])
                if filter_by_pax(alt_hits, pax_int):
                    alt_available  = True
                    # Only populate the label for whichever side actually changed
                    if has_alt_dep:
                        alt_dep_label  = _build_label(probe_dep,  (probe_dep.get("code")  or "").strip().upper())
                    if has_alt_dest:
                        alt_dest_label = _build_label(probe_dest, (probe_dest.get("code") or "").strip().upper())
        except Exception:
            pass

    return json.dumps({
        "total_results":                  len(cleaned),
        "used_departure_airport":         dep_label or dep.get("code", departure_city),
        "used_arrival_airport":           dst_label or dest.get("code", destination_city),
        "alternative_airport_available":  alt_available,
        "alternative_departure_airport":  alt_dep_label,   # empty string if dep didn't change
        "alternative_arrival_airport":    alt_dest_label,  # empty string if dest didn't change
        "aircraft":                       cleaned,
    }, indent=2)