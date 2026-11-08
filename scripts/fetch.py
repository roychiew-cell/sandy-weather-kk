"""Sandy weather: fetch local-first data for Kota Kinabalu and write data/latest.json.

Run by GitHub Actions on a schedule, or by hand:  python3 -I scripts/fetch.py
Every upstream response is archived unchanged under data/raw/<name>.json with its URL and
retrieval time, so the derived figures in latest.json can be recomputed by anyone.

Sources (chosen for Kota Kinabalu, 7 Nov 2026):
  MetMalaysia forecast (town Tn196 + district Ds554) and warnings  via api.data.gov.my
  WBKK airport observations (METAR) and aviation forecast (TAF)     via aviationweather.gov
  ECMWF 51-member ensemble, 9 deterministic models, blend, air quality via open-meteo.com
  Previous-run forecasts (issued the day before) for yesterday's scorecard via previous-runs-api.open-meteo.com
"""
import json, os, sys, math, urllib.request, urllib.parse, datetime as dt, statistics as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
os.makedirs(RAW, exist_ok=True)

LAT, LON = 5.937, 116.051            # Kota Kinabalu International Airport (WBKK)
TZ = "Asia/Kuching"
MYT = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "sandy-weather-kk/2.0 (github.com/roychiew-cell/sandy-weather-kk)"}
MODELS = {                             # Open-Meteo model key -> display name
    "best_match": "Open-Meteo blend",
    "ecmwf_ifs025": "ECMWF IFS",
    "gfs_global": "NOAA GFS",
    "ukmo_global_deterministic_10km": "UK Met Office UM",
    "jma_gsm": "JMA GSM",
    "icon_global": "DWD ICON",
    "gem_global": "Canada GEM",
    "meteofrance_arpege_world": "Météo-France ARPEGE",
    "cma_grapes_global": "CMA GRAPES",
}
STORM_WINDOW = (14, 18)                # MYT; "predicted >=1 mm during 14:00-18:00" sums accumulations ending 15..18

now = dt.datetime.now(MYT)
today = now.strftime("%Y-%m-%d")
yday = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
status = {}                            # name -> {"ok": bool, "url": ..., "at": ..., "error": ...}


def fetch(name, url):
    """GET url, archive the raw body, return parsed JSON or None."""
    at = dt.datetime.now(MYT).isoformat(timespec="seconds")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
        data = json.loads(body)
        with open(os.path.join(RAW, name + ".json"), "w") as f:
            json.dump({"url": url, "retrieved_at": at, "response": data}, f)
        status[name] = {"ok": True, "url": url, "at": at}
        return data
    except Exception as e:
        status[name] = {"ok": False, "url": url, "at": at, "error": str(e)[:300]}
        print("FAILED", name, e, file=sys.stderr)
        return None


def om(host, **q):
    q.setdefault("latitude", LAT); q.setdefault("longitude", LON); q.setdefault("timezone", TZ)
    return f"https://{host}/v1/{q.pop('path')}?" + urllib.parse.urlencode(q)


# ---------------------------------------------------------------- official + airport
metmy = fetch("metmalaysia_forecast", "https://api.data.gov.my/weather/forecast/?contains=Kota%20Kinabalu@location__location_name&limit=50")
warn = fetch("metmalaysia_warnings", "https://api.data.gov.my/weather/warning/?limit=100")
metar = fetch("wbkk_metar", "https://aviationweather.gov/api/data/metar?ids=WBKK&format=json&hours=48")
taf = fetch("wbkk_taf", "https://aviationweather.gov/api/data/taf?ids=WBKK&format=json")

# ---------------------------------------------------------------- models
ens = fetch("ecmwf_ensemble", om("ensemble-api.open-meteo.com", path="ensemble", models="ecmwf_ifs025",
            daily="precipitation_sum,temperature_2m_max", forecast_days=7))
blend = fetch("openmeteo_blend", om("api.open-meteo.com", path="forecast",
              hourly="temperature_2m,apparent_temperature,precipitation_probability,precipitation,weather_code,wind_speed_10m,wind_gusts_10m,cloud_cover,uv_index,cape",
              daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,uv_index_max,sunrise,sunset",
              forecast_days=7))
