import streamlit as st
import streamlit.components.v1 as components
import requests, uuid, re, json, html as H

try:
    from PIL import Image
    st.set_page_config(page_title="JetBot", page_icon=Image.open("favicon.png"), layout="wide")
except:
    st.set_page_config(page_title="JetBot", page_icon="✈", layout="wide")

BACKEND   = "http://localhost:8000/chat/stream"
PAGE_SIZE = 5
SHOW_MORE_WORDS = {"yes","more","show more","next","continue","yep","yeah","sure","ok","okay","show all","show remaining"}
BOT_SVG   = '<svg width="22" height="22" viewBox="0 0 24 24"><path d="M21 16v-2l-8-5V3.5C13 2.67 12.33 2 11.5 2S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" fill="#8a6a42"/></svg>'
USER_SVG  = '<svg width="20" height="20" viewBox="0 0 24 24"><defs><radialGradient id="ug" cx="50%" cy="35%" r="60%"><stop offset="0%" stop-color="#7a5a32"/><stop offset="100%" stop-color="#4a3018"/></radialGradient></defs><circle cx="12" cy="8" r="3.8" fill="url(#ug)"/><path d="M4.5 21c0-4.1 3.4-7.2 7.5-7.2s7.5 3.1 7.5 7.2" fill="url(#ug)"/></svg>'
DISCLAIMER_NOTE = (
    "Prices are approximate and based on current market estimates. Final quotes may vary. "
    "Flight times are approximate and may vary based on weather, air traffic, and other operational factors."
)
KNOWN_AMENITIES = [
    ("wifi","WiFi"),("wi-fi","WiFi"),("catering","Catering"),("food","Catering"),
    ("vip lounge","VIP Lounge"),("vip","VIP Lounge"),("hangar storage","Hangar Storage"),
    ("hangar","Hangar Storage"),("customs handling","Customs Handling"),("customs","Customs Handling"),
    ("pet-friendly","Pet-Friendly"),("pet friendly","Pet-Friendly"),("pet","Pet-Friendly"),
    ("gpu","GPU"),("ground power","GPU"),
]
_AMENITY_NONE = {"none","no","n/a","nothing","nil","none listed","no amenities","i don't need any","i don't require any","no thanks","not required"}
_ALT_REQ = re.compile(
    r'\b(alternative|alternate|other\s+airport|different\s+airport|show\s+(me\s+)?alternative|yes,?\s+show|other\s+option|another\s+airport)\b|'
    r'^(yes|yeah|yep|yup|sure|ok|okay|show|please|go ahead|do it|show me|show\s+alternative|alternative|other airport|yes please)[\s!.,]*$',
    re.IGNORECASE | re.MULTILINE
)
CURRENCY_OPTIONS = ["USD", "INR", "EUR", "GBP", "CHF", "AED", "JPY", "CAD", "AUD"]
CURRENCY_SYMBOLS = {"USD":"$","INR":"₹","EUR":"€","GBP":"£","CHF":"CHF","AED":"AED","JPY":"¥","CAD":"CA$","AUD":"A$"}
CURRENCY_LABELS  = {
    "USD":"USD — US Dollar","INR":"INR — Indian Rupee","EUR":"EUR — Euro",
    "GBP":"GBP — British Pound","CHF":"CHF — Swiss Franc","AED":"AED — UAE Dirham",
    "JPY":"JPY — Japanese Yen","CAD":"CAD — Canadian Dollar","AUD":"AUD — Australian Dollar",
}
_LABEL_TO_CODE = {v: k for k, v in CURRENCY_LABELS.items()}

# ── Redis store ───────────────────────────────────────────────────────────────
@st.cache_resource
def _get_store():
    from redis_store import SessionStore
    return SessionStore()

def _store_load(sid):
    try: return _get_store().load(sid)
    except: return []

def _store_append(sid, msg):
    try: _get_store().append(sid, msg)
    except: pass

def _store_clear(sid):
    try: _get_store().clear(sid)
    except: pass

# ── Price data store ──────────────────────────────────────────────────────────
def _ensure_prices_store():
    if "prices_store" not in st.session_state:
        st.session_state.prices_store = {}

def _normalise_name(name: str) -> str:
    n = re.sub(r'\s*[—–]\s*[Tt]urbo.*$|\s*⚠.*$|\s*⭐.*$', '', name)
    return re.sub(r'[^\w\s]', '', n).lower().strip()

def _clean_price(price_str: str) -> str:
    if not price_str: return price_str
    cleaned = re.sub(r'\s+[A-Z]{2,4}\s*$', '', price_str.strip())
    return cleaned if cleaned else price_str

def _store_prices_for_message(msg_index: int, prices_map: dict):
    _ensure_prices_store()
    key = str(msg_index)
    existing = st.session_state.prices_store.get(key) or {}
    existing.update(prices_map)
    norm_index = existing.get("__norm__") or {}
    for name, prices in prices_map.items():
        norm_index[_normalise_name(name)] = prices
    existing["__norm__"] = norm_index
    st.session_state.prices_store[key] = existing

def _get_price_for(msg_index: int, aircraft_name: str, currency: str, fallback: str) -> str:
    _ensure_prices_store()
    if currency == "USD": return _clean_price(fallback)
    key = str(msg_index)
    store = st.session_state.prices_store.get(key) or {}
    prices = store.get(aircraft_name) or {}
    if prices and currency in prices: return _clean_price(prices[currency])
    norm_index = store.get("__norm__") or {}
    norm_query = _normalise_name(aircraft_name)
    if norm_query in norm_index:
        p = norm_index[norm_query]
        if currency in p: return _clean_price(p[currency])
    for stored_name, stored_prices in store.items():
        if stored_name == "__norm__" or not isinstance(stored_prices, dict): continue
        sn = stored_name.lower()
        if norm_query in sn or sn in norm_query:
            if currency in stored_prices: return _clean_price(stored_prices[currency])
    return fallback

# ── Helpers ───────────────────────────────────────────────────────────────────
def _e(s): return H.escape(str(s))

def _render_amenities_block(text: str) -> str:
    lines = text.strip().splitlines()
    lead_lines, item_lines, tail_lines = [], [], []
    in_items = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- ") and not in_items and any(kw in stripped.lower() for kw in ("wifi","wi-fi","catering","vip")):
            in_items = True
        if in_items:
            if stripped.startswith("- Reply") or stripped.startswith("- reply"):
                tail_lines.append(stripped[2:].strip())
            elif stripped.startswith("- "):
                item_lines.extend([i.strip().rstrip(".") for i in stripped[2:].split(",") if i.strip()])
            else:
                tail_lines.append(stripped)
        else:
            lead_lines.append(stripped)
    if not item_lines: return md_simple(text)
    items_str = ",  ".join(item_lines)
    tail_html = f'<div class="am-tail">{_e(tail_lines[0])}</div>' if tail_lines else ""
    lead_html = "<br>".join(H.escape(l) for l in lead_lines if l)
    return f'<div class="am-lead">{lead_html}</div><div class="am-items">{_e(items_str)}</div>{tail_html}'

def md_simple(text):
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', H.escape(text))
    t = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2" target="_blank">\1</a>', t)
    return t.replace('\n', '<br>')

def md(text): return md_simple(text)

def field(body, pat):
    m = re.search(pat, body)
    return re.sub(r'\*+|<[^>]+>', '', m.group(1)).strip() if m else ""

def clean_intro(text):
    t = text.strip()
    for bad in [
        r'[Uu]nfortunately[^\n.]+[.\n]?', r'[Nn]one of the[^\n.]+[.\n]?',
        r'[Hh]ere are.{0,60}without.{0,40}filter[^\n.]*[.\n]?', r'\n+\d+\.[^\n]+',
        r'and will be confirmed at booking\.?', r'[Nn]ow[,\s]+I will search[^\n.]+[.\n]?',
        r'[Ll]et me search[^\n.]+[.\n]?', r'[Ss]earching for[^\n.]+[.\n]?',
        r'[Ii] will now search[^\n.]+[.\n]?', r'[Ii] will search[^\n.]+[.\n]?',
        r'[Hh]ere are the private jet[^\n.]+[.\n]?', r'[Yy]our amenity[^\n.]+[.\n]?',
        r'[Yy]our.*amenit.*preference[^\n.]+[.\n]?', r'[Aa]menit.*have been noted[^\n.]+[.\n]?',
    ]:
        t = re.sub(bad, '', t).strip()
    t = re.sub(r'[Hh]ere are.{0,80}(?:options|jets?|aircraft)[^\n]*\n?', '', t).strip()
    return "Here are the top aircraft options for your route:" + (f"\n\n{t}" if t else "")

def parse_aircraft(content, msg_index=None, currency="USD"):
    content = re.sub(r'^[⭐💡][^\n]*\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'^─+$', '', content, flags=re.MULTILINE)
    blocks = re.findall(r'\d+\.\s+\*\*(.+?)\*\*(.*?)(?=\n\d+\.\s+\*\*|\Z)', content, re.DOTALL)
    out, dep, arr = [], "", ""
    for name, body in blocks:
        p  = re.sub(r'<br>.*', '', field(body, r'[Pp]rice[:\s]+([^\n*<br]+)')).strip() or "—"
        ft = re.sub(r'\*+', '', field(body, r'[Ff]light\s*[Tt]ime[:\s]+([^\n*<]+)')).strip() or "N/A"
        if re.search(r'than jets|longer|slower|turboprop', ft, re.IGNORECASE): ft = "N/A"
        cap       = field(body, r'[Cc]apacity[:\s]+([^\n*<]+)') or "—"
        amenities = field(body, r'[Aa]menities[:\s]+([^\n*<]+)') or ""
        booking   = field(body, r'Request this flight.*?\(?(https?://[^\s\)]+)\)?') or ""
        route_raw = field(body, r'[Rr]oute[:\s]+([^\n]+)')
        if route_raw and '→' in route_raw:
            parts = re.split(r'→', route_raw, maxsplit=1)
            d = re.sub(r'\*+', '', parts[0]).strip()
            a = re.sub(r'\*+', '', parts[1]).strip() if len(parts) > 1 else ""
        else:
            d = re.sub(r'\*+|\s*[Aa]rrival.*$', '', field(body, r'[Dd]eparture[:\s]+([^\n]+)')).strip()
            a = re.sub(r'\*+|\s*\*.*$', '',       field(body, r'[Aa]rrival[:\s]+([^\n]+)')).strip()
        if not dep and d: dep, arr = d, a
        full_name  = name.strip()
        is_turbo   = bool(re.search(r'\bturbo\s*prop\b', full_name, re.IGNORECASE))
        name_clean = re.split(r'\s*[—–]\s*[Tt]urbo|\s*⚠|\s*⭐', full_name)[0].strip()
        p = _get_price_for(msg_index, name_clean, currency, p) if msg_index is not None else _clean_price(p)
        out.append({"name": name_clean, "price": p, "ft": ft, "dep": d, "arr": a,
                    "cap": cap, "amenities": amenities, "booking": booking, "is_turbo": is_turbo})
    return out, dep, arr

def split_roundtrip(content):
    for pat in [r'(?=✈\s*Return[:\s])', r'(?=↩\s*Return[:\s])', r'(?=\*{1,2}✈?\s*Return[:\s*])',
                r'(?=\n\s*Return\s+(?:Flight|Leg)[:\s])', r'(?=\n[-─]{2,}\s*Return)']:
        parts = re.split(pat, content, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2 and parts[1].strip() and re.search(r'\d+\.\s+\*\*', parts[1]):
            return parts[0], parts[1]
    return content, None

def split_multileg(content):
    parts = re.compile(r'(?=✈\s*Leg\s+\d+[:\s]|✈\s*[A-Za-z]+\s+Leg[:\s])', re.IGNORECASE).split(content)
    legs = [p for p in parts if p.strip() and re.search(r'\d+\.\s+\*\*', p)]
    return legs if len(legs) >= 2 else None

def _all_dates(messages, before_idx):
    _MM = {"january":"01","february":"02","march":"03","april":"04","may":"05","june":"06",
           "july":"07","august":"08","september":"09","october":"10","november":"11","december":"12",
           "jan":"01","feb":"02","mar":"03","apr":"04","jun":"06","jul":"07","aug":"08",
           "sep":"09","sept":"09","oct":"10","nov":"11","dec":"12"}
    _MP = r'january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec'
    def _nat(text):
        def _dmy(m):
            try: return f"{m.group(3)}-{_MM[m.group(2).lower()]}-{m.group(1).zfill(2)}"
            except: return m.group(0)
        def _mdy(m):
            try: return f"{m.group(3)}-{_MM[m.group(1).lower()]}-{m.group(2).zfill(2)}"
            except: return m.group(0)
        text = re.sub(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MP})\s+(\d{{4}})\b', _dmy, text, flags=re.IGNORECASE)
        return re.sub(rf'\b({_MP})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b', _mdy, text, flags=re.IGNORECASE)
    seen, out = set(), []
    for m in messages[:before_idx + 1]:
        for raw in [m["content"], m.get("resolved", "")]:
            for d in re.findall(r'\b(\d{4}-\d{2}-\d{2})\b', _nat(raw)):
                if d not in seen: seen.add(d); out.append(d)
    return out

def _extract_pax(messages, before_idx):
    pax_re = re.compile(
        r'\b(\d{1,2})\s*(?:pax|passenger[s]?|people|person[s]?|travell?er[s]?|guest[s]?)\b'
        r'|\b(?:pax|passenger[s]?|people|person[s]?|travell?er[s]?|guest[s]?)\s*[:\-]?\s*(\d{1,2})\b',
        re.IGNORECASE
    )
    bare_num_re = re.compile(r'^\s*(\d{1,2})\s*$')
    result = None
    for m in messages[:before_idx + 1]:
        if m["role"] != "user": continue
        text = (m.get("resolved") or m.get("content", "")).strip()
        match = pax_re.search(text)
        if match:
            n = int(match.group(1) or match.group(2))
            if 1 <= n <= 19: result = n
            continue
        bare = bare_num_re.match(text)
        if bare:
            idx = messages.index(m)
            for prev in reversed(messages[:idx]):
                if prev["role"] == "assistant":
                    if re.search(r'how many passenger|number of passenger|how many.*travel|how many.*pax', prev["content"], re.IGNORECASE):
                        n = int(bare.group(1))
                        if 1 <= n <= 19: result = n
                    break
    return result

def _scan_amenities(text, from_user=True):
    t = text.lower().strip()
    if from_user and (t in _AMENITY_NONE or re.match(r'^(none|no|nope|nah|nil|nothing|no thanks)[\s.,!]*$', t)):
        return []
    if not from_user and not re.search(r'have been noted|you require|amenities? noted|as an amenity|as amenities|noted.*amenit|your.*amenit|prefer.*amenit|with.*amenit', t):
        return []
    found, seen = [], set()
    for kw, canonical in KNOWN_AMENITIES:
        if re.search(r'\b' + re.escape(kw) + r'\b', t) and canonical not in seen:
            found.append(canonical); seen.add(canonical)
    return found

def has_pending_more(messages):
    last = next((i for i, m in reversed(list(enumerate(messages)))
                 if m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE)), None)
    if last is None: return False
    ac, _, _ = parse_aircraft(messages[last]["content"])
    if len(ac) <= PAGE_SIZE: return False
    return not any(m["role"] == "user" and m["content"].strip().lower() in SHOW_MORE_WORDS for m in messages[last + 1:])

