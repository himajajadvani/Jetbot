import streamlit as st
import streamlit.components.v1 as components
import requests, uuid, re, html as H

# ── Page config ───────────────────────────────────────────────────────────────
try:
    from PIL import Image
    st.set_page_config(page_title="JetBot", page_icon=Image.open("favicon.png"), layout="wide", initial_sidebar_state="collapsed")
except:
    st.set_page_config(page_title="JetBot", page_icon="✈", layout="wide", initial_sidebar_state="collapsed")

BACKEND = "http://localhost:8000/chat/stream"

# ── SVGs ──────────────────────────────────────────────────────────────────────
BOT_SVG  = '<svg width="22" height="22" viewBox="0 0 24 24"><path d="M21 16v-2l-8-5V3.5C13 2.67 12.33 2 11.5 2S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" fill="#8a6a42"/></svg>'
USER_SVG = '<svg width="20" height="20" viewBox="0 0 24 24"><defs><radialGradient id="ug" cx="50%" cy="35%" r="60%"><stop offset="0%" stop-color="#7a5a32"/><stop offset="100%" stop-color="#4a3018"/></radialGradient></defs><circle cx="12" cy="8" r="3.8" fill="url(#ug)"/><path d="M4.5 21c0-4.1 3.4-7.2 7.5-7.2s7.5 3.1 7.5 7.2" fill="url(#ug)"/></svg>'
CHK_SVG  = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none"><path d="M9 12l2 2 4-4" stroke="#6a8a5a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="9" stroke="#6a8a5a" stroke-width="1.5"/></svg>'

# ── Amenity map ───────────────────────────────────────────────────────────────
AM = {
    "wifi":"WiFi","wi-fi":"WiFi","wi fi":"WiFi","internet":"WiFi",
    "catering":"Catering","food":"Catering","meal":"Catering","meals":"Catering","dining":"Catering","drinks":"Catering",
    "vip lounge":"VIP Lounge","vip":"VIP Lounge","lounge":"VIP Lounge",
    "hangar":"Hangar","hanger":"Hangar","storage":"Hangar",
    "customs":"Customs","immigration":"Customs",
    "pet friendly":"Pet-Friendly","pet-friendly":"Pet-Friendly","pets":"Pet-Friendly","pet":"Pet-Friendly","dog":"Pet-Friendly","dogs":"Pet-Friendly",
    "gpu":"GPU","ground power":"GPU",
}

def prefs_display(keys):
    seen, out = set(), []
    for k in keys:
        lbl = AM.get(k, k.replace("_"," ").title())
        if lbl not in seen: seen.add(lbl); out.append(lbl)
    return out

def parse_prefs(messages):
    for m in reversed(messages):
        if m["role"] == "user":
            hit = re.search(r'amenities detected\s*=\s*"([^"]+)"', m["content"])
            if hit and hit.group(1).strip():
                return [a.strip() for a in hit.group(1).split(",") if a.strip()]
    # fallback — scan user message before first result
    ridx = [i for i,m in enumerate(messages) if m["role"]=="assistant" and re.search(r'^\d+\.\s+\*\*',m["content"],re.MULTILINE)]
    if ridx:
        for m in reversed(messages[:ridx[0]]):
            if m["role"] == "user":
                t = m["content"].lower()
                found = set()
                for kw in sorted(AM, key=len, reverse=True):
                    if kw in t: found.add(AM[kw])
                if found:
                    return list(dict.fromkeys(next((k for k,v in AM.items() if v==l),l.lower()) for l in found))
                break
    return []

def md(text):
    t = H.escape(text)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2" target="_blank">\1</a>', t)
    return t.replace('\n','<br>')

def field(body, pat):
    m = re.search(pat, body)
    return re.sub(r'\*+|<[^>]+>','',m.group(1)).strip() if m else ""