det = fetch("models_deterministic", om("api.open-meteo.com", path="forecast", models=",".join(MODELS),
            daily="temperature_2m_max,temperature_2m_min,precipitation_sum", forecast_days=7))
prev = fetch("models_previous_day1", om("previous-runs-api.open-meteo.com", path="forecast", models=",".join(MODELS),
             hourly="temperature_2m_previous_day1,precipitation_previous_day1", past_days=1, forecast_days=1))
aq = fetch("air_quality", om("air-quality-api.open-meteo.com", path="air-quality", current="pm2_5,pm10,us_aqi"))

# ---------------------------------------------------------------- derive: observations
obs = []
for m in sorted(metar or [], key=lambda m: m["obsTime"]):
    t = dt.datetime.fromtimestamp(m["obsTime"], MYT)
    raw = m.get("rawOb") or ""
    recent = [w for w in raw.split() if w.startswith("RE") and len(w) > 2 and w != "RMK"]
    obs.append({"t": t.isoformat(timespec="minutes"), "temp": m.get("temp"), "dewp": m.get("dewp"),
                "wdir": m.get("wdir"), "wspd": m.get("wspd"), "cover": m.get("cover"),
                "wx": m.get("wxString") or "", "recent": " ".join(recent), "raw": raw})
obs_y = {o["t"][11:13]: o["temp"] for o in obs if o["t"].startswith(yday) and o["t"][14:16] == "00" and o["temp"] is not None}
ts_episodes = []                       # active thunderstorm (present weather contains TS), split at gaps > 45 min
cur = None
for o in obs:
    if not o["t"].startswith(yday):
        continue
    active = "TS" in o["wx"]
    tt = dt.datetime.fromisoformat(o["t"])
    if active:
        if cur and (tt - dt.datetime.fromisoformat(cur["last"])).total_seconds() <= 45 * 60:
            cur["last"] = o["t"]
        else:
            cur = {"first": o["t"], "last": o["t"]}; ts_episodes.append(cur)
recent_ts = [o["t"][11:16] for o in obs if o["t"].startswith(yday) and "TS" in o["recent"] and "TS" not in o["wx"]]

# ---------------------------------------------------------------- derive: scorecard (forecasts issued 24 h before each hour, scored on the hours ALL models and the airport share)
scores = {}
score_hours = []
if prev and obs_y and len(obs_y) >= 18:
    h = prev["hourly"]
    series = {}
    for key, name in MODELS.items():
        T = {t[11:13]: v for t, v in zip(h["time"], h.get(f"temperature_2m_previous_day1_{key}", [])) if t.startswith(yday)}
        P = {t[11:13]: v for t, v in zip(h["time"], h.get(f"precipitation_previous_day1_{key}", [])) if t.startswith(yday)}
        if sum(v is not None for v in T.values()) >= 18:
            series[name] = (T, P)
    common = set(obs_y)
    for T, P in series.values():
        common &= {k for k, v in T.items() if v is not None}
    score_hours = sorted(common)
    if len(score_hours) >= 18:
        for name, (T, P) in series.items():
            pairs = [(T[k], obs_y[k]) for k in score_hours]
            rmse = math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs))
            bias = sum(a - b for a, b in pairs) / len(pairs)
            tvals = [v for v in T.values() if v is not None]
            ends = [f"{k:02d}" for k in range(STORM_WINDOW[0] + 1, STORM_WINDOW[1] + 1)]   # accumulations ending 15..18
            pv = [P.get(k) for k in ends]
            storm_raw = None if any(v is None for v in pv) else sum(pv)
            scores[name] = {"n": len(pairs), "rmse": round(rmse, 2), "bias": round(bias, 2),
                            "tmax": round(max(tvals), 1) if tvals else None,
                            "window_mm": None if storm_raw is None else round(storm_raw, 1),
                            "window_ge1mm": None if storm_raw is None else storm_raw >= 1.0}

