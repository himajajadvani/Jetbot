import streamlit as st
import streamlit.components.v1 as components
import requests, uuid, re, json, html as H

try:
    from PIL import Image
    st.set_page_config(page_title="JetBot", page_icon=Image.open("favicon.png"), layout="wide")
except:
    st.set_page_config(page_title="JetBot", page_icon="✈", layout="wide")

BACKEND  = "http://localhost:8000/chat/stream"
PAGE_SIZE = 5
SHOW_MORE_WORDS = {"yes","more","show more","next","continue","yep","yeah","sure","ok","okay","show all","show remaining"}
BOT_SVG  = '<svg width="22" height="22" viewBox="0 0 24 24"><path d="M21 16v-2l-8-5V3.5C13 2.67 12.33 2 11.5 2S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" fill="#8a6a42"/></svg>'
USER_SVG = '<svg width="20" height="20" viewBox="0 0 24 24"><defs><radialGradient id="ug" cx="50%" cy="35%" r="60%"><stop offset="0%" stop-color="#7a5a32"/><stop offset="100%" stop-color="#4a3018"/></radialGradient></defs><circle cx="12" cy="8" r="3.8" fill="url(#ug)"/><path d="M4.5 21c0-4.1 3.4-7.2 7.5-7.2s7.5 3.1 7.5 7.2" fill="url(#ug)"/></svg>'

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

# ── Helpers ───────────────────────────────────────────────────────────────────
def _e(s): return H.escape(str(s))

def md(text):
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', H.escape(text))
    t = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2" target="_blank">\1</a>', t)
    return t.replace('\n', '<br>')

def field(body, pat):
    m = re.search(pat, body)
    return re.sub(r'\*+|<[^>]+>', '', m.group(1)).strip() if m else ""

def clean_intro(text):
    t = text.strip()
    for bad in [r'[Uu]nfortunately[^\n.]+[.\n]?', r'[Nn]one of the[^\n.]+[.\n]?',
                r'[Hh]ere are.{0,60}without.{0,40}filter[^\n.]*[.\n]?', r'\n+\d+\.[^\n]+',
                r'and will be confirmed at booking\.?', r'[Nn]ow[,\s]+I will search[^\n.]+[.\n]?',
                r'[Ll]et me search[^\n.]+[.\n]?', r'[Ss]earching for[^\n.]+[.\n]?',
                r'[Ii] will now search[^\n.]+[.\n]?', r'[Ii] will search[^\n.]+[.\n]?',
                r'[Hh]ere are the private jet[^\n.]+[.\n]?', r'[Yy]our amenity[^\n.]+[.\n]?',
                r'[Yy]our.*amenit.*preference[^\n.]+[.\n]?', r'[Aa]menit.*have been noted[^\n.]+[.\n]?']:
        t = re.sub(bad, '', t).strip()
    t = re.sub(r'[Hh]ere are.{0,80}(?:options|jets?|aircraft)[^\n]*\n?', '', t).strip()
    return "Here are the top aircraft options for your route:" + (f"\n\n{t}" if t else "")