# ── HTML building blocks ──────────────────────────────────────────────────────
def summary_card(aircraft, dep, arr, amenities=None, label="", date="", pax=None):
    al = amenities or []
    lines = [f"✈ {label} Summary" if label else "✈ Private Jet Summary", "", f"Route: {dep or '—'} → {arr or '—'}"]
    if date: lines.append(f"Date:  {date}")
    if pax:  lines.append(f"Passengers: {pax}")
    if al:   lines.append(f"Amenities: {', '.join(al)}")
    lines += [""] + [f"{i}. {ac['name']}  —  {ac['price']}  ·  {ac['ft']}" for i, ac in enumerate(aircraft, 1)] + ["", "Searched via JetBot · Private Aviation Intelligence"]
    def code(s): return f' <span style="color:#b8a898">({m.group(1)})</span>' if (m := re.search(r'\(([^)]+)\)\s*$', s)) else ""
    def city(s): return _e(s.split("(")[0].strip()) if s else "—"
    route_row    = f'<div class="sc-meta"><span class="sc-meta-label">Route</span><span class="sc-route-val">{city(dep)}{code(dep)}</span><span class="sc-sep">→</span><span class="sc-route-val">{city(arr)}{code(arr)}</span></div>' if (dep or arr) else ""
    pax_part     = f'<span class="sc-meta-divider">·</span><span class="sc-pax-label">Pax</span><span class="sc-pax-val">{_e(str(pax))}</span>' if pax else ""
    date_row     = f'<div class="sc-date-row"><span class="sc-date-label">Date</span><span class="sc-date-val">{_e(date)}</span>{pax_part}</div>' if (date or pax) else ""
    amenity_html = f'<div class="sc-amenity-row"><span class="sc-amenity-label">Noted Amenities</span><span class="sc-amenity-check">✓</span><span class="sc-amenity-val">{_e(", ".join(al))}</span></div>' if al else ""
    rows         = "".join(f'<div class="sc-row"><span class="sc-idx">{i:02d}</span><span class="sc-acname">{_e(ac["name"])}</span><span class="sc-price">{_e(ac["price"])}</span><span class="sc-ft">{_e(ac["ft"])}</span></div>' for i, ac in enumerate(aircraft, 1))
    data_text    = _e(json.dumps("\n".join(lines)))
    card_label   = _e(label) if label else "Flight"
    return (f'<div class="summary-card"><div class="sc-header"><div class="sc-title"><svg width="14" height="14" viewBox="0 0 24 24"><path d="M21 16v-2l-8-5V3.5C13 2.67 12.33 2 11.5 2S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" fill="#8a6a42"/></svg>{card_label} Summary</div>'
            f'<button class="copy-btn" data-text="{data_text}" onclick="var t=JSON.parse(this.getAttribute(\'data-text\'));navigator.clipboard.writeText(t).then(()=>{{this.textContent=\'✓ Copied!\';this.classList.add(\'copied\');setTimeout(()=>{{this.textContent=\'Copy\';this.classList.remove(\'copied\')}},2500)}});">Copy</button>'
            f'</div>{route_row}{date_row}{amenity_html}<div class="sc-rows">{rows}</div><div class="sc-footer">Searched via JetBot · Private Aviation Intelligence</div></div>')

def _make_cards(aircraft_list, start_idx=1, msg_index=None, currency="USD"):
    def _parse_price(p):
        try: return float(re.sub(r'[^\d.]', '', p))
        except: return float('inf')
    first_non_turbo = next((start_idx + i for i, ac in enumerate(aircraft_list) if not ac.get("is_turbo")), None)
    cheapest_jet    = min((_parse_price(ac["price"]) for ac in aircraft_list if not ac.get("is_turbo")), default=float('inf'))
    cards = ""
    for idx, ac in enumerate(aircraft_list, start_idx):
        is_best  = (idx == first_non_turbo)
        is_turbo = ac.get("is_turbo", False)
        price    = _get_price_for(msg_index, ac["name"], currency, ac["price"]) if msg_index is not None else _clean_price(ac["price"])
        best_tag  = '<div class="ac-best-tag"><span class="ac-star">&#9733;</span> Best Value</div>' if is_best else ""
        turbo_tag = f'<div class="ac-turbo-tag">{"Turboprop — More affordable, but slower" if _parse_price(ac["price"]) < cheapest_jet else "Turboprop — Slower than jets"}</div>' if is_turbo else ""
        border    = 'border-left:3px solid #8a6a42;' if is_best else ('border-left:3px solid #c08040;' if is_turbo else 'border-left:3px solid rgba(138,106,66,.25);')
        dep_short = (ac.get("dep") or "—").split("(")[0].strip().split(",")[0].strip()
        arr_short = (ac.get("arr") or "—").split("(")[0].strip().split(",")[0].strip()
        route_inline = f'<span class="ac-route-inline">{_e(dep_short)} → {_e(arr_short)}</span>' if (ac.get("dep") or ac.get("arr")) else ""
        am = ac.get("amenities", "")
        amenity_html = f'<div class="ac-amenity"><span class="ac-label">Amenities</span><span class="ac-am-val">{_e(am)}</span></div>' if am and am.lower() not in ("none listed", "none", "") else ""
        cards += (
            f'<div class="aircraft-card" style="{border}">'
            f'<div class="ac-top-row"><div class="ac-left">'
            f'{best_tag}{turbo_tag}'
            f'<div class="ac-name">{_e(ac["name"])}</div>'
            f'<div class="ac-sub-row">'
            f'<span class="ac-cap-pill"><svg width="11" height="11" viewBox="0 0 24 24" style="vertical-align:middle;margin-right:3px"><circle cx="12" cy="7" r="4" fill="#8a6a42"/><path d="M5.5 20c0-3.6 3-6.3 6.5-6.3s6.5 2.7 6.5 6.3" fill="#8a6a42"/></svg>{_e(ac.get("cap","—"))}</span>'
            f'<span class="ac-ft-pill">⏱ {_e(ac["ft"])}</span>'
            f'{route_inline}</div>{amenity_html}</div>'
            f'<div class="ac-right"><div class="ac-price">{_e(price)}</div><div class="ac-idx-label">#{idx:02d}</div></div>'
            f'</div></div>'
        )
    return cards

def _disclaimer_html():
    return f'<div class="disclaimer-note"><span class="disclaimer-icon">⚠️</span><span class="disclaimer-text">{_e(DISCLAIMER_NOTE)}</span></div>'

def _amenities_noted_html(amenities: list) -> str:
    if not amenities:
        return ""
    chips = "".join(
        f'<span style="display:inline-block;background:#f5efe6;'
        f'border:1px solid #c9a96e;border-radius:20px;'
        f'padding:3px 11px;margin:2px 4px;font-size:.68rem;'
        f'color:#6b4c1e;font-weight:500;letter-spacing:.03em;">✦ {_e(a)}</span>'
        for a in amenities
    )
    return (
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;'
        f'margin:10px 0 2px;padding:8px 14px;'
        f'background:rgba(245,239,230,.6);border:1px solid rgba(201,169,110,.3);'
        f'border-radius:8px;">'
        f'<span style="font-size:.55rem;letter-spacing:.18em;text-transform:uppercase;'
        f'color:#b8a898;margin-right:8px;white-space:nowrap;">✓ Noted Amenities</span>'
        f'{chips}</div>'
    )

def _post_results_html(is_multileg: bool = False) -> str:
    closing = (
        "Your multi-leg itinerary is ready above. Need to tweak any leg, or plan a new trip?"
        if is_multileg else
        "Need to adjust anything — dates, passengers, or route? Or ready to plan another flight?"
    )
    return f'<div class="bubble bot post-results-bubble">{closing}</div>'

def _route_strip(dep, arr):
    ds, as_ = (dep or "—").split(",")[0].strip(), (arr or "—").split(",")[0].strip()
    return (f'<div class="flight-summary"><div class="fs-pill"><span class="fs-label">From</span><span class="fs-value">{_e(ds)}</span></div>'
            f'<span class="fs-sep">→</span><div class="fs-pill"><span class="fs-label">To</span><span class="fs-value">{_e(as_)}</span></div>'
            f'<span class="fs-meta">Top options by price</span></div>') if (ds != "—" or as_ != "—") else ""

def _leg_header(icon, label, dep, arr, user_dep="", user_arr=""):
    airport_dep = (dep or "—").split(",")[0].strip()
    airport_arr = (arr or "—").split(",")[0].strip()
    has_user_cities = (
        user_dep and user_arr
        and user_dep.lower().strip() != airport_dep.lower().strip()
    )
    if has_user_cities:
        city_span = (
            f'<span class="leg-city-route">'
            f'{_e(user_dep.upper())} → {_e(user_arr.upper())}'
            f'</span>'
        )
    else:
        city_span = ""
    return (
        f'<div class="leg-header">'
        f'<span class="leg-icon">{icon}</span>'
        f'<span class="leg-label">{_e(label)}</span>'
        f'{city_span}'
        f'<span class="leg-route">{_e(airport_dep)}</span>'
        f'<span class="leg-arrow">→</span>'
        f'<span class="leg-route">{_e(airport_arr)}</span>'
        f'</div>'
    )

def render_leg(ac_list, dep, arr, session_id, amenities, label, icon, date="", alt_airport="", msg_index=None, currency="USD", pax=None, is_last_leg=False, is_multileg=False, user_dep="", user_arr=""):
    total, shown = len(ac_list), ac_list[:PAGE_SIZE]
    if total == 0:
        count = f'<div class="bubble bot" style="margin-top:8px">No {label.lower()} aircraft found for this leg.</div>'
    elif total <= PAGE_SIZE:
        alt = ""
        if alt_airport and not is_multileg:
            alt = (f' ✈ I also have results from <strong>{_e(alt_airport)}</strong> — reply <strong>show me the alternative airport</strong> to see them.')
        count = f'<div class="bubble bot" style="margin-top:8px">Showing <strong>{total} of {total}</strong> {label.lower()} aircraft. That\'s all available for this leg.{alt}</div>'
    else:
        count = f'<div class="bubble bot" style="margin-top:8px">Showing <strong>{PAGE_SIZE} of {total}</strong> {label.lower()} aircraft. Reply <strong>yes</strong> to see the remaining {total - PAGE_SIZE}.</div>'
    amenity_row = _amenities_noted_html(amenities or []) if is_last_leg else ""
    post = _post_results_html(is_multileg=is_multileg) if is_last_leg else ""
    return (_leg_header(icon, label, dep, arr, user_dep=user_dep, user_arr=user_arr) + _route_strip(dep, arr)
            + _make_cards(shown, 1, msg_index=msg_index, currency=currency)
            + amenity_row + count + post + _disclaimer_html())

def render_session(full_list, dep, arr, session_id, intro_html, amenities=None, card_label="", date="", alt_airport="", msg_index=None, currency="USD", pax=None, user_dep="", user_arr=""):
    total, shown = len(full_list), full_list[:PAGE_SIZE]
    if total <= PAGE_SIZE:
        alt = (f' ✈ I also have results from <strong>{_e(alt_airport)}</strong> — reply <strong>show me the alternative airport</strong> to see them.') if alt_airport else ""
        tail = f'<div class="bubble bot" style="margin-top:12px">Showing <strong>{total} of {total}</strong> available aircraft. That\'s all available aircraft for this route.{alt}</div>'
    else:
        tail = f'<div class="bubble bot" style="margin-top:12px">Showing <strong>{PAGE_SIZE} of {total}</strong> available aircraft. Reply <strong>yes</strong> to see the remaining {total - PAGE_SIZE}.</div>'
    leg_hdr = _leg_header("✈", "One-Way", dep, arr, user_dep=user_dep, user_arr=user_arr)
    return (intro_html + leg_hdr + _route_strip(dep, arr) + _make_cards(shown, 1, msg_index=msg_index, currency=currency)
            + _amenities_noted_html(amenities or []) + tail + _post_results_html() + _disclaimer_html())