# ---------------------------------------------------------------- derive: forecasts per model, ensemble
days = sorted({*(blend["daily"]["time"] if blend else []), *(det["daily"]["time"] if det else []), *(ens["daily"]["time"] if ens else []), *(r["date"] for r in (metmy or []) if r.get("date", "") >= today)})
fc = {}
if det:
    dd = det["daily"]
    for key, name in MODELS.items():
        sfx = "" if len(MODELS) == 1 else f"_{key}"
        idx = {d: i for i, d in enumerate(dd["time"])}
        pick = lambda arr: [arr[idx[d]] if (arr and d in idx) else None for d in days]
        fc[name] = {"tmax": pick(dd.get(f"temperature_2m_max{sfx}")), "tmin": pick(dd.get(f"temperature_2m_min{sfx}")),
                    "rain": pick(dd.get(f"precipitation_sum{sfx}"))}
ens_days = []
if ens:
    e = ens["daily"]
    pk = [k for k in e if k.startswith("precipitation_sum")]
    tk = [k for k in e if k.startswith("temperature_2m_max")]
    for i, d in enumerate(e["time"]):
        v = sorted(x for x in (e[k][i] for k in pk) if x is not None)
        tv = sorted(x for x in (e[k][i] for k in tk) if x is not None)
        if len(v) < 40:
            ens_days.append({"day": d, "n": len(v)}); continue
        n = len(v)
        q = lambda p: round(v[min(n - 1, int(round((n - 1) * p)))], 1)   # sorted member at index round((n-1)*p); equals the linear-interpolation percentile position for n=51
        ens_days.append({"day": d, "n": n, "members": v,
                         "p1": round(100 * sum(x >= 1 for x in v) / n), "p10": round(100 * sum(x >= 10 for x in v) / n),
                         "p20": round(100 * sum(x >= 20 for x in v) / n),
                         "q10": q(.1), "q50": q(.5), "q90": q(.9), "tmax50": round(S.median(tv), 1) if tv else None})

# ---------------------------------------------------------------- derive: MetMalaysia records and warnings
metmy_by_id = {}
for r in metmy or []:
    metmy_by_id.setdefault(r["location"]["location_id"], []).append(r)
for k in metmy_by_id:
    metmy_by_id[k].sort(key=lambda r: r["date"])
# Ds = district, Tn = town, St = state (MetMalaysia id prefixes)
metmy_out = {k: {"kind": {"Ds": "district", "Tn": "town", "St": "state"}.get(k[:2], "other"),
                 "name": v[0]["location"]["location_name"], "days": v} for k, v in metmy_by_id.items()}

warnings = []
import re
MONTHS = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"])}
PLACES = ["Kota Kinabalu", "West Coast", "Western Sabah", "Eastern Sabah", "Labuan", "Kudat", "Sandakan", "Tawau", "Lahad Datu", "Interior", "Penampang", "Papar", "Tuaran", "Beaufort", "Kota Belud", "Ranau", "Layang-Layang", "Sulu", "Sulawesi", "Reef North", "Reef South", "Condore", "Sabah", "Sarawak", "Kelantan", "Terengganu", "Pahang", "Johor", "Selangor", "Perak", "Penang", "Kedah", "Perlis", "Melaka", "N. Sembilan", "Samui", "Tioman", "Phuket", "Straits Of Melaka"]


def parse_date(s):
    m = re.search(r"(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})", s)
    if not m or m.group(2) not in MONTHS:
        return None
    return f"{m.group(3)}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"


def parse_time(s):
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*(AM|PM)", s, re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return f"{h:02d}:{m.group(2)}"


DIVISION_STATES = {"Sabah", "Sarawak"}
OTHER_STATES = ["Perlis", "Kedah", "Penang", "Pulau Pinang", "Perak", "Kelantan", "Terengganu", "Pahang", "Selangor", "Kuala Lumpur", "Putrajaya",
                "Negeri Sembilan", "N. Sembilan", "Melaka", "Malacca", "Johor", "Sarawak", "Sabah", "Labuan"]