def clean_intro(text, prefs):
    t = text.strip()
    for bad in [r'[Uu]nfortunately[^\n.]+[.\n]?', r'[Nn]one of the[^\n.]+[.\n]?',
                r'[Hh]ere are.{0,60}without.{0,40}filter[^\n.]*[.\n]?',
                r'[Nn]o aircraft.{0,60}amenities[^\n.]*[.\n]?',
                r'\n+\d+\.[^\n]+',   # strip "1.Repeated intro text" duplicate lines
                r'and will be confirmed at booking\.?',
                ]:
        t = re.sub(bad, '', t).strip()
    if prefs:
        pstr = ", ".join(prefs_display(prefs))
        t = re.sub(r'[Hh]ere are your top options[^\n]+\n?', '', t).strip()
        t = re.sub(r'[Yy]our amenity preferences[^\n]+\n?', '', t).strip()
        t = f"Your amenity preferences ({pstr}) have been noted.\n\nHere are the top aircraft options for your route:" + (f"\n\n{t}" if t else "")
    else:
        # Strip LLM's own intro line and replace with clean version
        t = re.sub(r'[Hh]ere are.{0,80}(?:options|jets?|aircraft)[^\n]*\n?', '', t).strip()
        if not t:
            t = "Here are the top aircraft options for your route:"
        else:
            t = f"Here are the top aircraft options for your route:\n\n{t}"
    return t

def parse_aircraft(content):
    blocks = re.findall(r'\d+\.\s+\*\*(.+?)\*\*(.*?)(?=\n\d+\.\s+\*\*|\Z)', content, re.DOTALL)
    out, dep, arr = [], "", ""
    for name, body in blocks:
        p  = re.sub(r'<br>.*','',field(body,r'[Pp]rice[:\s]+([^\n*<br]+)')).strip() or "—"
        ft = re.sub(r'\*+','',field(body,r'[Ff]light\s*[Tt]ime[:\s]+([^\n*<]+)')).strip() or "N/A"
        d  = re.sub(r'\*+|\s*[Aa]rrival.*$','',field(body,r'[Dd]eparture[:\s]+([^\n]+)')).strip()
        a  = re.sub(r'\*+|\s*\*.*$','',field(body,r'[Aa]rrival[:\s]+([^\n]+)')).strip()
        if not dep and d: dep, arr = d, a
        out.append({"name": name.strip(), "price": p, "ft": ft, "dep": d, "arr": a,
                    "cap": field(body,r'[Cc]apacity[:\s]+([^\n*<]+)') or "—",
                    "amen": field(body,r'[Aa]menities[:\s]+([^\n*<]+)')})
    return out, dep, arr

# ── Summary card with clipboard copy ─────────────────────────────────────────
def summary_card(aircraft, dep, arr, prefs):
    pstr = ", ".join(prefs_display(prefs)) if prefs else ""
    # plain text for clipboard
    lines = ["✈ Private Jet Summary","",f"Route: {dep or '—'} → {arr or '—'}"]
    if pstr: lines.append(f"Noted amenities: {pstr}")
    lines.append("")
    for i,ac in enumerate(aircraft,1): lines.append(f"{i}. {ac['name']}  —  {ac['price']}  ·  {ac['ft']}")
    lines += ["","Searched via JetBot · Private Aviation Intelligence"]

    def code(s): return f' <span style="color:#b8a898">({m.group(1)})</span>' if (m:=re.search(r'\(([^)]+)\)\s*$',s)) else ""
    def city(s): return H.escape(s.split("(")[0].strip()) if s else "—"

    route_row = (f'<div class="sc-meta"><span style="font-size:.55rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898;margin-right:6px">Route</span>'
                 f'<span class="sc-route-val">{city(dep)}{code(dep)}</span><span class="sc-sep">→</span>'
                 f'<span class="sc-route-val">{city(arr)}{code(arr)}</span></div>') if (dep or arr) else ""

    pref_row = (f'<div class="sc-pref-row"><span class="sc-pref-label">Noted amenities</span>'
                f'<span class="sc-pref-val">{CHK_SVG} <span style="color:#6a8a5a">{H.escape(pstr)}</span></span></div>') if pstr else ""

    rows = "".join(
        f'<div class="sc-row"><span class="sc-idx">{i:02d}</span>'
        f'<span class="sc-acname">{H.escape(ac["name"])}</span>'
        f'<span class="sc-price">{H.escape(ac["price"])}</span>'
        f'<span class="sc-ft">{H.escape(ac["ft"])}</span></div>'
        for i,ac in enumerate(aircraft,1)
    )
    # Store text in a data attribute to avoid any JS string escaping issues
    import json as _json
    data_text = H.escape(_json.dumps("\n".join(lines)))
    return (
        f'<div class="summary-card">'
        f'<div class="sc-header">'
        f'<div class="sc-title"><svg width="14" height="14" viewBox="0 0 24 24"><path d="M21 16v-2l-8-5V3.5C13 2.67 12.33 2 11.5 2S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z" fill="#8a6a42"/></svg>Flight Summary</div>'
        f'<button class="copy-btn" id="copybtn" data-text="{data_text}" onclick="var t=JSON.parse(this.getAttribute(\'data-text\'));navigator.clipboard.writeText(t).then(()=>{{this.textContent=\'✓ Copied!\';this.classList.add(\'copied\');setTimeout(()=>{{this.textContent=\'Copy\';this.classList.remove(\'copied\')}},2500)}});">'
        f'Copy</button>'
        f'</div>'
        f'{route_row}{pref_row}'
        f'<div class="sc-rows">{rows}</div>'
        f'<div class="sc-footer">Searched via JetBot · Private Aviation Intelligence</div>'
        f'</div>'
    )