def parse_aircraft(content):
    content = re.sub(r'^[⭐💡][^\n]*\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'^─+$', '', content, flags=re.MULTILINE)
    blocks = re.findall(r'\d+\.\s+\*\*(.+?)\*\*(.*?)(?=\n\d+\.\s+\*\*|\Z)', content, re.DOTALL)
    out, dep, arr = [], "", ""
    for name, body in blocks:
        p  = re.sub(r'<br>.*', '', field(body, r'[Pp]rice[:\s]+([^\n*<br]+)')).strip() or "—"
        ft = re.sub(r'\*+', '', field(body, r'[Ff]light\s*[Tt]ime[:\s]+([^\n*<]+)')).strip() or "N/A"
        if re.search(r'than jets|longer|slower|turboprop', ft, re.IGNORECASE): ft = "N/A"
        cap = field(body, r'[Cc]apacity[:\s]+([^\n*<]+)') or "—"
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
def summary_card(aircraft, dep, arr, amenities=None, label="", date=""):
    al = amenities or []
    lines = [f"✈ {label} Summary" if label else "✈ Private Jet Summary", "", f"Route: {dep or '—'} → {arr or '—'}"]
    if date: lines.append(f"Date:  {date}")
    if al:   lines.append(f"Amenities: {', '.join(al)}")
    lines += [""] + [f"{i}. {ac['name']}  —  {ac['price']}  ·  {ac['ft']}" for i, ac in enumerate(aircraft, 1)] + ["", "Searched via JetBot · Private Aviation Intelligence"]
    def code(s): return f' <span style="color:#b8a898">({m.group(1)})</span>' if (m := re.search(r'\(([^)]+)\)\s*$', s)) else ""
    def city(s): return _e(s.split("(")[0].strip()) if s else "—"
    route_row    = f'<div class="sc-meta"><span class="sc-meta-label">Route</span><span class="sc-route-val">{city(dep)}{code(dep)}</span><span class="sc-sep">→</span><span class="sc-route-val">{city(arr)}{code(arr)}</span></div>' if (dep or arr) else ""
    date_row     = f'<div class="sc-date-row"><span class="sc-date-label">Date</span><span class="sc-date-val">{_e(date)}</span></div>' if date else ""
    amenity_html = f'<div class="sc-amenity-row"><span class="sc-amenity-label">Noted Amenities</span><span class="sc-amenity-check">✓</span><span class="sc-amenity-val">{_e(", ".join(al))}</span></div>' if al else ""
    rows         = "".join(f'<div class="sc-row"><span class="sc-idx">{i:02d}</span><span class="sc-acname">{_e(ac["name"])}</span><span class="sc-price">{_e(ac["price"])}</span><span class="sc-ft">{_e(ac["ft"])}</span></div>' for i, ac in enumerate(aircraft, 1))
    data_text    = _e(json.dumps("\n".join(lines)))
    card_label   = _e(label) if label else "Flight"
    return (f'<div class="summary-card"><div class="sc-header"><div class="sc-title"><svg width="14" height="14" viewBox="0 0 24 24"><path d="M21 16v-2l-8-5V3.5C13 2.67 12.33 2 11.5 2S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" fill="#8a6a42"/></svg>{card_label} Summary</div>'
            f'<button class="copy-btn" data-text="{data_text}" onclick="var t=JSON.parse(this.getAttribute(\'data-text\'));navigator.clipboard.writeText(t).then(()=>{{this.textContent=\'✓ Copied!\';this.classList.add(\'copied\');setTimeout(()=>{{this.textContent=\'Copy\';this.classList.remove(\'copied\')}},2500)}});">Copy</button>'
            f'</div>{route_row}{date_row}{amenity_html}<div class="sc-rows">{rows}</div><div class="sc-footer">Searched via JetBot · Private Aviation Intelligence</div></div>')

def _make_cards(aircraft_list, start_idx=1):
    def _parse_price(p):
        try: return float(re.sub(r'[^\d.]', '', p))
        except: return float('inf')
    first_non_turbo = next((start_idx + i for i, ac in enumerate(aircraft_list) if not ac.get("is_turbo")), None)
    cheapest_jet    = min((_parse_price(ac["price"]) for ac in aircraft_list if not ac.get("is_turbo")), default=float('inf'))
    cards = ""
    for idx, ac in enumerate(aircraft_list, start_idx):
        is_best  = (idx == first_non_turbo)
        is_turbo = ac.get("is_turbo", False)
        best_tag  = '<div class="ac-best-tag"><span class="ac-star">&#9733;</span> Best Value</div>' if is_best else ""
        turbo_tag = (f'<div class="ac-turbo-tag">{"Turboprop — More affordable, but slower" if _parse_price(ac["price"]) < cheapest_jet else "Turboprop — Slower than jets"}</div>') if is_turbo else ""
        border    = 'border-left:3px solid #8a6a42;' if is_best else ('border-left:3px solid #c08040;' if is_turbo else 'border-left:3px solid rgba(138,106,66,.25);')
        route_html   = (f'<div class="ac-route"><span class="route-label">Route</span><span class="route-airport">{_e(ac["dep"] or "—")}</span><span class="arrow">→</span><span class="route-airport">{_e(ac["arr"] or "—")}</span></div>' if (ac.get("dep") or ac.get("arr")) else "")
        am = ac.get("amenities", "")
        amenity_html = f'<div class="ac-amenity"><span class="ac-label">Amenities</span><span class="ac-am-val">{_e(am)}</span></div>' if am and am.lower() not in ("none listed", "none", "") else ""
        cards += (f'<div class="aircraft-card" style="{border}">{best_tag}{turbo_tag}'
                  f'<div class="ac-number">Aircraft {idx:02d}</div><div class="ac-name">{_e(ac["name"])}</div>'
                  f'<div class="ac-grid">'
                  f'<div class="ac-field"><span class="ac-label">Capacity</span><span class="ac-value">{_e(ac.get("cap","—"))}</span></div>'
                  f'<div class="ac-field"><span class="ac-label">Price</span><span class="ac-price">{_e(ac["price"])}</span></div>'
                  f'<div class="ac-field"><span class="ac-label">Flight Time</span><span class="ac-value">{_e(ac["ft"])}</span></div>'
                  f'</div>{route_html}{amenity_html}</div>')
    return cards

def _route_strip(dep, arr):
    ds, as_ = (dep or "—").split(",")[0].strip(), (arr or "—").split(",")[0].strip()
    return (f'<div class="flight-summary"><div class="fs-pill"><span class="fs-label">From</span><span class="fs-value">{_e(ds)}</span></div>'
            f'<span class="fs-sep">→</span><div class="fs-pill"><span class="fs-label">To</span><span class="fs-value">{_e(as_)}</span></div>'
            f'<span class="fs-meta">Top options by price</span></div>') if (ds != "—" or as_ != "—") else ""

def _leg_header(icon, label, dep, arr):
    return (f'<div class="leg-header"><span class="leg-icon">{icon}</span><span class="leg-label">{_e(label)}</span>'
            f'<span class="leg-route">{_e((dep or "—").split(",")[0].strip())}</span><span class="leg-arrow">→</span>'
            f'<span class="leg-route">{_e((arr or "—").split(",")[0].strip())}</span></div>')

def render_leg(ac_list, dep, arr, session_id, amenities, label, icon, date="", alt_airport=""):
    total, shown = len(ac_list), ac_list[:PAGE_SIZE]
    sc = summary_card(shown, dep, arr, amenities, label=label, date=date) if session_id else ""
    if total == 0:
        count = f'<div class="bubble bot" style="margin-top:8px">No {label.lower()} aircraft found for this leg.</div>'
    elif total <= PAGE_SIZE:
        alt = (f' ✈ I also have results from <strong>{_e(alt_airport)}</strong> — reply <strong>show me the alternative airport</strong> to see them.') if alt_airport else ""
        count = f'<div class="bubble bot" style="margin-top:8px">Showing <strong>{total} of {total}</strong> {label.lower()} aircraft. That\'s all available for this leg.{alt}</div>'
    else:
        count = f'<div class="bubble bot" style="margin-top:8px">Showing <strong>{PAGE_SIZE} of {total}</strong> {label.lower()} aircraft. Reply <strong>yes</strong> to see the remaining {total - PAGE_SIZE}.</div>'
    return _leg_header(icon, label, dep, arr) + _route_strip(dep, arr) + _make_cards(shown, 1) + sc + count

def render_session(full_list, dep, arr, session_id, intro_html, amenities=None, card_label="", date="", alt_airport=""):
    total, shown = len(full_list), full_list[:PAGE_SIZE]
    sc = summary_card(shown, dep, arr, amenities, label=card_label, date=date) if session_id else ""
    if total <= PAGE_SIZE:
        alt = (f' ✈ I also have results from <strong>{_e(alt_airport)}</strong> — reply <strong>show me the alternative airport</strong> to see them.') if alt_airport else ""
        tail = f'<div class="bubble bot" style="margin-top:12px">Showing <strong>{total} of {total}</strong> available aircraft. That\'s all available aircraft for this route.{alt}</div>'
    else:
        tail = f'<div class="bubble bot" style="margin-top:12px">Showing <strong>{PAGE_SIZE} of {total}</strong> available aircraft. Reply <strong>yes</strong> to see the remaining {total - PAGE_SIZE}.</div>'
    return intro_html + _route_strip(dep, arr) + _make_cards(shown, 1) + sc + tail

# ── CSS ───────────────────────────────────────────────────────────────────────
CHAT_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300&family=DM+Mono:wght@300;400;500&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{background:transparent;font-family:'DM Mono',monospace;padding:8px 4px 16px}
.msg-row{display:flex;margin-bottom:18px;gap:13px;align-items:flex-start}
.msg-row.user{flex-direction:row-reverse}
.avatar{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px}
.avatar.bot{background:radial-gradient(135deg,rgba(240,210,150,.5),rgba(184,151,100,.2));border:2px solid #c8a464;box-shadow:0 0 0 1px rgba(255,225,150,.45),0 3px 12px rgba(138,106,66,.18),inset 0 1px 0 rgba(255,240,200,.8)}
.avatar.user{background:radial-gradient(135deg,#eedcb8,#d8bc90);border:2px solid #c8a464;box-shadow:0 0 0 1px rgba(255,225,150,.4),0 3px 12px rgba(138,106,66,.16),inset 0 1px 0 rgba(255,245,215,.9)}
.bubble{max-width:82%;padding:14px 18px;border-radius:10px;font-size:.82rem;line-height:1.75;font-family:'DM Mono',monospace}
.bubble.bot{background:rgba(255,255,255,.92);border:1px solid rgba(138,106,66,.13);border-radius:10px 10px 10px 2px;color:#2c2318;box-shadow:0 2px 16px rgba(138,106,66,.08),inset 0 1px 0 #fff}
.bubble.user{background:linear-gradient(145deg,#d8c4a0,#ccb48c);border:1px solid rgba(160,120,70,.35);border-radius:10px 10px 2px 10px;color:#2a1f12;box-shadow:0 3px 16px rgba(138,106,66,.18),inset 0 1px 0 rgba(255,245,220,.5)}
.bubble strong{color:#6b4f2e;font-weight:500}
.flight-summary{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,.7);border:1px solid rgba(138,106,66,.14);border-radius:6px;padding:10px 16px;margin-bottom:12px}
.fs-pill{display:flex;flex-direction:column;gap:1px}.fs-label{font-size:.5rem;letter-spacing:.2em;text-transform:uppercase;color:#b8a898}
.fs-value{font-size:.82rem;color:#2c2318}.fs-sep{color:rgba(138,106,66,.5);font-size:.9rem;margin:0 2px}.fs-meta{font-size:.65rem;color:#b8a898;margin-left:auto;letter-spacing:.08em}
.aircraft-card{background:linear-gradient(150deg,rgba(255,255,255,.95),rgba(253,248,240,.98));border:1px solid rgba(138,106,66,.14);border-left:3px solid rgba(138,106,66,.7);border-radius:8px;padding:18px 22px;margin:12px 0;font-family:'DM Mono',monospace;box-shadow:0 4px 24px rgba(138,106,66,.07),inset 0 1px 0 rgba(255,255,255,.9);transition:all .25s}
.aircraft-card:hover{border-left-color:#8a6a42;box-shadow:0 6px 32px rgba(138,106,66,.12);transform:translateY(-1px)}
.ac-number{font-size:.57rem;letter-spacing:.22em;text-transform:uppercase;color:rgba(138,106,66,.55);margin-bottom:4px}
.ac-name{font-family:'Cormorant Garamond',serif;font-size:1.12rem;color:#1a2438;letter-spacing:.03em;margin-bottom:12px}
.ac-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 20px;margin-bottom:10px}
.ac-field{display:flex;flex-direction:column;gap:2px}.ac-label{font-size:.57rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898}
.ac-value{font-size:.78rem;color:#3c3028;font-weight:300}.ac-price{font-size:1.05rem;color:#5a3e1e;font-weight:500;letter-spacing:.03em}
.ac-route{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:rgba(138,106,66,.05);border:1px solid rgba(138,106,66,.1);border-radius:4px;padding:7px 12px;margin:8px 0;font-size:.73rem}
.route-label{font-size:.55rem;letter-spacing:.15em;text-transform:uppercase;color:#b8a898}.arrow{color:rgba(138,106,66,.6);font-size:.8rem}.route-airport{color:#2c2318;font-size:.75rem}
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
.leg-route{font-size:.82rem;color:#2c2318}.leg-arrow{color:rgba(138,106,66,.55);font-size:.8rem;margin:0 2px}
.roundtrip-divider{border:none;border-top:1px dashed rgba(138,106,66,.25);margin:24px 0}
.ac-best-tag{display:inline-flex;align-items:center;gap:5px;background:#fdf6e8;border:1px solid #eed49f;border-radius:3px;padding:3px 10px;font-size:.58rem;letter-spacing:.18em;text-transform:uppercase;color:#a07830;font-weight:500;margin-bottom:8px}
.ac-star{color:#c5973a;font-size:.7rem}
.ac-turbo-tag{display:inline-flex;align-items:center;gap:5px;background:#fff8f0;border:1px solid #e8c090;border-radius:3px;padding:3px 10px;font-size:.58rem;letter-spacing:.12em;color:#a06020;font-weight:500;margin-bottom:8px;margin-left:6px}
.ac-amenity{margin:6px 0 4px;display:flex;flex-direction:column;gap:2px}
.ac-am-val{font-size:.73rem;color:#3c3028;font-weight:300;font-style:italic}
.ac-book-btn{display:inline-block;margin-top:10px;padding:7px 14px;background:transparent;border:1px solid rgba(138,106,66,.4);border-radius:3px;color:#6b4f2e;font-family:'DM Mono',monospace;font-size:.62rem;letter-spacing:.14em;text-transform:uppercase;text-decoration:none;transition:all .2s}
.ac-book-btn:hover{background:rgba(138,106,66,.08);border-color:#8a6a42;color:#4a3018}
.alt-airport-header{background:linear-gradient(90deg,rgba(138,106,66,.08),rgba(138,106,66,.02));border:1px solid rgba(138,106,66,.18);border-left:3px solid #b8976a;border-radius:6px;padding:12px 16px;margin:20px 0 8px;font-family:'DM Mono',monospace}
.alt-airport-title{font-size:.62rem;letter-spacing:.2em;text-transform:uppercase;color:#8a6a42;margin-bottom:6px}
.alt-airport-why{font-size:.72rem;color:#5a4030;line-height:1.6;font-style:italic}
"""

def _extract_alt_airport(text: str) -> str:
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

def _render_alt_airport(msg_content: str) -> str:
    alt_m = re.search(r'🔄\s*\*{0,2}Alternative departure[:\s*]+(.+?)(?=\n)', msg_content, re.IGNORECASE)
    if not alt_m: return ""
    alt_section = msg_content[alt_m.start():]
    why_m    = re.search(r'Why this airport\?[\*\s]+(.+?)(?=\n\n|\n\d+\.\s|\Z)', alt_section, re.DOTALL | re.IGNORECASE)
    why_text = re.sub(r'\*+|\n', ' ', why_m.group(1)).strip() if why_m else ""
    alt_ac, _, _ = parse_aircraft(alt_section)
    if not alt_ac: return ""
    why_html = f'<div class="alt-airport-why">{_e(why_text)}</div>' if why_text else ""
    return (f'<div class="alt-airport-header"><div class="alt-airport-title">🔄 Alternative departure: {_e(alt_m.group(1).strip().strip("*"))}</div>{why_html}</div>'
            + _make_cards(alt_ac[:3], start_idx=1))

# ── Build chat HTML ───────────────────────────────────────────────────────────
def build_chat_html(messages, is_loading, session_id=""):
    sessions, last_was_result = [], False
    for i, m in enumerate(messages):
        is_result = m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE)
        if m["role"] == "user" and last_was_result and m["content"].strip().lower() not in SHOW_MORE_WORDS:
            last_was_result = False
        if is_result:
            if not sessions or not last_was_result:
                sessions.append({"ridx":[],"amenities":[],"all_ac":[],"dep":"","arr":"","date":"",
                                  "is_roundtrip":False,"ob_ac":[],"ob_dep":"","ob_arr":"","ob_date":"",
                                  "rt_ac":[],"rt_dep":"","rt_arr":"","rt_date":""})
            sess = sessions[-1]; sess["ridx"].append(i)
            ob_text, rt_text = split_roundtrip(m["content"])
            if rt_text is not None:
                sess["is_roundtrip"] = True
                ob_ac, ob_dep, ob_arr = parse_aircraft(ob_text); rt_ac, rt_dep, rt_arr = parse_aircraft(rt_text)
                sess["ob_ac"].extend(ob_ac); sess["rt_ac"].extend(rt_ac)
                if not sess["ob_dep"] and ob_dep: sess["ob_dep"], sess["ob_arr"] = ob_dep, ob_arr
                if not sess["rt_dep"] and rt_dep: sess["rt_dep"], sess["rt_arr"] = rt_dep, rt_arr
                sess["all_ac"] = sess["ob_ac"] + sess["rt_ac"]
            else:
                ac, d, a = parse_aircraft(m["content"]); sess["all_ac"].extend(ac)
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
                if sess.get("is_roundtrip"):
                    more_inner = ""
                    for leg_ac, leg_dep, leg_arr, leg_label, leg_icon, leg_date in [
                        (sess["ob_ac"], sess["ob_dep"], sess["ob_arr"], "Outbound", "✈", sess.get("ob_date","")),
                        (sess["rt_ac"], sess["rt_dep"], sess["rt_arr"], "Return",   "↩", sess.get("rt_date","")),
                    ]:
                        if len(leg_ac) > PAGE_SIZE:
                            remaining = leg_ac[PAGE_SIZE:]; total = len(leg_ac)
                            more_inner += (_leg_header(leg_icon, leg_label, leg_dep, leg_arr)
                                           + _route_strip(leg_dep, leg_arr) + _make_cards(remaining, PAGE_SIZE + 1)
                                           + (summary_card(leg_ac, leg_dep, leg_arr, amenities, label=leg_label, date=leg_date) if session_id else "")
                                           + f'<div class="bubble bot" style="margin-top:8px">Showing <strong>{total} of {total}</strong> {leg_label.lower()} aircraft. That\'s all available for this leg.</div>')
                    if more_inner:
                        html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{more_inner}</div></div>'
                elif len(sess["all_ac"]) > PAGE_SIZE:
                    remaining, total = sess["all_ac"][PAGE_SIZE:], len(sess["all_ac"])
                    dep, arr, date   = sess["dep"], sess["arr"], sess.get("date","")
                    more_inner = (_route_strip(dep, arr) + _make_cards(remaining, PAGE_SIZE + 1)
                                  + (summary_card(sess["all_ac"], dep, arr, amenities, date=date) if session_id else "")
                                  + f'<div class="bubble bot" style="margin-top:12px">Showing <strong>{total} of {total}</strong> available aircraft. That\'s all available aircraft for this route.</div>')
                    html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{more_inner}</div></div>'

        elif i in idx_to_session:
            sess, si = idx_to_session[i]
            if si in rendered_sessions: continue
            rendered_sessions.add(si); skip_next_bot = False
            first_ridx = sess["ridx"][0]; first_content = messages[first_ridx]["content"]; amenities = []

            aq_idx = next((j for j in range(first_ridx) if messages[j]["role"] == "assistant"
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
                for j in range(first_ridx):
                    if messages[j]["role"] == "user":
                        found = _scan_amenities(messages[j]["content"], from_user=True)
                        if found: amenities = found
            if not amenities and not user_said_no_amenities:
                for j in range(first_ridx + 1):
                    if messages[j]["role"] == "assistant":
                        if re.search(r'do you require any|any of the following amenities', messages[j]["content"], re.IGNORECASE): continue
                        found = _scan_amenities(messages[j]["content"], from_user=False)
                        if found: amenities = found
            sess["amenities"] = amenities
            amenity_line  = f'Your amenity preferences ({md(", ".join(amenities))}) have been noted.<br><br>' if amenities else ""
            prev_user     = next((messages[j]["content"] for j in range(first_ridx - 1, -1, -1) if messages[j]["role"] == "user"), "")
            is_alt_result = bool(_ALT_REQ.search(prev_user.strip()))

            if sess.get("is_roundtrip"):
                dates = _all_dates(messages, first_ridx)
                ob_date, rt_date = (dates[0] if dates else ""), (dates[1] if len(dates) > 1 else "")
                sess["ob_date"], sess["rt_date"] = ob_date, rt_date
                alt_airport  = "" if is_alt_result else _extract_alt_airport(first_content)
                intro_bubble = f'<div class="bubble bot" style="margin-bottom:10px">{amenity_line}Here are the top aircraft options for your round trip:</div>'
                ob_html = render_leg(sess["ob_ac"], sess["ob_dep"], sess["ob_arr"], session_id, amenities, "Outbound", "✈", date=ob_date, alt_airport="")
                rt_html = render_leg(sess["rt_ac"], sess["rt_dep"], sess["rt_arr"], session_id, amenities, "Return",   "↩", date=rt_date, alt_airport=alt_airport)
                html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{intro_bubble + ob_html}<hr class="roundtrip-divider">{rt_html}</div></div>'
            else:
                dates = _all_dates(messages, first_ridx); date = dates[0] if dates else ""; sess["date"] = date
                intro_m = re.match(r'^(.*?)(?=\n*\d+\.\s)', first_content, re.DOTALL)
                raw_intro_lines = [l for l in (intro_m.group(1).splitlines() if intro_m else [])
                                   if not (l.strip() and re.search(r'require.*amenit|amenit.*noted|have been noted|as an amenity|as amenities', l, re.IGNORECASE))]
                intro_html  = f'<div class="bubble bot" style="margin-bottom:10px">{amenity_line}{md(clean_intro(chr(10).join(raw_intro_lines)))}</div>'
                alt_airport = "" if is_alt_result else _extract_alt_airport(first_content)
                html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{render_session(sess["all_ac"], sess["dep"], sess["arr"], session_id, intro_html, amenities, date=date, alt_airport=alt_airport)}{_render_alt_airport(first_content)}</div></div>'

        else:
            if skip_next_bot: skip_next_bot = False; continue
            is_ac = bool(re.search(r'have been noted|amenities? noted|your.*amenit.*preference|noted.*amenit|noted[:\s]+wifi|noted[:\s]+cater|noted[:\s]+vip', m["content"], re.IGNORECASE))
            html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0"><div class="{"bubble bot amenity-confirm" if is_ac else "bubble bot"}">{md(m["content"])}</div></div></div>'

    if is_loading:
        html += f'<div class="typing-wrap"><div class="avatar bot">{BOT_SVG}</div><div class="typing-dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div></div>'
    if not messages and not is_loading:
        html = '''<div style="text-align:center;padding:80px 0 30px;opacity:.6">
          <div style="display:flex;justify-content:center;margin-bottom:20px"><div style="width:72px;height:72px;border-radius:50%;background:radial-gradient(135deg,rgba(212,185,140,.3),rgba(184,151,100,.1));border:2px solid #c8a464;box-shadow:0 0 0 1px rgba(255,225,150,.4),0 4px 24px rgba(138,106,66,.12);display:flex;align-items:center;justify-content:center">
          <svg width="34" height="34" viewBox="0 0 24 24"><path d="M21 16v-2l-8-5V3.5C13 2.67 12.33 2 11.5 2S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" fill="#8a6a42"/></svg></div></div>
          <div style="font-family:'Cormorant Garamond',serif;font-size:1.35rem;font-weight:300;font-style:italic;color:#5a4030;letter-spacing:.03em">Where would you like to fly today?</div>
          <div style="font-size:.58rem;letter-spacing:.28em;text-transform:uppercase;color:#b8a898;margin-top:12px">Private jets · Global routes · Instant quotes</div></div>'''
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CHAT_CSS}</style></head><body>{html}<script>window.scrollTo(0,document.body.scrollHeight);</script></body></html>'


# ── Streamlit UI ──────────────────────────────────────────────────────────────
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300&family=DM+Mono:wght@300;400;500&display=swap');
*,*::before,*::after{box-sizing:border-box}
html,body,[data-testid="stAppViewContainer"],[data-testid="stMain"]{background:#f0e8d8!important;font-family:'DM Mono',monospace}
[data-testid="stAppViewContainer"]{background:linear-gradient(160deg,#fdf8f0 0%,#f0e8d8 55%,#e8d8c0 100%)!important}
#MainMenu,footer,header,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"]{display:none!important}
.block-container{max-width:920px!important;margin:0 auto!important;padding:0 28px 160px!important}
.jet-header{padding:36px 0 22px;border-bottom:1px solid rgba(138,106,66,.18);margin-bottom:24px}
.jet-logo{font-family:'Cormorant Garamond',serif;font-size:2.5rem;color:#1a2438;letter-spacing:.04em;line-height:1}
.jet-logo span{color:#8a6a42}.jet-tagline{font-size:.62rem;letter-spacing:.28em;text-transform:uppercase;color:#9a8e80;margin-top:7px}
.route-bar{background:rgba(255,255,255,.65);border:1px solid rgba(138,106,66,.16);border-radius:4px;padding:9px 16px;font-size:.68rem;letter-spacing:.14em;color:#9a8e80;text-transform:uppercase}
.route-bar strong{color:#6b4f2e;font-weight:500}
.stButton>button{background:transparent!important;border:1px solid rgba(138,106,66,.28)!important;border-radius:3px!important;color:#7a5a32!important;font-family:'DM Mono',monospace!important;font-size:.66rem!important;letter-spacing:.16em!important;text-transform:uppercase!important;padding:9px 16px!important;transition:all .2s!important}
.stButton>button:hover{background:rgba(138,106,66,.07)!important;border-color:rgba(138,106,66,.5)!important}
[data-testid="stChatInput"]{background:rgba(240,232,216,.98)!important;border-top:1px solid rgba(138,106,66,.14)!important}
[data-testid="stChatInput"] textarea{background:rgba(255,255,255,.9)!important;border:1px solid rgba(138,106,66,.22)!important;border-radius:4px!important;color:#1a2438!important;font-family:'DM Mono',monospace!important;font-size:.82rem!important;caret-color:#8a6a42!important}
[data-testid="stChatInput"] textarea:focus{border-color:rgba(138,106,66,.45)!important;box-shadow:0 0 0 2px rgba(138,106,66,.08)!important;outline:none!important}
[data-testid="stChatInput"] textarea::placeholder{color:#b8a898!important}
[data-testid="stChatInput"] button{background:#8a6a42!important;border:none!important;border-radius:4px!important;color:#fdfaf5!important}
.api-note{font-size:.58rem;color:#b8a898;letter-spacing:.1em;text-align:center;padding:5px 0 0}
.api-note span{color:#8a6a42}
</style>""", unsafe_allow_html=True)

# ── Session init ──────────────────────────────────────────────────────────────
st.session_state.setdefault("is_loading", False)
st.session_state.setdefault("messages", [])
if "session_id" not in st.session_state:
    sid = st.query_params.get("sid") or str(uuid.uuid4())
    st.session_state.session_id = sid; st.query_params["sid"] = sid
if not st.session_state.get("_history_loaded"):
    st.session_state["_history_loaded"] = True
    loaded = _store_load(st.session_state.session_id)
    if loaded: st.session_state.messages = loaded

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown('<div class="jet-header"><div class="jet-logo">Jet<span>Bot</span></div><div class="jet-tagline">Private Aviation Intelligence</div></div>', unsafe_allow_html=True)

active_route = ""
for m in reversed(st.session_state.messages):
    if m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE):
        dep = re.search(r'[Dd]eparture[:\s]+([^\n*<]+)', m["content"])
        arr = re.search(r'[Aa]rrival[:\s]+([^\n*<]+)', m["content"])
        if dep and arr: active_route = f'{dep.group(1).split(",")[0].strip()} → {arr.group(1).split(",")[0].strip()}'
        break

col1, col2 = st.columns([5, 1])
with col1:
    bar = f'<strong>Route</strong> {H.escape(active_route)}' if active_route else "Ask me where you'd like to fly"
    st.markdown(f'<div class="route-bar">{bar}</div>', unsafe_allow_html=True)
with col2:
    if st.button("↺ New Flight"):
        _store_clear(st.session_state.session_id)
        new_sid = str(uuid.uuid4())
        st.session_state.update({"session_id": new_sid, "messages": [], "is_loading": False, "_history_loaded": True})
        st.query_params["sid"] = new_sid; st.rerun()

# ── Chat render ───────────────────────────────────────────────────────────────
msgs = st.session_state.messages
n = len(msgs)
result_msgs = sum(1 for m in msgs if m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE))
h = max(400, min(1200, 160 + (n - result_msgs * 2) * 80 + result_msgs * 500 + (220 if st.session_state.is_loading else 0)))
components.html(build_chat_html(msgs, st.session_state.is_loading, st.session_state.session_id), height=h, scrolling=True)

user_input = st.chat_input("Message JetBot…")
st.markdown('<div class="api-note">Powered by <span>JetBot</span> · Private Aviation Intelligence</div>', unsafe_allow_html=True)

# ── Handle new user message ───────────────────────────────────────────────────
if user_input and user_input.strip():
    text = user_input.strip()
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
    last = next((m.get("resolved") or m["content"] for m in reversed(msgs) if m["role"] == "user"), None)
    if last:
        reply_parts = []

        def _token_generator():
            try:
                with requests.post(BACKEND, json={"message": last, "session_id": st.session_state.session_id},
                                   stream=True, timeout=(10, 240)) as resp:
                    if resp.status_code != 200:
                        chunk = f"⚠ Backend error {resp.status_code}"; reply_parts.append(chunk); yield chunk; return
                    for raw in resp.iter_lines():
                        if not raw: continue
                        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                        if not line.startswith("data: "): continue
                        p = line[6:]
                        if p == "[DONE]": break
                        if p.startswith("[SESSION:"): st.session_state.session_id = p[9:-1]; continue
                        token = p.replace("\\n", "\n"); reply_parts.append(token); yield token
            except requests.exceptions.ConnectionError:
                chunk = "⚠ Cannot connect to backend. Run: uvicorn main:app --reload"; reply_parts.append(chunk); yield chunk
            except requests.exceptions.Timeout:
                chunk = "⚠ Search timed out — Avinode may be slow. Please try again."; reply_parts.append(chunk); yield chunk
            except Exception as e:
                chunk = f"⚠ Error: {str(e)}"; reply_parts.append(chunk); yield chunk

        with st.chat_message("assistant", avatar="✈"):
            st.write_stream(_token_generator())

        reply = "".join(reply_parts) or "⚠ Empty response from backend."
        assistant_msg = {"role": "assistant", "content": reply}
        st.session_state.messages.append(assistant_msg)
        _store_append(st.session_state.session_id, assistant_msg)
        st.session_state.is_loading = False
        st.rerun()