DIVISIONS = {"Sabah": {"West Coast": ["Kota Kinabalu", "Penampang", "Papar", "Tuaran", "Kota Belud", "Ranau", "Putatan"],
                       "Interior": ["Beaufort", "Kuala Penyu", "Sipitang", "Tenom", "Keningau", "Tambunan", "Nabawan"],
                       "Kudat": ["Kudat", "Kota Marudu", "Pitas"], "Sandakan": ["Sandakan", "Beluran", "Kinabatangan", "Tongod", "Telupid"],
                       "Tawau": ["Tawau", "Lahad Datu", "Semporna", "Kunak", "Kalabakan"]},
             "Sarawak": {"Kuching": ["Kuching", "Bau", "Lundu"], "Samarahan": ["Samarahan", "Asajaya", "Simunjan"], "Serian": ["Serian", "Tebedu"],
                         "Sri Aman": ["Sri Aman", "Lubok Antu"], "Betong": ["Betong", "Saratok", "Pusa", "Kabong"], "Sarikei": ["Sarikei", "Meradong", "Julau", "Pakan"],
                         "Sibu": ["Sibu", "Kanowit", "Selangau"], "Mukah": ["Mukah", "Dalat", "Daro", "Matu", "Tanjung Manis"], "Kapit": ["Kapit", "Song", "Belaga", "Bukit Mabong"],
                         "Bintulu": ["Bintulu", "Tatau", "Sebauh"], "Miri": ["Miri", "Marudi", "Subis", "Beluru", "Telang Usan"], "Limbang": ["Limbang", "Lawas"]}}
STATE_ALIAS = {"Penang": ["Penang", "Pulau Pinang"], "Negeri Sembilan": ["Negeri Sembilan", "N. Sembilan"], "Melaka": ["Melaka", "Malacca"],
               "Labuan": ["Labuan", "W.P. Labuan", "WP Labuan"], "Kuala Lumpur": ["Kuala Lumpur", "W.P. Kuala Lumpur", "WP Kuala Lumpur"],
               "Putrajaya": ["Putrajaya", "W.P. Putrajaya", "WP Putrajaya"]}  # how MetMalaysia bulletins spell the states


def _name_re(n):
    return re.compile(r"(^|[^A-Za-z])" + re.escape(n) + r"(?=$|[^A-Za-z])", re.I)


def _alt(state):
    return "(?:" + "|".join(re.escape(x) for x in STATE_ALIAS.get(state, [state])) + ")"