# ── Render one page of aircraft cards ────────────────────────────────────────
def render_cards(content, session_id="", prefs=None, all_aircraft=None, rdep="", rarr="", is_last=False, is_first=False):
    prefs = prefs or []
    aircraft, page_dep, page_arr = parse_aircraft(content)
    # Always prefer dep/arr from the passed-in aircraft list (from cache — guaranteed full labels)
    # Only fall back to parsed text if no aircraft passed
    if all_aircraft:
        first = all_aircraft[0]
        dep = first.get("dep") or first.get("departure_airport") or rdep or page_dep
        arr = first.get("arr") or first.get("arrival_airport") or rarr or page_arr
    else:
        dep, arr = rdep or page_dep, rarr or page_arr

    # intro bubble — only on first page of this search session
    intro_html = ""
    if is_first:
        intro_m = re.match(r'^(.*?)(?=\n*\d+\.\s)', content, re.DOTALL)
        if intro_m:
            cleaned = clean_intro(intro_m.group(1), prefs)
            if cleaned.strip():
                intro_html = f'<div class="bubble bot" style="margin-bottom:10px">{md(cleaned)}</div>'

    # flight strip — use first aircraft's full dep/arr labels
    first_dep = aircraft[0]["dep"] if aircraft else dep
    first_arr = aircraft[0]["arr"] if aircraft else arr
    strip_dep = first_dep or dep or "—"
    strip_arr = first_arr or arr or "—"
    # Show short city name in strip (first segment before comma), full in route
    dep_short = strip_dep.split(",")[0].strip()
    arr_short = strip_arr.split(",")[0].strip()
    strip = (f'<div class="flight-summary">'
             f'<div class="fs-pill"><span class="fs-label">From</span><span class="fs-value">{H.escape(dep_short)}</span></div>'
             f'<span class="fs-sep">→</span>'
             f'<div class="fs-pill"><span class="fs-label">To</span><span class="fs-value">{H.escape(arr_short)}</span></div>'
             f'<span class="fs-meta">Top options by price</span></div>') if (dep_short != "—" or arr_short != "—") else ""

    cards = ""
    for idx, ac in enumerate(aircraft, 1):
        am_html = "".join(f'<span class="ac-amenity">{H.escape(a.strip())}</span>'
                          for a in re.split(r'[,;]+', ac["amen"])
                          if a.strip() and a.strip().lower() not in ("none","n/a","none listed","-",""))
        amenity_sec = (f'<div class="ac-divider"></div><span class="ac-section-label">Amenities</span><div>{am_html}</div>') if am_html else ""
        route_html  = (f'<div class="ac-route"><span class="route-label">Route</span>'
                       f'<span class="route-airport">{H.escape(ac["dep"] or "—")}</span>'
                       f'<span class="arrow">→</span>'
                       f'<span class="route-airport">{H.escape(ac["arr"] or "—")}</span></div>') if (ac["dep"] or ac["arr"]) else ""
        cards += (f'<div class="aircraft-card">'
                  f'<div class="ac-number">Aircraft {idx:02d}</div>'
                  f'<div class="ac-name">{H.escape(ac["name"])}</div>'
                  f'<div class="ac-grid">'
                  f'<div class="ac-field"><span class="ac-label">Capacity</span><span class="ac-value">{H.escape(ac["cap"])}</span></div>'
                  f'<div class="ac-field"><span class="ac-label">Price</span><span class="ac-price">{H.escape(ac["price"])}</span></div>'
                  f'<div class="ac-field"><span class="ac-label">Flight Time</span><span class="ac-value">{H.escape(ac["ft"])}</span></div>'
                  f'</div>{route_html}{amenity_sec}</div>')

    # summary card — only on last result message
    # first result page → show only this page's 5; subsequent → show all accumulated
    sum_html = ""
    if is_last and session_id:
        # Always use all accumulated aircraft for summary
        sum_ac = all_aircraft or aircraft
        sum_html = summary_card(sum_ac, strip_dep, strip_arr, prefs)

    # outro
    outro_m = re.search(r'\n((?:Showing|Would|That\'s all).+)$', content, re.DOTALL)
    outro = f'<div class="bubble bot" style="margin-top:10px;color:#9a8e80">{md(outro_m.group(1).strip())}</div>' if outro_m else ""

    return intro_html + strip + cards + sum_html + outro