# ── CSS ───────────────────────────────────────────────────────────────────────
CHAT_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300&family=DM+Mono:wght@300;400;500&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{background:transparent;font-family:'DM Mono',monospace;padding:8px 4px 0;display:flex;flex-direction:column;min-height:100vh}
#chat-messages{padding:16px 12px 18px;background:linear-gradient(180deg,#fbf8f3 0%, #f8f4ee 100%);min-height:575px;max-height:64vh;overflow-y:auto;scroll-behavior:smooth;}
.msg-row{display:flex;margin-bottom:18px;gap:13px;align-items:flex-start}
.msg-row.user{flex-direction:row-reverse}
.avatar{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px}
.avatar.bot{background:radial-gradient(135deg,rgba(247,226,182,.75),rgba(215,187,131,.22));border:2px solid #c8a464;box-shadow:0 0 0 1px rgba(255,225,150,.38),0 4px 16px rgba(138,106,66,.14),inset 0 1px 0 rgba(255,240,200,.95);}
.avatar.user{background:radial-gradient(135deg,#eedcb8,#d8bc90);border:2px solid #c8a464;box-shadow:0 0 0 1px rgba(255,225,150,.4),0 3px 12px rgba(138,106,66,.16),inset 0 1px 0 rgba(255,245,215,.9)}
.bubble{display:inline-block;max-width:72%;padding:9px 14px;border-radius:10px;font-size:.78rem;line-height:1.55;font-family:'DM Mono',monospace}
.bubble.bot{background:linear-gradient(180deg,#fffdfa 0%, #faf7f2 100%);border:1px solid rgba(138,106,66,.12);border-radius:12px 12px 12px 4px;color:#2c2318;box-shadow:0 2px 10px rgba(138,106,66,.05), inset 0 1px 0 rgba(255,255,255,.8);}
.bubble.user{display:inline-block;background:linear-gradient(145deg,#d8c4a0,#ccb48c);border:1px solid rgba(160,120,70,.35);border-radius:10px 10px 2px 10px;color:#2a1f12;box-shadow:0 2px 10px rgba(138,106,66,.15),inset 0 1px 0 rgba(255,245,220,.5);padding:9px 14px;font-size:.78rem;line-height:1.55}
.bubble.bot-compact{background:linear-gradient(180deg,#fffdfa 0%, #faf7f2 100%);border:1px solid rgba(138,106,66,.12);border-radius:12px 12px 12px 4px;color:#2c2318;box-shadow:0 2px 10px rgba(138,106,66,.05), inset 0 1px 0 rgba(255,255,255,.8);display:inline-block;max-width:72%;padding:9px 14px;font-size:.78rem;line-height:1.55;font-family:'DM Mono',monospace;}
.bubble strong{color:#6b4f2e;font-weight:500}
.aircraft-card{background:linear-gradient(150deg,rgba(255,255,255,.97),rgba(253,248,240,.99));border:1px solid rgba(138,106,66,.13);border-radius:8px;padding:12px 16px;margin:8px 0;font-family:'DM Mono',monospace;box-shadow:0 2px 12px rgba(138,106,66,.06),inset 0 1px 0 rgba(255,255,255,.9);transition:all .2s}
.aircraft-card:hover{box-shadow:0 4px 20px rgba(138,106,66,.11);transform:translateY(-1px)}
.ac-top-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.ac-left{flex:1;min-width:0}.ac-right{text-align:right;flex-shrink:0}
.ac-name{font-family:'Cormorant Garamond',serif;font-size:1.05rem;color:#1a2438;letter-spacing:.03em;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ac-sub-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:4px}
.ac-cap-pill,.ac-ft-pill{font-size:.65rem;color:#6b4f2e;background:rgba(138,106,66,.07);border:1px solid rgba(138,106,66,.14);border-radius:3px;padding:2px 7px;white-space:nowrap}
.ac-route-inline{font-size:.65rem;color:#9a8e80;letter-spacing:.06em}
.ac-price{font-size:1.1rem;color:#5a3e1e;font-weight:500;letter-spacing:.02em;line-height:1.2;margin-bottom:4px}
.ac-idx-label{font-size:.55rem;letter-spacing:.18em;text-transform:uppercase;color:rgba(138,106,66,.45)}
.ac-label{font-size:.55rem;letter-spacing:.14em;text-transform:uppercase;color:#b8a898}
.ac-amenity{margin-top:5px;display:flex;flex-direction:column;gap:1px}
.ac-am-val{font-size:.68rem;color:#3c3028;font-weight:300;font-style:italic}
.ac-best-tag{display:inline-flex;align-items:center;gap:4px;background:#fdf6e8;border:1px solid #eed49f;border-radius:3px;padding:2px 8px;font-size:.55rem;letter-spacing:.16em;text-transform:uppercase;color:#a07830;font-weight:500;margin-bottom:5px}
.ac-star{color:#c5973a;font-size:.65rem}
.ac-turbo-tag{display:inline-flex;align-items:center;gap:4px;background:#fff8f0;border:1px solid #e8c090;border-radius:3px;padding:2px 8px;font-size:.55rem;letter-spacing:.1em;color:#a06020;font-weight:500;margin-bottom:5px;margin-left:6px}
.disclaimer-note{display:flex;align-items:center;gap:8px;background:rgba(255,248,230,.7);border:1px solid rgba(200,160,60,.22);border-radius:6px;padding:10px 14px;margin:14px 0 4px;font-size:.67rem;line-height:1.65;color:#7a5a30}
.disclaimer-icon{flex-shrink:0;margin-top:0;display:flex;align-items:center;font-size:.8rem;}
.disclaimer-text{color:#7a5a30;letter-spacing:.01em}
.flight-summary{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,.7);border:1px solid rgba(138,106,66,.14);border-radius:6px;padding:10px 16px;margin-bottom:12px}
.fs-pill{display:flex;flex-direction:column;gap:1px}.fs-label{font-size:.5rem;letter-spacing:.2em;text-transform:uppercase;color:#b8a898}
.fs-value{font-size:.82rem;color:#2c2318}.fs-sep{color:rgba(138,106,66,.5);font-size:.9rem;margin:0 2px}.fs-meta{font-size:.65rem;color:#b8a898;margin-left:auto;letter-spacing:.08em}
.summary-card{background:linear-gradient(150deg,rgba(255,255,255,.92),rgba(253,248,240,.96));border:1px solid rgba(138,106,66,.18);border-radius:8px;padding:18px 22px;margin-top:16px;font-family:'DM Mono',monospace}
.sc-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;gap:10px}
.sc-title{display:flex;align-items:center;gap:8px;font-family:'Cormorant Garamond',serif;font-size:1rem;color:#1a2438;letter-spacing:.03em}
.copy-btn{display:inline-flex;align-items:center;gap:6px;font-family:'DM Mono',monospace;font-size:.58rem;letter-spacing:.16em;text-transform:uppercase;color:#6b4f2e;background:transparent;border:1px solid rgba(138,106,66,.3);border-radius:3px;padding:6px 13px;cursor:pointer;transition:all .2s;white-space:nowrap}
.copy-btn:hover{background:rgba(138,106,66,.07);border-color:rgba(138,106,66,.5)}.copy-btn.copied{color:#5a8a50;border-color:rgba(90,138,80,.4);background:rgba(90,138,80,.07)}
.sc-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid rgba(138,106,66,.1)}
.sc-meta-label{font-size:.55rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898;margin-right:4px}
.sc-route-val{font-size:.8rem;color:#2c2318}.sc-sep{color:rgba(138,106,66,.45);margin:0 4px}
.sc-date-row{display:flex;align-items:center;gap:10px;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid rgba(138,106,66,.1)}
.sc-date-label{font-size:.55rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898}
.sc-date-val{font-size:.8rem;color:#2c2318;font-variant-numeric:tabular-nums;letter-spacing:.04em}
.sc-pax-label{font-size:.55rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898;margin-left:2px}.sc-pax-val{font-size:.8rem;color:#2c2318;font-weight:500}.sc-meta-divider{color:rgba(138,106,66,.3);margin:0 10px;font-size:.7rem}.am-bubble{padding:10px 14px!important}.am-lead{font-size:.78rem;color:#2c2318;margin-bottom:7px;line-height:1.5}.am-items{font-size:.78rem;color:#2c2318;line-height:1.7;margin-bottom:8px}.am-tail{font-size:.72rem;color:#9a8e80;margin-top:4px;font-style:italic}
.sc-amenity-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid rgba(138,106,66,.1)}
.sc-amenity-label{font-size:.55rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898}.sc-amenity-check{color:#7a9a6a;font-size:.75rem}.sc-amenity-val{font-size:.8rem;color:#2c2318;font-style:italic}
.sc-rows{display:flex;flex-direction:column;gap:7px}.sc-row{display:flex;align-items:baseline;padding:6px 0;border-bottom:1px solid rgba(138,106,66,.06)}.sc-row:last-child{border-bottom:none}
.sc-idx{font-size:.57rem;letter-spacing:.14em;text-transform:uppercase;color:rgba(138,106,66,.5);width:28px;flex-shrink:0}
.sc-acname{font-family:'Cormorant Garamond',serif;font-size:.95rem;color:#1a2438;flex:1}.sc-price{font-size:.8rem;color:#5a3e1e;font-weight:500;white-space:nowrap;margin:0 10px}.sc-ft{font-size:.72rem;color:#9a8e80;white-space:nowrap}
.sc-footer{font-size:.58rem;color:#c8b898;letter-spacing:.1em;margin-top:12px;text-align:right}
.typing-wrap{display:flex;gap:13px;align-items:center;margin-bottom:18px}
.typing-dots{display:flex;gap:6px;padding:14px 20px;background:rgba(255,255,255,.92);border:1px solid rgba(138,106,66,.12);border-radius:10px 10px 10px 2px}
.dot{width:5px;height:5px;border-radius:50%;background:#b8976a;animation:pulse 1.4s ease-in-out infinite}
.dot:nth-child(2){animation-delay:.22s}.dot:nth-child(3){animation-delay:.44s}
@keyframes pulse{0%,80%,100%{opacity:.15;transform:scale(.7)}40%{opacity:1;transform:scale(1)}}
.leg-header{display:flex;align-items:center;gap:10px;background:linear-gradient(90deg,rgba(138,106,66,.1),rgba(138,106,66,.03));border:1px solid rgba(138,106,66,.18);border-left:3px solid #8a6a42;border-radius:6px;padding:10px 16px;margin:16px 0 8px;font-family:'DM Mono',monospace}
.leg-icon{font-size:1rem;flex-shrink:0}.leg-label{font-size:.58rem;letter-spacing:.22em;text-transform:uppercase;color:#8a6a42;font-weight:500;flex-shrink:0}
.leg-city-route{font-size:.58rem;letter-spacing:.18em;text-transform:uppercase;color:#8a6a42;font-weight:500;flex-shrink:0;margin-right:2px}
.leg-route{font-size:.82rem;color:#2c2318}.leg-arrow{color:rgba(138,106,66,.55);font-size:.8rem;margin:0 2px}
.roundtrip-divider{border:none;border-top:1px dashed rgba(138,106,66,.25);margin:20px 0}
.alt-airport-header{background:linear-gradient(90deg,rgba(138,106,66,.08),rgba(138,106,66,.02));border:1px solid rgba(138,106,66,.18);border-left:3px solid #b8976a;border-radius:6px;padding:12px 16px;margin:20px 0 8px;font-family:'DM Mono',monospace}
.alt-airport-title{font-size:.62rem;letter-spacing:.2em;text-transform:uppercase;color:#8a6a42;margin-bottom:6px}
.alt-airport-why{font-size:.72rem;color:#5a4030;line-height:1.6;font-style:italic}
.amenity-confirm{background:rgba(240,248,235,.85)!important;border-color:rgba(90,138,80,.2)!important}
.post-results-bubble{margin-top:14px!important;display:block!important;max-width:100%!important;font-size:.75rem!important;color:#5a4030!important;font-style:italic;background:linear-gradient(180deg,#fdfaf5 0%,#f9f4ec 100%)!important;border-color:rgba(138,106,66,.15)!important;}
.post-results-bar{display:flex;gap:8px;margin:10px 0 6px;flex-wrap:wrap}
.prb-btn{font-family:'DM Mono',monospace;font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;background:linear-gradient(180deg,#efe4d2 0%,#e6d6c0 100%);border:1.2px solid rgba(138,106,66,.55);border-radius:20px;padding:7px 18px;color:#352515;cursor:pointer;transition:background .15s,border-color .15s,box-shadow .15s;box-shadow:0 2px 8px rgba(112,84,42,.07);}
.prb-btn:hover{background:linear-gradient(180deg,#e8dac8 0%,#dccdb8 100%);border-color:rgba(138,106,66,.85);box-shadow:0 3px 12px rgba(112,84,42,.12);}
.prb-btn.prb-new{border-color:rgba(138,106,66,.30);color:#7a5a32}.prb-btn.prb-new:hover{border-color:rgba(138,106,66,.6)}
#modify-overlay{display:none;position:fixed;inset:0;background:rgba(26,20,12,.45);z-index:9998;backdrop-filter:blur(2px)}
#modify-overlay.open{display:flex;align-items:center;justify-content:center}
#modify-panel{background:linear-gradient(160deg,#fdfaf5 0%,#f8f2e6 100%);border:1.5px solid rgba(138,106,66,.4);border-radius:12px;padding:28px 32px 24px;width:min(480px,92vw);box-shadow:0 24px 60px rgba(26,20,12,.22),0 4px 16px rgba(138,106,66,.12);font-family:'DM Mono',monospace;position:relative;}
#modify-panel h3{font-family:'Cormorant Garamond',serif;font-size:1.25rem;color:#1a2438;letter-spacing:.04em;margin-bottom:20px;display:flex;align-items:center;gap:8px;}
.mod-field{margin-bottom:14px}
.mod-label{font-size:.55rem;letter-spacing:.22em;text-transform:uppercase;color:#a49379;display:block;margin-bottom:5px}
.mod-input{width:100%;background:rgba(255,255,255,.85);border:1px solid rgba(138,106,66,.28);border-radius:6px;padding:8px 12px;font-family:'DM Mono',monospace;font-size:.78rem;color:#2c2318;outline:none;transition:border-color .15s,box-shadow .15s;}
.mod-input:focus{border-color:rgba(138,106,66,.65);box-shadow:0 0 0 3px rgba(138,106,66,.08)}
.mod-row{display:flex;gap:12px}.mod-row .mod-field{flex:1}
.mod-actions{display:flex;gap:10px;margin-top:20px;justify-content:flex-end}
.mod-cancel{font-family:'DM Mono',monospace;font-size:.65rem;letter-spacing:.14em;text-transform:uppercase;background:transparent;border:1px solid rgba(138,106,66,.25);border-radius:20px;padding:8px 20px;color:#9a8070;cursor:pointer;transition:all .15s}
.mod-cancel:hover{border-color:rgba(138,106,66,.5);color:#6b4f2e}
.mod-search{font-family:'DM Mono',monospace;font-size:.65rem;letter-spacing:.14em;text-transform:uppercase;background:linear-gradient(180deg,#efe4d2,#e6d6c0);border:1.2px solid rgba(138,106,66,.55);border-radius:20px;padding:8px 24px;color:#352515;cursor:pointer;transition:all .15s;box-shadow:0 2px 8px rgba(112,84,42,.07)}
.mod-search:hover{background:linear-gradient(180deg,#e8dac8,#dfccb8);box-shadow:0 3px 12px rgba(112,84,42,.13)}
#modify-close{position:absolute;top:14px;right:16px;background:none;border:none;font-size:1.1rem;color:#b8a898;cursor:pointer;line-height:1;padding:2px 6px;border-radius:4px}
#modify-close:hover{color:#6b4f2e;background:rgba(138,106,66,.07)}
@media(max-width:768px){
  #chat-messages{padding:10px 6px 14px;min-height:300px}
  .msg-row{gap:8px;margin-bottom:12px}.avatar{width:32px;height:32px;flex-shrink:0}
  .bubble,.bubble.bot,.bubble.user,.bubble.bot-compact{max-width:88%!important;font-size:.76rem!important;padding:8px 11px!important;}
  .aircraft-card{padding:12px 12px!important}.ac-name{font-size:.85rem!important}.ac-price{font-size:.88rem!important}
  .ac-top-row{flex-direction:column!important;gap:6px!important}.ac-left{width:100%!important}
  .ac-right{flex-direction:row!important;align-items:center!important;justify-content:space-between!important;width:100%!important;border-left:none!important;border-top:1px solid rgba(138,106,66,.12)!important;padding-top:8px!important;padding-left:0!important;margin-top:4px!important}
  .flight-summary{flex-wrap:wrap;gap:6px;padding:8px 10px}.fs-meta{display:none}
  .summary-card{padding:12px 14px}.sc-title{font-size:.88rem!important}
  .leg-header{padding:8px 10px;gap:6px}.leg-label,.leg-route{font-size:.72rem!important}
  .disclaimer-note{font-size:.62rem;padding:8px 10px}
}
@media(max-width:480px){
  .bubble,.bubble.bot,.bubble.user,.bubble.bot-compact{max-width:94%!important;font-size:.74rem!important;}
  .aircraft-card{padding:10px 10px!important}.ac-name{font-size:.8rem!important}.ac-sub-row{flex-wrap:wrap;gap:4px}
}
"""

def _extract_user_cities(messages, before_idx: int) -> tuple:
    """
    Scan conversation history up to before_idx and return (dep_city, arr_city)
    as the user typed them (e.g. 'New York', 'Miami'), not the resolved airport names.
    Returns ("", "") if not found.
    """
    route_re = re.compile(
        r'(?:from\s+)?([A-Za-z][A-Za-z\s\-]{1,28}?)\s+to\s+([A-Za-z][A-Za-z\s\-]{1,28}?)(?:\s|$|[.,!?])',
        re.IGNORECASE
    )
    mod_re = re.compile(
        r'Search(?:\s+round-trip)?\s+from\s+(.+?)\s+to\s+(.+?)(?:,|\s+on\s)',
        re.IGNORECASE
    )
    dep, arr = "", ""
    noise = {"one","want","need","fly","travel","going","get","way","how","what","please","i","me","us"}
    for m in messages[:before_idx + 1]:
        if m["role"] != "user": continue
        content = m.get("content", "").strip()
        mm = mod_re.search(content)
        if mm:
            dep = mm.group(1).strip().split(",")[0].strip()
            arr = mm.group(2).strip().split(",")[0].strip()
            continue
        rm = route_re.search(content)
        if rm:
            d = rm.group(1).strip()
            a = rm.group(2).strip()
            if d.lower().split()[-1] not in noise and a.lower().split()[-1] not in noise:
                dep, arr = d, a
    return dep, arr


def _extract_alt_airport(text: str) -> str:
    # Check for multi-leg specific offer format first (captures "leg 1", "leg 1 and leg 3", "all legs", etc.)
    m = re.search(r'alternative airport(?: is|s are) available for ([^*\n.!?]+?)(?:\.| Reply| to see|\Z)', text, re.IGNORECASE)
    if m:
        return m.group(1)
        
    patterns = [
        r'also have results(?:\s+available)?(?:\s+from)?\s+\*{0,2}([^*\n.!?]{4,60}?)\*{0,2}(?:\.|,|\?|!|$|\s+Would)',
        r'results available(?:\s+from)?\s+\*{0,2}([^*\n.!?]{4,60}?)\*{0,2}(?:\.|,|\?|!|$|\s+Would)',
        r'[Ii] also have[^.]*?from\s+\*{0,2}([^*\n.!?]{4,60}?)\*{0,2}(?:\.|,|\?|!|$)',
        r'(?:options|flights|aircraft)\s+from\s+\*{0,2}([A-Z][^*\n.!?]{3,55}?)\*{0,2}(?:\.|,|\?|!|\s+Would|\s+Do)',
        r'departing\s+from\s+\*{0,2}([^*\n.!?]{4,60}?)\*{0,2}(?:\.|,|\?|!|$)',
        r'at\s+\*{0,2}([A-Z][^*\n.!?]{3,55}?\([A-Z]{3,4}\))\*{0,2}',
        r'\*\*([^*\n]{4,55}?\([A-Z]{3,4}\))\*\*',
    ]
    noise = {"the","this","those","here","there","them","all","both"}
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip().rstrip('.,!?').strip()
            if len(val) >= 3 and val.lower() not in noise: return val
    return ""

def _render_alt_airport(msg_content: str, msg_index: int = None, currency: str = "USD") -> str:
    alt_m = re.search(r'🔄\s*\*{0,2}Alternative departure[:\s*]+(.+?)(?=\n)', msg_content, re.IGNORECASE)
    if not alt_m: return ""
    alt_section = msg_content[alt_m.start():]
    why_m    = re.search(r'Why this airport\?[\*\s]+(.+?)(?=\n\n|\n\d+\.\s|\Z)', alt_section, re.DOTALL | re.IGNORECASE)
    why_text = re.sub(r'\*+|\n', ' ', why_m.group(1)).strip() if why_m else ""
    alt_ac, _, _ = parse_aircraft(alt_section, msg_index=msg_index, currency=currency)
    if not alt_ac: return ""
    why_html = f'<div class="alt-airport-why">{_e(why_text)}</div>' if why_text else ""
    return (f'<div class="alt-airport-header"><div class="alt-airport-title">🔄 Alternative departure: {_e(alt_m.group(1).strip().strip("*"))}</div>{why_html}</div>'
            + _make_cards(alt_ac[:3], start_idx=1))

# ── Build chat HTML ───────────────────────────────────────────────────────────
def _is_leg_result(content: str) -> bool:
    return bool(re.search(r'✈\s*Leg\s+\d+', content, re.IGNORECASE))

def _extract_leg_label(content: str, fallback_num: int) -> str:
    m = re.search(r'✈\s*(Leg\s+\d+[^\n]{0,50})', content, re.IGNORECASE)
    return m.group(1).strip().rstrip(':').strip() if m else f"Leg {fallback_num}"

def build_chat_html(messages, is_loading, session_id="", currency="USD", trip_type_selected=False):
    sessions, last_was_result = [], False
    for i, m in enumerate(messages):
        is_result = m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE)
        if m["role"] == "user" and last_was_result and m["content"].strip().lower() not in SHOW_MORE_WORDS:
            last_was_result = False
        if is_result:
            this_is_leg = _is_leg_result(m["content"])
            prev_is_multileg = sessions and last_was_result and sessions[-1].get("is_multileg")
            if not sessions or (not last_was_result and not (this_is_leg and prev_is_multileg)):
                sessions.append({
                    "ridx":[], "amenities":[], "all_ac":[], "dep":"", "arr":"", "date":"",
                    "is_roundtrip": False, "is_multileg": False,
                    "ob_ac":[], "ob_dep":"", "ob_arr":"", "ob_date":"",
                    "rt_ac":[], "rt_dep":"", "rt_arr":"", "rt_date":"",
                    "legs": [],
                })
            sess = sessions[-1]; sess["ridx"].append(i)
            ob_text, rt_text = split_roundtrip(m["content"])
            ml_legs = split_multileg(m["content"]) if rt_text is None else None

            if rt_text is not None:
                sess["is_roundtrip"] = True
                ob_ac, ob_dep, ob_arr = parse_aircraft(ob_text, msg_index=i, currency=currency)
                rt_ac, rt_dep, rt_arr = parse_aircraft(rt_text, msg_index=i, currency=currency)
                sess["ob_ac"].extend(ob_ac); sess["rt_ac"].extend(rt_ac)
                if not sess["ob_dep"] and ob_dep: sess["ob_dep"], sess["ob_arr"] = ob_dep, ob_arr
                if not sess["rt_dep"] and rt_dep: sess["rt_dep"], sess["rt_arr"] = rt_dep, rt_arr
                sess["all_ac"] = sess["ob_ac"] + sess["rt_ac"]
            elif ml_legs:
                sess["is_multileg"] = True
                for leg_num, leg_text in enumerate(ml_legs, 1):
                    leg_ac, leg_dep, leg_arr = parse_aircraft(leg_text, msg_index=i, currency=currency)
                    lm = re.search(r'✈\s*(?:Leg\s+\d+)?[:\s]*([^\n]{0,60})', leg_text)
                    leg_label = f"Leg {leg_num}"
                    if lm:
                        raw_label = lm.group(0).replace("✈", "").strip().rstrip(":")
                        if raw_label: leg_label = raw_label
                    sess["legs"].append({"ac": leg_ac, "dep": leg_dep, "arr": leg_arr, "date": "", "label": leg_label, "num": leg_num})
                    sess["all_ac"].extend(leg_ac)
                if not sess["dep"] and sess["legs"]:
                    sess["dep"] = sess["legs"][0]["dep"]; sess["arr"] = sess["legs"][-1]["arr"]
            elif this_is_leg:
                sess["is_multileg"] = True
                leg_num = len(sess["legs"]) + 1
                leg_label = _extract_leg_label(m["content"], leg_num)
                leg_ac, leg_dep, leg_arr = parse_aircraft(m["content"], msg_index=i, currency=currency)
                sess["legs"].append({"ac": leg_ac, "dep": leg_dep, "arr": leg_arr, "date": "", "label": leg_label, "num": leg_num})
                sess["all_ac"].extend(leg_ac)
                if not sess["dep"] and leg_dep: sess["dep"] = leg_dep
                if leg_arr: sess["arr"] = leg_arr
            else:
                ac, d, a = parse_aircraft(m["content"], msg_index=i, currency=currency)
                sess["all_ac"].extend(ac)
                if not sess["dep"] and d: sess["dep"], sess["arr"] = d, a
            last_was_result = True

    idx_to_session = {i: (sess, si) for si, sess in enumerate(sessions) for i in sess["ridx"]}
    expanded = set()
    for si, sess in enumerate(sessions):
        first = sess["ridx"][0]
        for j in range(first + 1, len(messages)):
            if messages[j]["role"] == "user" and messages[j]["content"].strip().lower() in SHOW_MORE_WORDS:
                expanded.add(si); break
            if messages[j]["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', messages[j]["content"], re.MULTILINE):
                break

    html, rendered_sessions, skip_next_bot = "", set(), False

    html = (
        f'<div class="msg-row">'
        f'<div class="avatar bot">{BOT_SVG}</div>'
        f'<div class="bubble bot">'
        f'Hello! I\'m <strong>JetBot</strong>, your private aviation assistant. '
        f'I can help you find and compare private charter options instantly.<br><br>'
        f'To get started, please select your <strong>trip type</strong> below — '
        f'One-Way, Round Trip, or Multi-Leg.'
        f'</div></div>'
    )
    for i, m in enumerate(messages):
        if m["role"] == "user":
            is_show_more  = m["content"].strip().lower() in SHOW_MORE_WORDS
            applies_to_si = None
            if is_show_more:
                for si, sess in enumerate(sessions):
                    if sess["ridx"][0] < i: applies_to_si = si
                if applies_to_si is not None:
                    rel = sessions[applies_to_si]
                    needs_more = (rel.get("is_roundtrip") and (len(rel["ob_ac"]) > PAGE_SIZE or len(rel["rt_ac"]) > PAGE_SIZE)) \
                                 or (not rel.get("is_roundtrip") and len(rel["all_ac"]) > PAGE_SIZE)
                    if needs_more: skip_next_bot = True
            html += f'<div class="msg-row user"><div class="avatar user">{USER_SVG}</div><div class="bubble user">{H.escape(m["content"])}</div></div>'

            if is_show_more and applies_to_si in expanded and applies_to_si is not None:
                sess = sessions[applies_to_si]; amenities = sess.get("amenities", [])
                first_ridx = sess["ridx"][0]
                if sess.get("is_roundtrip"):
                    more_inner = ""
                    for leg_ac, leg_dep, leg_arr, leg_label, leg_icon, leg_date in [
                        (sess["ob_ac"], sess["ob_dep"], sess["ob_arr"], "Outbound", "✈", sess.get("ob_date","")),
                        (sess["rt_ac"], sess["rt_dep"], sess["rt_arr"], "Return",   "↩", sess.get("rt_date","")),
                    ]:
                        if len(leg_ac) > PAGE_SIZE:
                            remaining = leg_ac[PAGE_SIZE:]; total = len(leg_ac)
                            more_inner += (_leg_header(leg_icon, leg_label, leg_dep, leg_arr)
                                           + _route_strip(leg_dep, leg_arr) + _make_cards(remaining, PAGE_SIZE + 1, msg_index=first_ridx, currency=currency)
                                           + f'<div class="bubble bot" style="margin-top:8px">Showing <strong>{total} of {total}</strong> {leg_label.lower()} aircraft. That\'s all available for this leg.</div>'
                                           + _disclaimer_html())
                    if more_inner:
                        html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{more_inner}</div></div>'
                elif len(sess["all_ac"]) > PAGE_SIZE:
                    remaining, total = sess["all_ac"][PAGE_SIZE:], len(sess["all_ac"])
                    dep, arr, date   = sess["dep"], sess["arr"], sess.get("date","")
                    more_inner = (_route_strip(dep, arr) + _make_cards(remaining, PAGE_SIZE + 1, msg_index=first_ridx, currency=currency)
                                  + f'<div class="bubble bot" style="margin-top:12px">Showing <strong>{total} of {total}</strong> available aircraft. That\'s all available aircraft for this route.</div>'
                                  + _disclaimer_html())
                    html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{more_inner}</div></div>'

        elif i in idx_to_session:
            sess, si = idx_to_session[i]
            if si in rendered_sessions: continue
            rendered_sessions.add(si); skip_next_bot = False

            first_ridx = sess["ridx"][0]; first_content = messages[first_ridx]["content"]; amenities = []

            # ── Scope amenity scan to after the last modify-panel search ──────
            _mod_re = re.compile(r'^Search (?:from|round-trip|multi-leg)', re.IGNORECASE)
            scan_start = 0
            for _j in range(first_ridx):
                if messages[_j]["role"] == "user" and _mod_re.match(messages[_j].get("content", "")):
                    scan_start = _j

            aq_idx = next((j for j in range(scan_start, first_ridx) if messages[j]["role"] == "assistant"
                           and re.search(r'do you require any|any of the following|wifi.*catering|amenity.*question', messages[j]["content"], re.IGNORECASE)), None)
            user_said_no_amenities = False
            if aq_idx is not None:
                for j in range(aq_idx + 1, first_ridx + 1):
                    if messages[j]["role"] == "user":
                        txt = messages[j]["content"].lower().strip()
                        if re.match(r'^(none|no|nope|nah|nil|nothing|no thanks|n/a)[\s.,!]*$', txt) or txt in _AMENITY_NONE:
                            user_said_no_amenities = True; break
                        found = _scan_amenities(messages[j]["content"], from_user=True)
                        if found: amenities = found; break
            if not amenities and not user_said_no_amenities:
                for j in range(scan_start, first_ridx):
                    if messages[j]["role"] == "user":
                        found = _scan_amenities(messages[j]["content"], from_user=True)
                        if found: amenities = found
            if not amenities and not user_said_no_amenities:
                for j in range(scan_start, first_ridx + 1):
                    if messages[j]["role"] == "assistant":
                        if re.search(r'do you require any|any of the following amenities', messages[j]["content"], re.IGNORECASE): continue
                        found = _scan_amenities(messages[j]["content"], from_user=False)
                        if found: amenities = found
            sess["amenities"] = amenities
            pax = _extract_pax(messages, first_ridx)
            amenity_line  = ""  # removed — amenities now shown as chips below results
            prev_user     = next((messages[j]["content"] for j in range(first_ridx - 1, -1, -1) if messages[j]["role"] == "user"), "")
            is_alt_result = bool(_ALT_REQ.search(prev_user.strip()))

            if sess.get("is_multileg"):
                last_ridx = sess["ridx"][-1]
                dates = _all_dates(messages, last_ridx)
                intro_bubble = f'<div class="bubble bot" style="margin-bottom:10px">Here are the top aircraft options for your multi-leg trip:</div>'
                legs_html = ""
                alt_legs = []
                for leg_num, leg in enumerate(sess["legs"]):
                    leg_date = dates[leg_num] if leg_num < len(dates) else ""
                    leg["date"] = leg_date
                    is_last = (leg_num == len(sess["legs"]) - 1)
                    
                    # Extract alternative airport for this specific leg
                    # Safe access to ridx in case multiple legs are in one message
                    ridx = sess["ridx"][leg_num] if leg_num < len(sess["ridx"]) else sess["ridx"][-1]
                    leg_content = messages[ridx]["content"]
                    alt_airport = "" if is_alt_result else _extract_alt_airport(leg_content)
                    
                    # Only count it if it actually refers to this specific leg or 'all'
                    if alt_airport:
                        if "all" in alt_airport.lower() or str(leg_num + 1) in alt_airport:
                            alt_legs.append(leg_num + 1)

                    legs_html += render_leg(leg["ac"], leg["dep"], leg["arr"], session_id, amenities,
                                            leg["label"], "✈", date=leg_date, alt_airport=alt_airport,
                                            msg_index=first_ridx, currency=currency, pax=pax, 
                                            is_last_leg=is_last, is_multileg=True)
                    if not is_last: legs_html += '<hr class="roundtrip-divider">'
                
                # Combined summary for all alternative airports at the end
                alt_bubble = ""
                if alt_legs:
                    if len(alt_legs) == len(sess["legs"]):
                        target = "all legs"
                    elif len(alt_legs) == 1:
                        target = f"leg {alt_legs[0]}"
                    else:
                        target = f"leg {', '.join(map(str, alt_legs[:-1]))} and {alt_legs[-1]}"
                    alt_bubble = (f'<div class="bubble bot" style="margin-top:12px; border-left: 3px solid #b8976a;">'
                                  f'✈ Alternative airports are available for <strong>{target}</strong>. '
                                  f'Reply <strong>show me alternative airport for {target}</strong> to see them.</div>')
                
                html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{intro_bubble}{legs_html}{alt_bubble}</div></div>'

            elif sess.get("is_roundtrip"):
                dates = _all_dates(messages, first_ridx)
                ob_date, rt_date = (dates[0] if dates else ""), (dates[1] if len(dates) > 1 else "")
                sess["ob_date"], sess["rt_date"] = ob_date, rt_date
                alt_airport  = "" if is_alt_result else _extract_alt_airport(first_content)
                intro_bubble = f'<div class="bubble bot" style="margin-bottom:10px">Here are the top aircraft options for your round trip:</div>'
                u_dep, u_arr = _extract_user_cities(messages, first_ridx)
                ob_html = render_leg(sess["ob_ac"], sess["ob_dep"], sess["ob_arr"], session_id, amenities, "Outbound", "✈", date=ob_date, alt_airport="",        msg_index=first_ridx, currency=currency, pax=pax, is_last_leg=False, is_multileg=False, user_dep=u_dep, user_arr=u_arr)
                rt_html = render_leg(sess["rt_ac"], sess["rt_dep"], sess["rt_arr"], session_id, amenities, "Return",   "↩", date=rt_date, alt_airport=alt_airport, msg_index=first_ridx, currency=currency, pax=pax, is_last_leg=True,  is_multileg=False, user_dep=u_arr, user_arr=u_dep)
                html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{intro_bubble + ob_html}<hr class="roundtrip-divider">{rt_html}</div></div>'
            else:
                dates = _all_dates(messages, first_ridx); date = dates[0] if dates else ""; sess["date"] = date
                intro_m = re.match(r'^(.*?)(?=\n*\d+\.\s)', first_content, re.DOTALL)
                raw_intro_lines = [l for l in (intro_m.group(1).splitlines() if intro_m else [])
                                   if not (l.strip() and re.search(r'require.*amenit|amenit.*noted|have been noted|as an amenity|as amenities', l, re.IGNORECASE))]
                intro_html  = f'<div class="bubble bot" style="margin-bottom:10px">{md(clean_intro(chr(10).join(raw_intro_lines)))}</div>'
                alt_airport = "" if is_alt_result else _extract_alt_airport(first_content)
                u_dep, u_arr = _extract_user_cities(messages, first_ridx)
                html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{render_session(sess["all_ac"], sess["dep"], sess["arr"], session_id, intro_html, amenities, date=date, alt_airport=alt_airport, msg_index=first_ridx, currency=currency, pax=pax, user_dep=u_dep, user_arr=u_arr)}{_render_alt_airport(first_content, msg_index=first_ridx, currency=currency)}</div></div>'

        else:
            if skip_next_bot: skip_next_bot = False; continue
            is_ac = bool(re.search(r'have been noted|amenities? noted|your.*amenit.*preference|noted.*amenit|noted[:\s]+wifi|noted[:\s]+cater|noted[:\s]+vip', m["content"], re.IGNORECASE))
            is_amenity_q = bool(re.search(r'do you require any of the following amenities', m["content"], re.IGNORECASE))
            if is_amenity_q:
                inner = _render_amenities_block(m["content"])
                html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0"><div class="bubble bot-compact am-bubble">{inner}</div></div></div>'
            elif not is_ac:
                # suppress "have been noted" bubble — amenities shown as chips under results
                html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0"><div class="bubble bot-compact">{md(m["content"])}</div></div></div>'

    if is_loading:
        html += f'<div class="typing-wrap"><div class="avatar bot">{BOT_SVG}</div><div class="typing-dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div></div>'

    cur_options = "".join(
        f'<option value="{c}" {"selected" if c == currency else ""}>{CURRENCY_SYMBOLS.get(c,c)} {c}</option>'
        for c in CURRENCY_OPTIONS
    )
    return html


# ── Streamlit UI ──────────────────────────────────────────────────────────────
PAGE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300&family=DM+Mono:wght@300;400;500&display=swap');
*,*::before,*::after{box-sizing:border-box}
html,body,[data-testid="stAppViewContainer"],[data-testid="stMain"],[data-testid="stMainBlockContainer"]{background:#ede3d0!important;font-family:'DM Mono',monospace}
[data-testid="stVerticalBlockBorderWrapper"]:has(#modify-panel-anchor){background:#fdf8f2!important;border:1.5px solid rgba(138,106,66,.30)!important;border-radius:10px!important;box-shadow:0 6px 28px rgba(112,84,42,.13)!important}
[data-testid="stAppViewContainer"]{background:linear-gradient(160deg,#f5ede0 0%,#ede3d0 60%,#e0d4bc 100%)!important}
#MainMenu,footer,header,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"]{display:none!important}
.block-container{max-width:1200px!important;margin:0 auto!important;padding:18px 26px 34px!important}
.jet-header-row{margin-bottom:10px}
.jet-logo{font-family:'Cormorant Garamond',serif;font-size:2.1rem;color:#1a2438;letter-spacing:.04em;line-height:1}
.jet-logo span{color:#8a6a42}
.jet-tagline{font-size:.58rem;letter-spacing:.32em;text-transform:uppercase;color:#b0a490;margin-top:5px}
.chat-box{background:linear-gradient(180deg,#fbf7f1 0%, #f8f2e8 100%);border:1.5px solid #b8976a;border-bottom:none;border-radius:12px 12px 0 0;box-shadow:0 10px 30px rgba(112,84,42,.10);overflow:hidden;}
.box-topbar{display:flex;align-items:center;justify-content:space-between;padding:8px 20px;border-bottom:1px solid rgba(184,151,106,.30);background:#f5efe6!important;}
.route-bar{font-size:.63rem;letter-spacing:.18em;color:#a49379;text-transform:uppercase;}
.route-bar strong{color:#6b4f2e;font-weight:500}
.new-flight-btn{font-family:'DM Mono',monospace;font-size:.78rem!important;font-weight:400!important;letter-spacing:.18em;text-transform:uppercase;color:#352515;background:linear-gradient(180deg,#efe4d2 0%, #e6d6c0 100%);border:1.2px solid rgba(138,106,66,.62);border-radius:14px;padding:10px 18px;display:flex;align-items:center;gap:8px;box-shadow:0 3px 10px rgba(112,84,42,.08), inset 0 1px 0 rgba(255,255,255,.45);}
.new-flight-btn:hover{background:linear-gradient(180deg,#eadcc7 0%, #dfccb2 100%);border-color:rgba(138,106,66,.8);box-shadow:0 5px 14px rgba(112,84,42,.12), inset 0 1px 0 rgba(255,255,255,.45);}
#chat-messages{padding:16px 12px 18px;background:linear-gradient(180deg,#fbf8f3 0%, #f8f4ee 100%);min-height:555px;max-height:62vh;overflow-y:auto;scroll-behavior:smooth;}
[data-testid="stMarkdownContainer"]{background:transparent!important;padding:0!important}
[data-testid="stMarkdownContainer"] > div{background:transparent!important}
.element-container{background:transparent!important}
div[data-testid="stVerticalBlock"] > div.element-container{background:transparent!important;border:none!important;box-shadow:none!important}
[data-testid="stMainBlockContainer"]{padding-bottom:40px!important}
[data-testid="stVerticalBlock"]{gap:0!important;row-gap:0!important}
[data-testid="stVerticalBlock"] > div{margin-bottom:0!important;padding-bottom:0!important}
[data-testid="stVerticalBlock"] > div.element-container{margin:0!important;padding:0!important}
.element-container:has(.chat-box){margin-bottom:0!important;padding-bottom:0!important}
.st-key-jetbot_input_bar{background:#f5efe6!important;border:1.5px solid #b8976a!important;border-top:1px solid rgba(184,151,106,.30)!important;border-radius:0 0 12px 12px!important;padding:8px 16px 10px!important;box-shadow:0 10px 30px rgba(112,84,42,.10)!important;margin-top:0!important;position:relative!important;}
.st-key-jetbot_input_bar::before{content:"";display:block;height:.5px;opacity:.55;background:linear-gradient(90deg, rgba(184,151,106,0) 0%, rgba(184,151,106,.38) 50%, rgba(184,151,106,0) 100%);margin:0 0 4px 0;}
.st-key-jetbot_input_bar .trip-row{margin-bottom:6px!important}
.st-key-jetbot_input_bar .input-row{margin-top:0!important}
.stButton>button{background:linear-gradient(180deg,#f7efe3 0%, #f1e5d5 100%)!important;border:1px solid rgba(138,106,66,.28)!important;border-radius:12px!important;color:#3f2d19!important;font-family:'DM Mono',monospace!important;font-size:.74rem!important;letter-spacing:.02em!important;text-transform:uppercase!important;padding:0 10px!important;height:38px!important;line-height:36px!important;white-space:nowrap!important;width:100%!important;transition:all .15s!important;box-shadow:0 2px 8px rgba(112,84,42,.05), inset 0 1px 0 rgba(255,255,255,.45)!important;}
.stButton>button:hover{background:linear-gradient(180deg,#efe3d1 0%, #e8d9c4 100%)!important;border-color:rgba(138,106,66,.48)!important;}
[data-testid="stFormSubmitButton"]{width:100%!important;display:flex!important;align-items:center!important;justify-content:center!important;height:42px!important;margin:0!important;}
[data-testid="stFormSubmitButton"] button{background:#fff!important;border:none!important;border-left:1.5px solid rgba(138,106,66,.42)!important;border-radius:0 8px 8px 0!important;width:48px!important;min-width:48px!important;height:42px!important;padding:0!important;font-size:2rem!important;display:flex!important;align-items:center!important;justify-content:center!important;margin:0!important;box-shadow:none!important;}
[data-testid="stFormSubmitButton"] button p{color:#7a5c32!important;margin:0!important;}
[data-testid="stFormSubmitButton"] button:hover{background:linear-gradient(180deg,#f8f1e6 0%, #f1e5d5 100%)!important;}
[data-testid="stFormSubmitButton"] button:hover p{color:#5a3e1e!important;}
[data-testid="stTextInput"] label{display:none!important}
[data-testid="stTextInput"]{overflow:visible!important}
[data-testid="stTextInput"]>div,[data-testid="stTextInput"]>div>div{border:none!important;background:transparent!important;padding:0!important;box-shadow:none!important;overflow:visible!important}
[data-testid="stTextInput"] input{font-family:'DM Mono',monospace!important;font-size:.84rem!important;color:#1f1a14!important;background:transparent!important;border:none!important;border-radius:0!important;padding:0 14px!important;height:42px!important;line-height:42px!important;box-shadow:none!important;appearance:none!important;-webkit-appearance:none!important;margin:0!important;}
[data-testid="stTextInput"] input:focus{border:none!important;outline:none!important;box-shadow:none!important;}
[data-testid="stTextInput"] input::placeholder{color:#c8b898!important}
[data-testid="stTextInput"] input:disabled{color:#c8b898!important;background:transparent!important;cursor:not-allowed!important}
[data-testid="stSelectbox"]{margin-right:4px!important;margin-bottom:0!important;min-width:108px!important;max-width:130px!important;width:auto!important;}
[data-testid="stSelectbox"] label{display:none!important}
[data-testid="stSelectbox"]>div>div{font-family:'DM Mono',monospace!important;font-size:.72rem!important;color:#3c2a14!important;background:rgba(255,255,255,.92)!important;border:1px solid rgba(138,106,66,.22)!important;border-radius:10px!important;min-height:38px!important;height:auto!important;padding:4px 8px!important;box-shadow:none!important;line-height:1.4!important;white-space:nowrap!important;overflow:visible!important;}
[data-testid="stSelectbox"] [data-baseweb="select"] > div{min-height:38px!important;height:auto!important;padding:4px 8px!important;line-height:1.4!important;align-items:center!important;flex-wrap:nowrap!important;}
[data-testid="stSelectbox"] [data-baseweb="select"] [data-testid="stMarkdownContainer"],
[data-testid="stSelectbox"] [data-baseweb="select"] span,
[data-testid="stSelectbox"] [data-baseweb="select"] div[class*="singleValue"]{white-space:nowrap!important;overflow:visible!important;text-overflow:unset!important;font-size:.72rem!important;}
[data-testid="stSelectbox"] input{font-size:.72rem!important;}
[data-testid="stSelectbox"] svg{width:14px!important;height:14px!important;flex-shrink:0!important;}
[role="option"]{font-family:'DM Mono',monospace!important;font-size:.76rem!important;color:#3c2e1e!important;padding:5px 12px!important;}
[role="option"]:hover,[role="option"][aria-selected="true"]{background:rgba(138,106,66,.10)!important;color:#6b4f2e!important;}
ul[role="listbox"]{background:#faf6f0!important;border:1px solid rgba(138,106,66,.20)!important;border-radius:8px!important;box-shadow:0 4px 16px rgba(100,80,40,.12)!important;}
div[data-testid="stHorizontalBlock"]{align-items:center!important;gap:8px!important;flex-wrap:nowrap!important;overflow:visible!important;}
div[data-testid="stHorizontalBlock"]>div{padding:0!important;display:flex!important;align-items:center!important;min-width:0!important;}
.st-key-jetbot_input_bar [data-testid="column"]{display:flex!important;align-items:center!important;padding:0!important;}
.st-key-jetbot_input_bar [data-testid="column"] > div{width:100%!important;margin:0!important;}
.st-key-jetbot_input_bar [data-testid="column"]:last-child{justify-content:center!important;}
.st-key-jetbot_input_bar [data-testid="column"]:last-child > div{width:auto!important;}
.st-key-jetbot_input_bar form{overflow:visible!important}
.st-key-jetbot_input_bar [data-testid="stForm"],
.st-key-jetbot_input_bar [data-testid="stForm"] > div,
.st-key-jetbot_input_bar [data-testid="stForm"] > div > div{background:transparent!important;border:none!important;padding:0!important;overflow:visible!important;}
.st-key-jetbot_input_bar [data-testid="stForm"] [data-testid="stHorizontalBlock"],
.st-key-jetbot_input_bar form [data-testid="stHorizontalBlock"]{display:flex!important;align-items:center!important;gap:0!important;overflow:hidden!important;background:#fff!important;border:1px solid rgba(200,185,165,.3)!important;border-radius:10px!important;min-height:42px!important;margin-top:6px!important;box-shadow:none!important;transition:box-shadow .2s, border-color .2s!important;}
.st-key-jetbot_input_bar [data-testid="stForm"] [data-testid="stHorizontalBlock"]:focus-within,
.st-key-jetbot_input_bar form [data-testid="stHorizontalBlock"]:focus-within{border-color:rgba(200,185,165,.5)!important;box-shadow:none!important;}
.st-key-jetbot_input_bar form [data-testid="stHorizontalBlock"] > [data-testid="column"]{padding:0!important;margin:0!important;display:flex!important;align-items:center!important;}
.st-key-jetbot_input_bar form [data-testid="stHorizontalBlock"] > [data-testid="column"]:first-child{flex:1 1 auto!important;}
.st-key-jetbot_input_bar form [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child{flex:0 0 auto!important;}
[data-testid="stChatInput"],[data-testid="stBottom"],[data-testid="stBottomBlockContainer"],[data-testid="InputInstructions"]{display:none!important}
small{display:none!important}
.stForm{background:transparent!important;border:none!important;padding:0!important}
.stForm>div{padding:0!important;border:none!important;background:transparent!important}
.st-key-new_flight_hidden{position:fixed!important;top:-9999px!important;left:-9999px!important;width:1px!important;height:1px!important;overflow:hidden!important;opacity:0!important}
.api-note{position:fixed;bottom:3px;left:50%;transform:translateX(-50%);font-size:.72rem;color:#b8a888;letter-spacing:.08em;z-index:9998;pointer-events:none;white-space:nowrap;}
@media(max-width:768px){
  div[data-testid="stHorizontalBlock"]{flex-wrap:wrap!important;gap:6px!important;}
  [data-testid="stSelectbox"]{min-width:100px!important;max-width:none!important;flex:0 0 auto!important;}
  .stButton>button{font-size:.70rem!important;padding:0 8px!important;height:36px!important;line-height:34px!important;}
  .st-key-jetbot_input_bar{padding:6px 10px 8px!important;}
}
@media(max-width:480px){
  [data-testid="stSelectbox"]{min-width:90px!important;}
  .stButton>button{font-size:.67rem!important;}
}
"""

st.markdown(f"<style>{PAGE_CSS}\n{CHAT_CSS}</style>", unsafe_allow_html=True)

# ── Session init ──────────────────────────────────────────────────────────────
for _k, _v in [("is_loading", False), ("messages", []), ("currency", "USD"), ("trip_type_selected", False)]:
    st.session_state.setdefault(_k, _v)
_ensure_prices_store()

if "session_id" not in st.session_state:
    sid = st.query_params.get("sid") or str(uuid.uuid4())
    st.session_state.session_id = sid
    st.query_params["sid"] = sid
if not st.session_state.get("_history_loaded"):
    st.session_state["_history_loaded"] = True
    loaded = _store_load(st.session_state.session_id)
    if loaded:
        st.session_state.messages = loaded
        st.session_state.trip_type_selected = True

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    '<div style="padding:0 0 20px 0">'
    '<div class="jet-logo">Jet<span>Bot</span></div>'
    '<div class="jet-tagline">Private Aviation Intelligence</div>'
    '</div>',
    unsafe_allow_html=True
)

# ── Route topbar ──────────────────────────────────────────────────────────────
msgs = st.session_state.messages
active_route = ""
for idx_r, m in enumerate(reversed(msgs)):
    real_idx = len(msgs) - 1 - idx_r
    if m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE):
        dep = re.search(r'[Dd]eparture[:\s]+([^\n*<]+)', m["content"])
        arr = re.search(r'[Aa]rrival[:\s]+([^\n*<]+)', m["content"])
        if dep and arr:
            dep_airport = dep.group(1).split(",")[0].strip()
            arr_airport = arr.group(1).split(",")[0].strip()
            u_dep, u_arr = _extract_user_cities(msgs, real_idx)
            if u_dep and u_arr and u_dep.lower() != dep_airport.lower():
                active_route = f'{u_dep} → {u_arr}'
            else:
                active_route = f'{dep_airport} → {arr_airport}'
        break

_route_text = f'<strong>Route</strong>&nbsp; {H.escape(active_route)}' if active_route else "Ask me where you'd like to fly"

# ── Render chat ───────────────────────────────────────────────────────────────
n = len(msgs)
result_msgs = sum(1 for m in msgs if m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE))
_inner = build_chat_html(msgs, st.session_state.is_loading, st.session_state.session_id,
                         st.session_state.currency, st.session_state.get("trip_type_selected", False))

st.markdown(
    f'<div class="chat-box">'
    f'<div class="box-topbar"><span class="route-bar">{_route_text}</span>'
    f'<span class="new-flight-placeholder"></span></div>'
    f'<div id="chat-messages">{_inner}</div>'
    f'</div>',
    unsafe_allow_html=True
)

# ── Auto-scroll + sendPrompt bridge ──────────────────────────────────────────
_last_dep, _last_arr, _last_date, _last_pax, _last_amenities = "", "", "", "", ""
for _mi, _m in enumerate(msgs):
    if _m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', _m["content"], re.MULTILINE):
        _ac_list, _d, _a = parse_aircraft(_m["content"])
        if _d: _last_dep, _last_arr = _d, _a
        _dates = _all_dates(msgs, _mi)
        if _dates: _last_date = _dates[0]
        _pax = _extract_pax(msgs, _mi)
        if _pax: _last_pax = str(_pax)
        _am = []
        for _j in range(_mi):
            if msgs[_j]["role"] == "user":
                _found = _scan_amenities(msgs[_j]["content"], from_user=True)
                if _found: _am = _found
        if _am: _last_amenities = ", ".join(_am)

_mod_dep_val  = H.escape(_last_dep)
_mod_arr_val  = H.escape(_last_arr)
_mod_date_val = H.escape(_last_date)
_mod_pax_val  = H.escape(_last_pax)
_mod_am_val   = H.escape(_last_amenities)

components.html(
    f"""
    <script>
    (function() {{
        var pd = window.parent.document;

        if (!pd.getElementById('jetbot-modify-style')) {{
            var style = pd.createElement('style');
            style.id = 'jetbot-modify-style';
            style.textContent = `
                @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600&family=DM+Mono:wght@300;400;500&display=swap');
                #jetbot-modify-overlay{{display:none;position:fixed;inset:0;background:rgba(26,20,12,.50);z-index:99999;backdrop-filter:blur(3px);align-items:center;justify-content:center}}
                #jetbot-modify-overlay.open{{display:flex}}
                #jetbot-modify-panel{{background:linear-gradient(160deg,#fdfaf5 0%,#f8f2e6 100%);border:1.5px solid rgba(138,106,66,.4);border-radius:12px;padding:28px 32px 24px;width:min(480px,92vw);box-shadow:0 24px 60px rgba(26,20,12,.25),0 4px 16px rgba(138,106,66,.12);font-family:'DM Mono',monospace;position:relative}}
                #jetbot-modify-panel h3{{font-family:'Cormorant Garamond',serif;font-size:1.25rem;color:#1a2438;letter-spacing:.04em;margin:0 0 20px;display:flex;align-items:center;gap:8px}}
                .jmod-field{{margin-bottom:14px}}
                .jmod-label{{font-size:.55rem;letter-spacing:.22em;text-transform:uppercase;color:#a49379;display:block;margin-bottom:5px;font-family:'DM Mono',monospace}}
                .jmod-input{{width:100%;background:rgba(255,255,255,.9);border:1px solid rgba(138,106,66,.28);border-radius:6px;padding:9px 12px;font-family:'DM Mono',monospace;font-size:.8rem;color:#2c2318;outline:none;transition:border-color .15s,box-shadow .15s;box-sizing:border-box}}
                .jmod-input:focus{{border-color:rgba(138,106,66,.65);box-shadow:0 0 0 3px rgba(138,106,66,.08)}}
                .jmod-row{{display:flex;gap:12px}}
                .jmod-row .jmod-field{{flex:1}}
                .jmod-actions{{display:flex;gap:10px;margin-top:22px;justify-content:flex-end}}
                .jmod-cancel{{font-family:'DM Mono',monospace;font-size:.65rem;letter-spacing:.14em;text-transform:uppercase;background:transparent;border:1px solid rgba(138,106,66,.25);border-radius:20px;padding:9px 20px;color:#9a8070;cursor:pointer;transition:all .15s}}
                .jmod-cancel:hover{{border-color:rgba(138,106,66,.5);color:#6b4f2e;background:rgba(138,106,66,.04)}}
                .jmod-search{{font-family:'DM Mono',monospace;font-size:.65rem;letter-spacing:.14em;text-transform:uppercase;background:linear-gradient(180deg,#efe4d2,#e6d6c0);border:1.2px solid rgba(138,106,66,.55);border-radius:20px;padding:9px 24px;color:#352515;cursor:pointer;transition:all .15s;box-shadow:0 2px 8px rgba(112,84,42,.07)}}
                .jmod-search:hover{{background:linear-gradient(180deg,#e8dac8,#dccdb8);box-shadow:0 3px 12px rgba(112,84,42,.13)}}
                #jetbot-modify-close{{position:absolute;top:14px;right:16px;background:none;border:none;font-size:1.1rem;color:#b8a898;cursor:pointer;line-height:1;padding:4px 8px;border-radius:4px;font-family:'DM Mono',monospace}}
                #jetbot-modify-close:hover{{color:#6b4f2e;background:rgba(138,106,66,.07)}}
                .jmod-required-note{{font-size:.6rem;color:#c08040;margin-top:6px;display:none;font-family:'DM Mono',monospace}}
            `;
            pd.head.appendChild(style);
        }}

        if (!pd.getElementById('jetbot-modify-overlay')) {{
            var div = pd.createElement('div');
            div.innerHTML = `
                <div id="jetbot-modify-overlay">
                  <div id="jetbot-modify-panel">
                    <button id="jetbot-modify-close">✕</button>
                    <h3>✏ Modify Search</h3>
                    <div class="jmod-row">
                      <div class="jmod-field">
                        <label class="jmod-label">From</label>
                        <input class="jmod-input" id="jmod-dep" placeholder="Departure city" />
                      </div>
                      <div class="jmod-field">
                        <label class="jmod-label">To</label>
                        <input class="jmod-input" id="jmod-arr" placeholder="Destination city" />
                      </div>
                    </div>
                    <div class="jmod-row">
                      <div class="jmod-field">
                        <label class="jmod-label">Date</label>
                        <input class="jmod-input" id="jmod-date" placeholder="e.g. 25th March" />
                      </div>
                      <div class="jmod-field">
                        <label class="jmod-label">Passengers</label>
                        <input class="jmod-input" id="jmod-pax" placeholder="e.g. 2" type="number" min="1" max="19" />
                      </div>
                    </div>
                    <div class="jmod-field">
                      <label class="jmod-label">Amenities <span style="opacity:.5;font-size:.5rem">(optional)</span></label>
                      <input class="jmod-input" id="jmod-am" placeholder="e.g. WiFi, Catering — or leave blank" />
                    </div>
                    <div class="jmod-actions">
                      <button class="jmod-cancel" id="jmod-cancel-btn">Cancel</button>
                      <button class="jmod-search" id="jmod-search-btn">Search ➤</button>
                    </div>
                  </div>
                </div>
            `;
            pd.body.appendChild(div.firstElementChild);

            pd.getElementById('jetbot-modify-close').onclick = function() {{ closeModifyPanel(); }};
            pd.getElementById('jmod-cancel-btn').onclick     = function() {{ closeModifyPanel(); }};
            pd.getElementById('jmod-search-btn').onclick     = function() {{ submitModify(); }};
            pd.getElementById('jetbot-modify-overlay').addEventListener('click', function(e) {{
                if (e.target === pd.getElementById('jetbot-modify-overlay')) closeModifyPanel();
            }});
            pd.addEventListener('keydown', function(e) {{
                if (e.key === 'Escape') closeModifyPanel();
            }});
        }}

        function setVal(id, val) {{ var el = pd.getElementById(id); if (el && val) el.value = val; }}
        setVal('jmod-dep',  '{_mod_dep_val}');
        setVal('jmod-arr',  '{_mod_arr_val}');
        setVal('jmod-date', '{_mod_date_val}');
        setVal('jmod-pax',  '{_mod_pax_val}');
        setVal('jmod-am',   '{_mod_am_val}');

        window.parent.openModifyPanel = function() {{
            var overlay = pd.getElementById('jetbot-modify-overlay');
            if (overlay) overlay.classList.add('open');
        }};
        function closeModifyPanel() {{
            var overlay = pd.getElementById('jetbot-modify-overlay');
            if (overlay) overlay.classList.remove('open');
        }}
        function submitModify() {{
            var dep  = (pd.getElementById('jmod-dep').value  || '').trim();
            var arr  = (pd.getElementById('jmod-arr').value  || '').trim();
            var date = (pd.getElementById('jmod-date').value || '').trim();
            var pax  = (pd.getElementById('jmod-pax').value  || '').trim();
            var am   = (pd.getElementById('jmod-am').value   || '').trim();
            if (!dep || !arr || !date || !pax) {{ alert('Please fill in From, To, Date, and Passengers.'); return; }}
            var msg = 'Search from ' + dep + ' to ' + arr + ' on ' + date
                    + ', ' + pax + ' passenger' + (pax === '1' ? '' : 's')
                    + (am ? ', amenities: ' + am : ', no amenities');
            closeModifyPanel();
            sendPromptToStreamlit(msg);
        }}
        function sendPromptToStreamlit(text) {{
            var input = pd.querySelector('[data-testid="stForm"] input[type="text"]:not(:disabled)');
            if (!input) return;
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(input, text);
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            setTimeout(function() {{
                var btn = pd.querySelector('[data-testid="stForm"] button[kind="primaryFormSubmit"]')
                       || pd.querySelector('[data-testid="stForm"] button[type="submit"]')
                       || pd.querySelector('[data-testid="stFormSubmitButton"] button');
                if (btn) btn.click();
            }}, 80);
        }}
        window.parent.sendPrompt = function(text) {{ sendPromptToStreamlit(text); }};

        function getChat() {{ try {{ return pd.getElementById('chat-messages'); }} catch(e) {{ return null; }} }}
        function scrollToBottom(el) {{ if (el) el.scrollTop = el.scrollHeight; }}
        var chatEl = getChat();
        scrollToBottom(chatEl);
        function observeChat(el) {{
            if (!el) return;
            new MutationObserver(function() {{ scrollToBottom(el); }}).observe(el, {{ childList: true, subtree: true }});
        }}
        if (chatEl) {{
            observeChat(chatEl);
        }} else {{
            var tries = 0;
            var iv = setInterval(function() {{
                var found = getChat();
                if (found) {{ scrollToBottom(found); observeChat(found); clearInterval(iv); }}
                if (++tries > 20) clearInterval(iv);
            }}, 100);
        }}
    }})();
    </script>
    """,
    height=0,
)

# ── Input bar ─────────────────────────────────────────────────────────────────
with st.container(key="jetbot_input_bar"):
    if not st.session_state.get("trip_type_selected", False):
        _b1, _b2, _b3, _gap, _cur = st.columns([1.15, 1.15, 1.15, 4.55, 1.2], vertical_alignment="center")
        with _b1:
            if st.button("✈ ONE-WAY", key="btn_ow", use_container_width=True):
                st.session_state["_pending_trip"] = "one-way"
                st.session_state["_last_trip_type"] = "one-way"
                st.session_state.trip_type_selected = True; st.rerun()
        with _b2:
            if st.button("↻ ROUND TRIP", key="btn_rt", use_container_width=True):
                st.session_state["_pending_trip"] = "round-trip"
                st.session_state["_last_trip_type"] = "round-trip"
                st.session_state.trip_type_selected = True; st.rerun()
        with _b3:
            if st.button("🗺 MULTI-LEG", key="btn_ml", use_container_width=True):
                st.session_state["_pending_trip"] = "multi-leg"
                st.session_state["_last_trip_type"] = "multi-leg"
                st.session_state.trip_type_selected = True; st.rerun()
        with _gap: st.empty()
        with _cur:
            _ci = CURRENCY_OPTIONS.index(st.session_state.currency) if st.session_state.currency in CURRENCY_OPTIONS else 0
            _sc = st.selectbox("currency", CURRENCY_OPTIONS, index=_ci, key="cur_main",
                               format_func=lambda c: f"{c} ({CURRENCY_SYMBOLS.get(c,c)})", label_visibility="hidden")
            if _sc != st.session_state.currency:
                st.session_state.currency = _sc; st.rerun()
    else:
        _has_results = any(
            m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE)
            for m in msgs
        )
        if _has_results:
            _gap, _ms, _nf, _cur = st.columns([5.2, 1.4, 1.4, 1.4], vertical_alignment="center")
        else:
            _gap, _nf, _cur = st.columns([6.6, 1.4, 1.4], vertical_alignment="center")
            _ms = None
        with _gap: st.empty()
        if _has_results and _ms is not None:
            with _ms:
                if st.button("~ MODIFY", key="modify_search_btn", use_container_width=True):
                    st.session_state["_show_modify_panel"] = True; st.rerun()
        with _nf:
            if st.button("↺ NEW FLIGHT", key="new_flight_inline", use_container_width=True):
                _old_sid = st.session_state.session_id
                _store_clear(_old_sid)
                _new_sid = str(uuid.uuid4())
                _keys_to_clear = [k for k in st.session_state.keys()
                                   if k.startswith("_mod") or k.startswith("leg_")
                                   or k in ("_show_modify_panel","_pending_trip","_last_trip_type")]
                for _k in _keys_to_clear: st.session_state.pop(_k, None)
                st.session_state.update({
                    "session_id": _new_sid, "messages": [], "is_loading": False,
                    "_history_loaded": True, "prices_store": {}, "trip_type_selected": False,
                    "currency": st.session_state.get("currency", "USD"),
                })
                st.query_params["sid"] = _new_sid; st.rerun()
        with _cur:
            _ci = CURRENCY_OPTIONS.index(st.session_state.currency) if st.session_state.currency in CURRENCY_OPTIONS else 0
            _sc = st.selectbox("currency", CURRENCY_OPTIONS, index=_ci, key="cur_main_hidden",
                               format_func=lambda c: f"{c} ({CURRENCY_SYMBOLS.get(c,c)})", label_visibility="hidden")
            if _sc != st.session_state.currency:
                st.session_state.currency = _sc; st.rerun()

    with st.form(key="chat_form", clear_on_submit=True):
        _trip_chosen = st.session_state.get("trip_type_selected", False)
        _ic, _sc_col = st.columns([24, 1], gap="small", vertical_alignment="center")
        with _ic:
            _user_input = st.text_input(
                "msg",
                placeholder="Select a trip type above to begin…" if not _trip_chosen else "Message JetBot…",
                label_visibility="hidden",
                disabled=not _trip_chosen,
            )
        with _sc_col:
            _submitted = st.form_submit_button("➤", disabled=not _trip_chosen, use_container_width=True)

st.markdown('<div class="api-note">Powered by JetBot · Private Aviation Intelligence</div>', unsafe_allow_html=True)


# ── Modify Search Panel ───────────────────────────────────────────────────────
def _run_modify_prefill():
    _detected_trip = "one-way"
    _pending = st.session_state.get("_last_trip_type", "")
    if _pending:
        _detected_trip = _pending
    else:
        for _m in msgs:
            if _m["role"] != "user": continue
            _c = (_m.get("resolved") or _m.get("content","")).lower().strip()
            if "multi" in _c and "leg" in _c: _detected_trip = "multi-leg"; break
            if re.search(r'\bround[\s\-]?trip\b', _c): _detected_trip = "round-trip"
    st.session_state["_mod_trip_type"] = _detected_trip

    _ROUTE_RE1 = re.compile(
        r'\bfrom\s+([A-Za-z][A-Za-z\s\-]{1,35}?)\s+to\s+([A-Za-z][A-Za-z\s\-]{1,35}?)(?=\s+on\b|\s+for\b|\s*,|\s*\.|$|\s+\d)',
        re.IGNORECASE)
    _ROUTE_RE2 = re.compile(
        r'\b([A-Za-z][A-Za-z\s\-]{1,30}?)\s+to\s+([A-Za-z][A-Za-z\s\-]{1,30}?)(?=\s+on\b|\s+for\b|\s*,|\s*\.|$|\s+\d)',
        re.IGNORECASE)
    _NOISE = {"one","want","need","fly","travel","going","get","way","how","what","search","multi","leg"}
    _MON   = r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?'
    _NL_RE2 = re.compile(
        r'\b\d{1,2}(?:st|nd|rd|th)?[\s\-]+(?:of[\s\-]+)?(?:' + _MON + r')(?:[\s\-]+\d{4})?\b'
        r'|\b(?:' + _MON + r')[\s\-]+\d{1,2}(?:st|nd|rd|th)?(?:,?[\s\-]+\d{4})?\b', re.IGNORECASE)

    _last_result_idx = max(
        (_mi for _mi, _m in enumerate(msgs)
         if _m["role"] == "assistant" and re.search(r'^\.\d+\.\s+\*\*', _m["content"], re.MULTILINE)),
        default=len(msgs)
    )
    _p = _extract_pax(msgs, _last_result_idx)
    _scanned_am = []
    for _j in range(_last_result_idx):
        if msgs[_j]["role"] == "user":
            _f = _scan_amenities(msgs[_j]["content"], from_user=True)
            if _f: _scanned_am = _f
    if _p: st.session_state["_mod_last_pax"] = str(_p)
    if _scanned_am: st.session_state["_mod_last_am"] = ", ".join(_scanned_am)

    if _detected_trip == "multi-leg":
        _ml_routes = []
        for _m in msgs:
            if _m["role"] != "user": continue
            _txt = _m.get("content","").strip()
            _rm = _ROUTE_RE1.search(_txt) or _ROUTE_RE2.search(_txt)
            if _rm:
                _d, _a = _rm.group(1).strip(), _rm.group(2).strip()
                if _d.lower().split()[-1] not in _NOISE:
                    _ml_routes.append({"from": _d, "to": _a})
        _all_u_dates = []
        for _m in msgs:
            if _m["role"] != "user": continue
            _txt = _m.get("content","")
            for _hit in (_NL_RE2.findall(_txt) or re.findall(r'\d{4}-\d{2}-\d{2}', _txt)):
                _ds = _hit if isinstance(_hit, str) else _hit[0]
                if _ds and _ds not in _all_u_dates: _all_u_dates.append(_ds)
        _legs_pf = [{"from": r["from"], "to": r["to"], "date": _all_u_dates[i] if i < len(_all_u_dates) else ""} for i, r in enumerate(_ml_routes)]
        if not _legs_pf: _legs_pf = [{"from":"","to":"","date":""},{"from":"","to":"","date":""}]
        if "_mod_legs" not in st.session_state: st.session_state["_mod_legs"] = _legs_pf
    else:
        _user_dep, _user_arr = "", ""
        for _m in msgs:
            if _m["role"] != "user": continue
            _txt = (_m.get("resolved") or _m.get("content","")).strip()
            _rm = _ROUTE_RE1.search(_txt) or _ROUTE_RE2.search(_txt)
            if _rm:
                _d, _a = _rm.group(1).strip(), _rm.group(2).strip()
                if _d.lower().split()[-1] not in _NOISE: _user_dep, _user_arr = _d, _a
        _all_dates2 = []
        for _m in msgs:
            if _m["role"] != "user": continue
            _txt = _m.get("content","")
            for _hit in (_NL_RE2.findall(_txt) or re.findall(r'\d{4}-\d{2}-\d{2}', _txt)):
                _ds = _hit if isinstance(_hit, str) else _hit[0]
                if _ds and _ds not in _all_dates2: _all_dates2.append(_ds)
        if _user_dep: st.session_state.setdefault("_mod_last_dep", _user_dep)
        if _user_arr: st.session_state.setdefault("_mod_last_arr", _user_arr)
        if _all_dates2: st.session_state.setdefault("_mod_last_date", _all_dates2[0])
        if len(_all_dates2) > 1: st.session_state.setdefault("_mod_last_date2", _all_dates2[1])


if st.session_state.get("_show_modify_panel"):
    _run_modify_prefill()
    _trip    = st.session_state.get("_mod_trip_type", "one-way")
    _pax_v   = st.session_state.get("_mod_last_pax", "2")
    _am_v    = st.session_state.get("_mod_last_am",  "")
    _pax_int = int(_pax_v) if str(_pax_v).isdigit() else 2

    st.markdown('<div id="modify-panel-anchor"></div>', unsafe_allow_html=True)
    components.html("<script>setTimeout(function(){var el=window.parent.document.getElementById('modify-panel-anchor');if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},120);</script>", height=0)

    with st.container(border=True):
        st.markdown('<div id="modify-panel-anchor"></div>', unsafe_allow_html=True)
        st.markdown("#### ~ Modify Search")

        if _trip == "multi-leg":
            legs = st.session_state.get("_mod_legs", [{"from":"","to":"","date":""},{"from":"","to":"","date":""}])
            _remove_idx, _do_add = None, False

            for _li, _leg in enumerate(legs):
                _lc1, _lc2, _lc3, _lc4 = st.columns([3, 3, 3, 1])
                with _lc1: st.text_input(f"Leg {_li+1} · From", value=_leg["from"], placeholder="Departure", key=f"leg_{_li}_from")
                with _lc2: st.text_input(f"Leg {_li+1} · To",   value=_leg["to"],   placeholder="Destination", key=f"leg_{_li}_to")
                with _lc3: st.text_input(f"Leg {_li+1} · Date", value=_leg["date"], placeholder="e.g. 25 May", key=f"leg_{_li}_date")
                with _lc4:
                    st.write(""); st.write("")
                    if len(legs) > 2:
                        if st.button("✕", key=f"rm_{_li}", help="Remove leg"): _remove_idx = _li

            _add_c, _ = st.columns([1, 5])
            with _add_c:
                if st.button("+ Add Leg", use_container_width=True): _do_add = True

            for _li in range(len(legs)):
                legs[_li]["from"] = st.session_state.get(f"leg_{_li}_from", legs[_li]["from"])
                legs[_li]["to"]   = st.session_state.get(f"leg_{_li}_to",   legs[_li]["to"])
                legs[_li]["date"] = st.session_state.get(f"leg_{_li}_date", legs[_li]["date"])
            st.session_state["_mod_legs"] = legs

            def _clear_leg_keys():
                for _k in list(st.session_state.keys()):
                    if re.match(r'leg_\d+_(from|to|date)', _k): del st.session_state[_k]

            if _remove_idx is not None:
                legs.pop(_remove_idx); st.session_state["_mod_legs"] = legs
                _clear_leg_keys(); st.rerun()
            if _do_add:
                legs.append({"from": legs[-1]["to"] if legs else "", "to": "", "date": ""})
                st.session_state["_mod_legs"] = legs
                _clear_leg_keys(); st.rerun()

            st.divider()
            _pc, _ac = st.columns(2)
            with _pc: _pax = st.number_input("Passengers", min_value=1, max_value=19, value=_pax_int, key="mod_pax")
            with _ac: _am  = st.text_input("Amenities (optional)", value=_am_v, placeholder="e.g. WiFi, Catering", key="mod_am")

            _sb, _cb = st.columns(2)
            with _cb:
                if st.button("Cancel", use_container_width=True, key="mod_cancel"):
                    st.session_state.pop("_show_modify_panel", None); st.session_state.pop("_mod_legs", None)
                    _clear_leg_keys(); st.rerun()
            with _sb:
                if st.button("Search ➤", use_container_width=True, type="primary", key="mod_search"):
                    _valid = all(l["from"] and l["to"] and l["date"] for l in legs)
                    if not _valid or len(legs) < 2:
                        st.error("Please fill in From, To and Date for each leg. Minimum 2 legs.")
                    else:
                        _leg_parts = ", ".join(f"{l['from']} to {l['to']} on {l['date']}" for l in legs)
                        _am_part   = f", amenities: {_am}" if _am.strip() else ", no amenities"
                        _pax_word  = "passenger" if _pax == 1 else "passengers"
                        _msg = f"Search multi-leg: {_leg_parts}, {_pax} {_pax_word}{_am_part}"
                        st.session_state.pop("_show_modify_panel", None); st.session_state.pop("_mod_legs", None)
                        try:
                            from agent.core import _resolve_relative_date as _rrd
                            _msg_resolved = _rrd(_msg)
                        except Exception:
                            _msg_resolved = _msg
                        _mod_msg = {"role": "user", "content": _msg, "resolved": _msg_resolved}
                        st.session_state.messages.append(_mod_msg)
                        _store_append(st.session_state.session_id, _mod_msg)
                        st.session_state.is_loading = True; st.rerun()

        else:
            _dep_v   = st.session_state.get("_mod_last_dep",  "")
            _arr_v   = st.session_state.get("_mod_last_arr",  "")
            _date_v  = st.session_state.get("_mod_last_date", "")
            _date2_v = st.session_state.get("_mod_last_date2","")

            _c1, _c2 = st.columns(2)
            with _c1: _dep = st.text_input("From", value=_dep_v, placeholder="Departure city", key="mod_dep")
            with _c2: _arr = st.text_input("To",   value=_arr_v, placeholder="Destination city", key="mod_arr")

            if _trip == "round-trip":
                _c3, _c4 = st.columns(2)
                with _c3: _date  = st.text_input("Outbound Date", value=_date_v,  placeholder="e.g. 25th March", key="mod_date")
                with _c4: _date2 = st.text_input("Return Date",   value=_date2_v, placeholder="e.g. 30th March", key="mod_date2")
                _c5, _c6 = st.columns(2)
                with _c5: _pax = st.number_input("Passengers", min_value=1, max_value=19, value=_pax_int, key="mod_pax")
                with _c6: _am  = st.text_input("Amenities (optional)", value=_am_v, placeholder="e.g. WiFi, Catering", key="mod_am")
            else:
                _date2 = ""
                _c3, _c4 = st.columns(2)
                with _c3: _date = st.text_input("Date", value=_date_v, placeholder="e.g. 25th March", key="mod_date")
                with _c4: _pax  = st.number_input("Passengers", min_value=1, max_value=19, value=_pax_int, key="mod_pax")
                _am = st.text_input("Amenities (optional)", value=_am_v, placeholder="e.g. WiFi, Catering — or leave blank", key="mod_am")

            _sb, _cb = st.columns(2)
            with _cb:
                if st.button("Cancel", use_container_width=True, key="mod_cancel"):
                    for _k in ["_show_modify_panel","_mod_last_dep","_mod_last_arr","_mod_last_date","_mod_last_date2"]:
                        st.session_state.pop(_k, None)
                    st.rerun()
            with _sb:
                if st.button("Search ➤", use_container_width=True, type="primary", key="mod_search"):
                    _err = not _dep or not _arr or not _date or (_trip == "round-trip" and not _date2)
                    if _err:
                        st.error("Please fill in all required fields.")
                    else:
                        _am_part  = f", amenities: {_am}" if _am.strip() else ", no amenities"
                        _pax_word = "passenger" if _pax == 1 else "passengers"
                        if _trip == "round-trip":
                            _msg = (f"Search round-trip from {_dep} to {_arr}, "
                                    f"outbound {_date}, return {_date2}, {_pax} {_pax_word}{_am_part}")
                        else:
                            _msg = f"Search from {_dep} to {_arr} on {_date}, {_pax} {_pax_word}{_am_part}"
                        st.session_state.pop("_show_modify_panel", None)
                        try:
                            from agent.core import _resolve_relative_date as _rrd
                            _msg_resolved = _rrd(_msg)
                        except Exception:
                            _msg_resolved = _msg
                        _mod_msg = {"role": "user", "content": _msg, "resolved": _msg_resolved}
                        st.session_state.messages.append(_mod_msg)
                        _store_append(st.session_state.session_id, _mod_msg)
                        st.session_state.is_loading = True; st.rerun()


# ── Handle pending trip button click ─────────────────────────────────────────
if st.session_state.get("_pending_trip"):
    _trip_text = st.session_state.pop("_pending_trip")
    st.session_state.trip_type_selected = True
    _enriched = (
        f"{_trip_text}\n\n"
        f"[System note: User selected trip type via button: {_trip_text}. "
        f"Trip type is CONFIRMED — do NOT ask for it again. "
        f"Now execute Step 2 ONLY: ask BOTH departure and destination cities in ONE single message "
        f"using EXACTLY this phrasing: 'Please share your departure and arrival locations.' "
        f"Do NOT echo or confirm the trip type. Do NOT ask pax, date, or amenities yet. "
        f"Do NOT call search_flights yet.]"
    ) if _trip_text != "multi-leg" else (
        f"{_trip_text}\n\n"
        f"[System note: User selected MULTI-LEG trip via button. Trip type is CONFIRMED — do NOT ask for it again. "
        f"Follow the MULTI-LEG TRIP — MANDATORY STEPS (STEP A through STEP F) exactly as defined in your instructions. "
        f"Begin immediately at STEP A: output 'Let\\'s start with **Leg 1**.' then on the next line ask BOTH "
        f"departure and destination in ONE message: 'Please share your departure and arrival locations for this leg.' "
        f"Do NOT pre-empt or combine steps. Do NOT ask pax, date, or amenities yet. Do NOT call search_flights yet.]"
    )
    _msg = {"role": "user", "content": _trip_text, "resolved": _enriched}
    st.session_state.messages.append(_msg)
    _store_append(st.session_state.session_id, _msg)
    st.session_state.is_loading = True; st.rerun()

# ── Handle form submit ────────────────────────────────────────────────────────
if _submitted and _user_input and _user_input.strip():
    text = _user_input.strip()
    if not st.session_state.trip_type_selected:
        st.session_state.trip_type_selected = True
    try:
        from agent.core import _resolve_relative_date as _rrd
        text_resolved = _rrd(text)
    except Exception:
        text_resolved = text
    msg = {"role": "user", "content": text, "resolved": text_resolved}
    st.session_state.messages.append(msg)
    _store_append(st.session_state.session_id, msg)
    st.session_state.is_loading = not (text.lower() in SHOW_MORE_WORDS and has_pending_more(st.session_state.messages[:-1]))
    st.rerun()

# ── Stream backend response ───────────────────────────────────────────────────
if st.session_state.is_loading:
    last_msg = next((m for m in reversed(msgs) if m["role"] == "user"), None)
    last = (last_msg.get("resolved") or last_msg["content"]) if last_msg else None
    if last:
        reply_parts = []

        def _token_generator():
            try:
                with requests.post(
                    BACKEND,
                    json={"message": last, "session_id": st.session_state.session_id,
                          "currency": st.session_state.currency,
                          "history": [{"role": m["role"], "content": m.get("resolved") or m["content"]} for m in st.session_state.messages[:-1]]},
                    stream=True, timeout=(10, 240)
                ) as resp:
                    if resp.status_code != 200:
                        chunk = f"⚠ Backend error {resp.status_code}"; reply_parts.append(chunk); yield chunk; return
                    for raw in resp.iter_lines():
                        if not raw: continue
                        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                        if not line.startswith("data: "): continue
                        p = line[6:]
                        if p == "[DONE]": break
                        if p.startswith("[SESSION:"): st.session_state.session_id = p[9:-1]; continue
                        if p.startswith("[PRICES:"):
                            try:
                                prices_map = json.loads(p[8:-1])
                                _store_prices_for_message(len(st.session_state.messages), prices_map)
                            except Exception: pass
                            continue
                        token = p.replace("\\n", "\n"); reply_parts.append(token); yield token
            except requests.exceptions.ConnectionError:
                chunk = "⚠ Cannot connect to backend. Run: uvicorn main:app --reload"; reply_parts.append(chunk); yield chunk
            except requests.exceptions.Timeout:
                chunk = "⚠ Search timed out — Avinode may be slow. Please try again."; reply_parts.append(chunk); yield chunk
            except Exception as e:
                chunk = f"⚠ Error: {str(e)}"; reply_parts.append(chunk); yield chunk

        with st.spinner(""):
            for token in _token_generator(): pass

        reply = "".join(reply_parts) or "⚠ Empty response from backend."
        assistant_msg = {"role": "assistant", "content": reply}
        st.session_state.messages.append(assistant_msg)
        _store_append(st.session_state.session_id, assistant_msg)
        st.session_state.is_loading = False; st.rerun()