def area_coverage(text, state, division=None, district=None, ctx=""):
    """Same reading as areaCoverage() in index.html: True / False / None (cannot tell).
    Reads "State: item, item (district, district) and item" after the state's name (or, when only the heading/scope names the
    state, the whole body as that state's list); in Sabah and Sarawak the items are divisions, elsewhere districts; an item with a
    bracket list covers only the districts listed; a state named in the text with no list covers the whole state; a qualified
    region ("Eastern Sabah") cannot be resolved; whole names only, never substrings."""
    if not state:
        return None
    alt = _alt(state)
    s_re = re.compile(r"(^|[^A-Za-z])" + alt + r"(?=$|[^A-Za-z])", re.I)
    q_re = re.compile(r"\b(?:Eastern|Western|Northern|Southern|Central|Coastal|Interior|Upper|Lower)\s+" + alt + r"(?=$|[^A-Za-z])", re.I)
    ctx_text = ctx or ""

    def plain(t):  # "Eastern Sabah" is a region the page cannot resolve, never the whole state
        return q_re.sub(" ", t)

    in_text, in_ctx = bool(s_re.search(plain(text))), bool(s_re.search(plain(ctx_text)))
    if not in_text and not in_ctx:  # a qualified region anywhere and no plain state: unresolved, not excluded
        return None if (q_re.search(text) or q_re.search(ctx_text)) else False

    def in_list(lst, name):
        return bool(lst and name and _name_re(name).search(lst))

    def same(x, y):
        return bool(x and y and _name_re(x).search(y) and _name_re(y).search(x))

    known = (list(DIVISIONS[state].keys()) + [d for v in DIVISIONS[state].values() for d in v]) if state in DIVISIONS else []

    def placey(n):  # a place name is Title Case; prose ("other affected areas") is not
        return bool(re.match(r"^(?:[A-Z][A-Za-z.'&-]*\s*)+$", (n or "").strip()))

    def other_state(n):  # a bullet list continues into the next state's entry: understood, and never a match
        x = re.sub(r"^(?:W\.?P\.?|Wilayah Persekutuan)\s+", "", (n or "").strip(), flags=re.I).strip().lower()
        return x in {s.lower() for s in OTHER_STATES} and x not in {a.lower() for a in STATE_ALIAS.get(state, [state])}

    def recog(n):  # an item the parser understands
        if other_state(n):
            return True
        if known:
            return any(same(k, n) for k in known) or bool(district and same(n, district)) or bool(division and same(n, division))
        return placey(n)

    def list_ok(lst):  # every bracketed name understood
        if lst is None:
            return True
        parts = [x.strip() for x in re.split(r",|\band\b|•|;", lst) if x.strip()]
        return all((known and any(same(k, x) for k in known)) or placey(x) for x in parts)

    m = re.search(r"(?:^|[^A-Za-z])" + alt + r"\s*:\s*(.*)", text, re.S | re.I) if in_text else None
    if in_text and not m:
        ml = re.search(r"(?:^|[^A-Za-z])" + alt + r"\s*\(([^)]*)\)", text, re.I)
        if ml:  # "State (district, district)": only those districts, and only when every name is understood
            if not district:
                return None
            if in_list(ml.group(1), district):
                return True
            return False if list_ok(ml.group(1)) else None
        return True  # state named in the text without any list: whole state
    if m:
        seg = m.group(1)
    else:  # heading-only state: the body's list (after "over …") is that state's list
        mo = re.search(r"\bover\s+(?:the\s+(?:areas?|states?|districts?|divisions?)\s+of\s+)?(.*)", text, re.S | re.I)
        seg = mo.group(1) if mo else re.sub(r"^\s*(?:[IVX]+|\d+)\)\s*", "", text)
    seg = re.sub(r"\b(W\.P|N)\.", "\\1\x01", seg)  # "W.P." and "N." are abbreviations, not sentence ends
    seg = re.split(r"(?:^|\s)(?:[A-Z][A-Za-z.' ]*:)", seg)[0]
    seg = re.split(r"\.\s|\s(?:within|until|from|is|are|expected|valid|issued|will|may)\b", seg, flags=re.I)[0].replace("\x01", ".")
    items, depth, cur, i = [], 0, "", 0
    while i < len(seg):  # split on ",", "•", ";" and " and " outside brackets only
        ch = seg[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        is_and = (depth == 0 and seg[i:i + 3].lower() == "and" and (i == 0 or seg[i - 1].isspace())
                  and (i + 3 >= len(seg) or seg[i + 3].isspace()))
        if depth == 0 and (ch in ",•;" or is_and):
            items.append(cur)
            cur = ""
            if is_and:
                i += 2
        else:
            cur += ch
        i += 1
    items.append(cur)
    parsed = []
    for x in items:
        mm = re.match(r"^\s*([^()]+?)\s*(?:\(([^)]*)\))?\s*\.?\s*$", x)
        if mm and mm.group(1).strip():
            parsed.append((mm.group(1).strip(), mm.group(2)))
    if not parsed:
        return None
    def hit(p):
        return bool(district and (same(p[0], district) or in_list(p[1], district)))

    def understood():  # a negative needs the whole list understood
        return all(recog(p[0]) and list_ok(p[1]) for p in parsed)

    if state in DIVISION_STATES:
        dv = next((p for p in parsed if division and same(p[0], division)), None)
        if dv:
            if dv[1] is None:
                return True
            if not district:
                return None
            if in_list(dv[1], district):
                return True
            return False if list_ok(dv[1]) else None
        if any(hit(p) for p in parsed):
            return True
        return False if (division and understood()) else None
    if any(hit(p) for p in parsed):
        return True
    return False if (district and understood()) else None


def covers_kk(text, ctx=""):
    """Kota Kinabalu is covered when the subsection, read by area_coverage, names KK or Sabah's West Coast without leaving KK out.
    ctx = the bulletin heading and section scope, which may carry the state when the subsection does not."""
    return area_coverage(text, "Sabah", "West Coast", "Kota Kinabalu", ctx) is True


def subsections(text, head=""):
    """Split a MetMalaysia composite bulletin into numbered subsections with their own validity."""
    parts = re.split(r"(?=(?:\b[IVX]+\)|\b\d\))\s)", text)
    out = []
    scope = None
    for p in parts:
        p = p.strip()
        ms = re.search(r"SECTION\s+[A-Z]\s*:\s*([A-Z][A-Z \[\]0-9]+?)\s*$", p)   # a header at the END of a piece belongs to the NEXT subsection
        if len(p) < 30 or not re.match(r"(?:[IVX]+|\d)\)", p):
            if ms:
                scope = ms.group(1).title()
            continue
        this_scope = scope
        if ms:
            scope = ms.group(1).title()
        mt = re.match(r"(?:[IVX]+|\d)\)\s*([A-Z][A-Z \-/()]{6,}?)(?=\s+[A-Z][a-z]|\s*$)", p)
        title = mt.group(1).strip().title() if mt else ("Continuous rain" if "ontinuous rain" in p else None)
        sub = {"text": p, "areas": [a for a in PLACES if a in p], "from": None, "to": None, "title": title, "scope": this_scope}
        m = re.search(r"within the period of\s+(.+?)\s+until\s+(.+?)(?:\.|$)", p)
        if m:
            d1, d2 = parse_date(m.group(1)), parse_date(m.group(2))
            if d1 and d2:
                sub["from"], sub["to"] = d1 + "T00:00:00", d2 + "T23:59:59"
        else:
            m = re.search(r"until\s+(\d{1,2}[:.]\d{2}\s*(?:AM|PM))\s*;?\s*([A-Za-z]+,\s*\d{1,2}\s+[A-Z][a-z]+\s+\d{4})", p, re.I)
            if m:
                d, tm = parse_date(m.group(2)), parse_time(m.group(1))
                if d and tm:
                    sub["to"] = d + "T" + tm + ":00"
            else:
                m = re.search(r"until\s+([A-Za-z]+,\s*\d{1,2}\s+[A-Z][a-z]+\s+\d{4})", p)
                if m and parse_date(m.group(1)):
                    sub["to"] = parse_date(m.group(1)) + "T23:59:59"
        sub["sea"] = "waters" in p.lower()
        sub["covers_kk"] = covers_kk(p, (head or "") + " " + (this_scope or ""))
        out.append(sub)
    return out


for w in warn or []:
    tx = w.get("text_en") or ""
    head = (w.get("heading_en") or w["warning_issue"].get("title_en") or "")
    kind = ("cyclone" if "cyclone" in (head + tx).lower() and "warning" not in head.lower()
            else "marine" if "SEAS" in head.upper() or "WIND" in head.upper()
            else "rain" if "RAIN" in head.upper() else "other")
    if "THUNDERSTORM" in head.upper() and kind == "other":
        kind = "thunder"
    subs = subsections(tx, head)
    if not subs and tx and kind != "cyclone":
        # an unnumbered single-warning bulletin: one subsection carrying the bulletin's own structured validity
        subs = [{"text": tx, "areas": [a for a in PLACES if a in tx], "from": w.get("valid_from"), "to": w.get("valid_to"),
                 "from_inherited": True, "to_inherited": True,  # copied from the bulletin, not read from the text
                 "title": head.title(), "scope": None, "sea": "waters" in tx.lower(), "covers_kk": covers_kk(tx, head)}]
    warnings.append({"issued": w["warning_issue"]["issued"], "heading": head, "kind": kind,
                     "valid_from": w.get("valid_from"), "valid_to": w.get("valid_to"), "text": tx,
                     "areas": [a for a in PLACES if a in tx], "subsections": subs,
                     "parsed": all(s["to"] for s in subs) and bool(subs),
                     "names_kk_land": any(s["covers_kk"] and not s["sea"] for s in subs)})
seen, uniq = {}, []
for w in warnings:  # duplicates with different bulletin validity are kept as a recorded conflict, never resolved by order
    k = (w["heading"], w["text"][:400])
    first = seen.get(k)
    if first is None:
        seen[k] = w; uniq.append(w)
    elif (first.get("valid_from"), first.get("valid_to")) != (w.get("valid_from"), w.get("valid_to")):
        first.setdefault("valid_conflict", [f"{first.get('valid_from') or '?'} – {first.get('valid_to') or '?'}"]).append(
            f"{w.get('valid_from') or '?'} – {w.get('valid_to') or '?'}")

# ---------------------------------------------------------------- TAF: structured periods in MYT
taf_out = None
if taf:
    t0 = taf[0]
    per = []
    for f in t0.get("fcsts", []):
        per.append({"from": dt.datetime.fromtimestamp(f["timeFrom"], MYT).isoformat(timespec="minutes"),
                    "to": dt.datetime.fromtimestamp(f["timeTo"], MYT).isoformat(timespec="minutes"),
                    "change": f.get("fcstChange"), "prob": f.get("probability"), "wx": f.get("wxString") or "",
                    "wdir": f.get("wdir"), "wspd": f.get("wspd"), "visib": f.get("visib"),
                    "clouds": [c.get("cover", "") + (("/" + c["type"]) if c.get("type") else "") for c in f.get("clouds", [])]})
    taf_out = {"raw": t0.get("rawTAF"), "issued": dt.datetime.fromtimestamp(t0["issueTime"] / 1000 if t0["issueTime"] > 1e11 else t0["issueTime"], MYT).isoformat(timespec="minutes") if isinstance(t0.get("issueTime"), (int, float)) else t0.get("issueTime"),
               "valid_from": dt.datetime.fromtimestamp(t0["validTimeFrom"], MYT).isoformat(timespec="minutes"),
               "valid_to": dt.datetime.fromtimestamp(t0["validTimeTo"], MYT).isoformat(timespec="minutes"), "periods": per}

out = {
    "generated": now.isoformat(timespec="seconds"), "today": today, "yday": yday, "tz": "+08:00 (MYT)",
    "point": {"lat": LAT, "lon": LON, "name": "Kota Kinabalu International Airport (WBKK)"},
    "status": status, "obs": obs, "obs_y_max": max(obs_y.values()) if obs_y else None, "obs_y_hours": len(obs_y),
    "ts_episodes_yday": ts_episodes, "recent_ts_yday": recent_ts,
    "taf": taf_out, "metmy": metmy_out, "warnings": uniq, "warnings_retrieved": len(warn or []),
    "ens": ens_days, "scores": scores, "score_hours": score_hours, "score_window": list(STORM_WINDOW), "fc": fc, "days": days,
    "blend": {"daily": blend["daily"], "hourly": blend["hourly"], "units": blend.get("hourly_units")} if blend else None,
    "aq": {"current": aq.get("current"), "units": aq.get("current_units"), "time_myt": (aq.get("current") or {}).get("time")} if aq else None,
}
with open(os.path.join(ROOT, "data", "latest.json"), "w") as f:
    json.dump(out, f, separators=(",", ":"))
hist = os.path.join(ROOT, "data", "history"); os.makedirs(hist, exist_ok=True)
with open(os.path.join(hist, now.strftime("%Y-%m-%d_%H%M") + ".json"), "w") as f:
    json.dump(out, f, separators=(",", ":"))
# keep the last 72 hourly snapshots (3 days)
for fn in sorted(os.listdir(hist))[:-72]:
    os.remove(os.path.join(hist, fn))
print("written", today, "ok:", [k for k, v in status.items() if v["ok"]], "failed:", [k for k, v in status.items() if not v["ok"]])