# ── Build full chat HTML ──────────────────────────────────────────────────────
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
.fs-pill{display:flex;flex-direction:column;gap:1px}
.fs-label{font-size:.5rem;letter-spacing:.2em;text-transform:uppercase;color:#b8a898}
.fs-value{font-size:.82rem;color:#2c2318}
.fs-sep{color:rgba(138,106,66,.5);font-size:.9rem;margin:0 2px}
.fs-meta{font-size:.65rem;color:#b8a898;margin-left:auto;letter-spacing:.08em}
.aircraft-card{background:linear-gradient(150deg,rgba(255,255,255,.95),rgba(253,248,240,.98));border:1px solid rgba(138,106,66,.14);border-left:3px solid rgba(138,106,66,.7);border-radius:8px;padding:18px 22px;margin:12px 0;font-family:'DM Mono',monospace;box-shadow:0 4px 24px rgba(138,106,66,.07),inset 0 1px 0 rgba(255,255,255,.9);transition:all .25s}
.aircraft-card:hover{border-left-color:#8a6a42;box-shadow:0 6px 32px rgba(138,106,66,.12);transform:translateY(-1px)}
.ac-number{font-size:.57rem;letter-spacing:.22em;text-transform:uppercase;color:rgba(138,106,66,.55);margin-bottom:4px}
.ac-name{font-family:'Cormorant Garamond',serif;font-size:1.12rem;color:#1a2438;letter-spacing:.03em;margin-bottom:12px}
.ac-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 20px;margin-bottom:10px}
.ac-field{display:flex;flex-direction:column;gap:2px}
.ac-label{font-size:.57rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898}
.ac-value{font-size:.78rem;color:#3c3028;font-weight:300}
.ac-price{font-size:1.05rem;color:#5a3e1e;font-weight:500;letter-spacing:.03em}
.ac-route{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:rgba(138,106,66,.05);border:1px solid rgba(138,106,66,.1);border-radius:4px;padding:7px 12px;margin:8px 0;font-size:.73rem}
.route-label{font-size:.55rem;letter-spacing:.15em;text-transform:uppercase;color:#b8a898}
.arrow{color:rgba(138,106,66,.6);font-size:.8rem}
.route-airport{color:#2c2318;font-size:.75rem}
.ac-divider{height:1px;background:linear-gradient(90deg,rgba(138,106,66,.14),transparent);margin:10px 0 8px}
.ac-section-label{font-size:.57rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898;margin-bottom:6px;display:block}
.ac-amenity{display:inline-block;font-size:.57rem;letter-spacing:.1em;text-transform:uppercase;padding:3px 7px;border:1px solid rgba(138,106,66,.25);border-radius:2px;color:#6b4f2e;background:rgba(138,106,66,.07);margin:2px 3px 2px 0}
.summary-card{background:linear-gradient(150deg,rgba(255,255,255,.92),rgba(253,248,240,.96));border:1px solid rgba(138,106,66,.18);border-radius:8px;padding:18px 22px;margin-top:16px;font-family:'DM Mono',monospace}
.sc-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;gap:10px}
.sc-title{display:flex;align-items:center;gap:8px;font-family:'Cormorant Garamond',serif;font-size:1rem;color:#1a2438;letter-spacing:.03em}
.copy-btn{display:inline-flex;align-items:center;gap:6px;font-family:'DM Mono',monospace;font-size:.58rem;letter-spacing:.16em;text-transform:uppercase;color:#6b4f2e;background:transparent;border:1px solid rgba(138,106,66,.3);border-radius:3px;padding:6px 13px;cursor:pointer;transition:all .2s;white-space:nowrap}
.copy-btn:hover{background:rgba(138,106,66,.07);border-color:rgba(138,106,66,.5)}
.copy-btn.copied{color:#5a8a50;border-color:rgba(90,138,80,.4);background:rgba(90,138,80,.07)}
.sc-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid rgba(138,106,66,.1)}
.sc-route-val{font-size:.8rem;color:#2c2318}
.sc-sep{color:rgba(138,106,66,.45);margin:0 2px}
.sc-pref-row{display:flex;align-items:center;gap:6px;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid rgba(138,106,66,.1)}
.sc-pref-label{font-size:.55rem;letter-spacing:.16em;text-transform:uppercase;color:#b8a898;white-space:nowrap}
.sc-pref-val{font-size:.72rem;color:#6a8a5a;font-style:italic;display:flex;align-items:center;gap:4px}
.sc-rows{display:flex;flex-direction:column;gap:7px}
.sc-row{display:flex;align-items:baseline;gap:0;padding:6px 0;border-bottom:1px solid rgba(138,106,66,.06)}
.sc-row:last-child{border-bottom:none}
.sc-idx{font-size:.57rem;letter-spacing:.14em;text-transform:uppercase;color:rgba(138,106,66,.5);width:28px;flex-shrink:0}
.sc-acname{font-family:'Cormorant Garamond',serif;font-size:.95rem;color:#1a2438;flex:1}
.sc-price{font-size:.8rem;color:#5a3e1e;font-weight:500;white-space:nowrap;margin:0 10px}
.sc-ft{font-size:.72rem;color:#9a8e80;white-space:nowrap}
.sc-footer{font-size:.58rem;color:#c8b898;letter-spacing:.1em;margin-top:12px;text-align:right}
.typing-wrap{display:flex;gap:13px;align-items:center;margin-bottom:18px}
.typing-dots{display:flex;gap:6px;padding:14px 20px;background:rgba(255,255,255,.92);border:1px solid rgba(138,106,66,.12);border-radius:10px 10px 10px 2px}
.dot{width:5px;height:5px;border-radius:50%;background:#b8976a;animation:pulse 1.4s ease-in-out infinite}
.dot:nth-child(2){animation-delay:.22s}.dot:nth-child(3){animation-delay:.44s}
@keyframes pulse{0%,80%,100%{opacity:.15;transform:scale(.7)}40%{opacity:1;transform:scale(1)}}
"""

def build_chat_html(messages, is_loading, session_id=""):
    prefs = parse_prefs(messages)

    # Group result messages into search sessions.
    # A new session starts when there's a user message AFTER a result message.
    # Each session accumulates its own aircraft independently.
    SHOW_MORE_WORDS = {"yes","more","show more","next","continue","yep","yeah","sure","ok","okay"}

    sessions = []
    last_was_result = False
    for i, m in enumerate(messages):
        is_result = m["role"] == "assistant" and re.search(r'^\d+\.\s+\*\*', m["content"], re.MULTILINE)
        if m["role"] == "user" and last_was_result:
            # Only break session if this is NOT a "show more" type message
            is_show_more = m["content"].strip().lower() in SHOW_MORE_WORDS
            if not is_show_more:
                last_was_result = False  # real new request — next result is a new session
        if is_result:
            if not sessions or not last_was_result:
                sessions.append({"ridx": [], "all_ac": [], "dep": "", "arr": ""})
            sess = sessions[-1]
            sess["ridx"].append(i)
            ac, d, a = parse_aircraft(m["content"])
            sess["all_ac"].extend(ac)
            if not sess["dep"] and d:
                sess["dep"], sess["arr"] = d, a
            last_was_result = True

    # Build flat lookup: message index → session
    idx_to_session = {}
    for sess in sessions:
        for i in sess["ridx"]:
            idx_to_session[i] = sess

    all_ridx = [i for sess in sessions for i in sess["ridx"]]
    last = all_ridx[-1] if all_ridx else -1

    html = ""
    for i,m in enumerate(messages):
        is_res = i in all_ridx
        if m["role"] == "user":
            html += f'<div class="msg-row user"><div class="avatar user">{USER_SVG}</div><div class="bubble user">{H.escape(m["content"])}</div></div>'
        elif is_res:
            sess    = idx_to_session[i]
            is_first_in_sess = i == sess["ridx"][0]
            is_last_in_sess  = i == sess["ridx"][-1] and i == last
            inner = render_cards(m["content"], session_id, prefs, sess["all_ac"], sess["dep"], sess["arr"], is_last_in_sess, is_first_in_sess)
            html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{inner}</div></div>'
        else:
            inner = f'<div class="bubble bot">{md(m["content"])}</div>'
            html += f'<div class="msg-row"><div class="avatar bot">{BOT_SVG}</div><div style="flex:1;min-width:0">{inner}</div></div>'

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
.jet-logo span{color:#8a6a42}
.jet-tagline{font-size:.62rem;letter-spacing:.28em;text-transform:uppercase;color:#9a8e80;margin-top:7px}
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

for k,v in [("session_id",str(uuid.uuid4())),("messages",[]),("is_loading",False)]:
    if k not in st.session_state: st.session_state[k] = v

st.markdown('<div class="jet-header"><div class="jet-logo">Jet<span>Bot</span></div><div class="jet-tagline">Private Aviation Intelligence</div></div>', unsafe_allow_html=True)

col1, col2 = st.columns([5,1])
with col1:
    ex = len(st.session_state.messages)//2
    st.markdown(f'<div class="route-bar"><strong>Session</strong> {st.session_state.session_id[:8].upper()} · {ex} exchange{"s" if ex!=1 else ""}</div>', unsafe_allow_html=True)
with col2:
    if st.button("↺ New Flight"):
        for k,v in [("session_id",str(uuid.uuid4())),("messages",[]),("is_loading",False)]: st.session_state[k]=v
        st.rerun()

n = len(st.session_state.messages)
h = max(300, min(820, 120 + n*95 + (200 if st.session_state.is_loading else 0)))
components.html(build_chat_html(st.session_state.messages, st.session_state.is_loading, st.session_state.session_id), height=h, scrolling=True)

user_input = st.chat_input("Message JetBot…")
st.markdown('<div class="api-note">Powered by <span>JetBot</span> · Private Aviation Intelligence</div>', unsafe_allow_html=True)

if user_input and user_input.strip():
    st.session_state.messages.append({"role":"user","content":user_input.strip()})
    st.session_state.is_loading = True
    st.rerun()

if st.session_state.is_loading:
    last = next((m["content"] for m in reversed(st.session_state.messages) if m["role"]=="user"), None)
    if last:
        try:
            reply = ""
            with requests.post(BACKEND, json={"message":last,"session_id":st.session_state.session_id}, stream=True, timeout=(10,240)) as resp:
                if resp.status_code == 200:
                    for raw in resp.iter_lines():
                        if not raw: continue
                        line = raw.decode("utf-8") if isinstance(raw,bytes) else raw
                        if not line.startswith("data: "): continue
                        p = line[6:]
                        if p == "[DONE]": break
                        if p.startswith("[SESSION:"): st.session_state.session_id = p[9:-1]; continue
                        reply += p.replace("\\n","\n")
                else: reply = f"⚠ Backend error {resp.status_code}"
            if not reply.strip(): reply = "⚠ Empty response from backend."
        except requests.exceptions.ConnectionError: reply = "⚠ Cannot connect to backend. Run: uvicorn main:app --reload"
        except requests.exceptions.Timeout: reply = "⚠ Search timed out — Avinode may be slow. Please try again."
        except Exception as e: reply = f"⚠ Error: {str(e)}"
        st.session_state.messages.append({"role":"assistant","content":reply})
        st.session_state.is_loading = False
        st.rerun()