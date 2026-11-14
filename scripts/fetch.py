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
import json, os, sys, math, time, urllib.request, urllib.parse, datetime as dt, statistics as S

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
    err = None
    for attempt in range(1, 4):         # 14 Nov 2026: 3 of 8 hourly runs lost the Open-Meteo blend to a TLS handshake timeout; retry before giving up
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read()
            data = json.loads(body)
            with open(os.path.join(RAW, name + ".json"), "w") as f:
                json.dump({"url": url, "retrieved_at": at, "response": data}, f)
            status[name] = {"ok": True, "url": url, "at": at}
            if attempt > 1:
                status[name]["attempts"] = attempt
            return data
        except Exception as e:
            err = e
            print("RETRY" if attempt < 3 else "FAILED", name, "attempt", attempt, e, file=sys.stderr)
            if attempt < 3:
                time.sleep(4 * attempt)
    status[name] = {"ok": False, "url": url, "at": at, "error": ("after 3 attempts: " + str(err))[:300]}
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
DIVISIONS = {"Sabah": {"West Coast": ["Kota Kinabalu", "Penampang", "Papar", "Tuaran", "Kota Belud", "Ranau", "Putatan", "Tamparuli", "Kinarut"], "Interior": ["Beaufort", "Kuala Penyu", "Sipitang", "Tenom", "Keningau", "Tambunan", "Nabawan", "Pensiangan", "Sook", "Membakut", "Kemabong", "Weston"], "Kudat": ["Kudat", "Kota Marudu", "Pitas"], "Sandakan": ["Sandakan", "Beluran", "Kinabatangan", "Tongod", "Telupid", "Paitan", "Labuk & Sugut"], "Tawau": ["Tawau", "Lahad Datu", "Semporna", "Kunak", "Kalabakan"]}, "Sarawak": {"Kuching": ["Kuching", "Bau", "Lundu"], "Samarahan": ["Samarahan", "Asajaya", "Simunjan", "Sebuyau", "Gedong"], "Serian": ["Serian", "Tebedu", "Siburan"], "Sri Aman": ["Sri Aman", "Lubok Antu", "Pantu", "Lingga"], "Betong": ["Betong", "Saratok", "Pusa", "Kabong", "Spaoh"], "Sarikei": ["Sarikei", "Meradong", "Julau", "Pakan", "Bintangor"], "Sibu": ["Sibu", "Kanowit", "Selangau"], "Mukah": ["Mukah", "Dalat", "Daro", "Matu", "Tanjung Manis"], "Kapit": ["Kapit", "Song", "Belaga", "Bukit Mabong"], "Bintulu": ["Bintulu", "Tatau", "Sebauh"], "Miri": ["Miri", "Marudi", "Subis", "Beluru", "Telang Usan", "Bario"], "Limbang": ["Limbang", "Lawas"]}}
STATE_ALIAS = {"Penang": ["Penang", "Pulau Pinang"], "Negeri Sembilan": ["Negeri Sembilan", "N. Sembilan"], "Melaka": ["Melaka", "Malacca"],
               "Labuan": ["Labuan", "W.P. Labuan", "WP Labuan"], "Kuala Lumpur": ["Kuala Lumpur", "W.P. Kuala Lumpur", "WP Kuala Lumpur"],
               "Putrajaya": ["Putrajaya", "W.P. Putrajaya", "WP Putrajaya"]}  # how MetMalaysia bulletins spell the states


def _name_re(n):
    return re.compile(r"(^|[^A-Za-z])" + re.escape(n) + r"(?=$|[^A-Za-z])", re.I)


def _alt(state):
    return "(?:" + "|".join(re.escape(x) for x in STATE_ALIAS.get(state, [state])) + ")"


KNOWN_BY_STATE = {"johor": ["ayer hitam", "batu pahat", "iskandar puteri", "johor bahru", "kluang", "kota tinggi", "kulai", "labis", "mersing", "muar", "pagoh", "pasir gudang", "pekan nenas", "penawar", "pontian", "segamat", "senai", "simpang renggam", "skudai", "tangkak", "ulu tiram", "yong peng"], "kedah": ["alor star", "baling", "bandar baharu", "gurun", "jitra", "kampong raja", "kota setar", "kuah", "kuala kedah", "kuala muda", "kuala nerang", "kubang pasu", "kulim", "langkawi", "padang terap", "pendang", "pokok sena", "sik", "sungai petani", "yan"], "kelantan": ["bachok", "gua musang", "jeli", "kota bharu", "kuala krai", "kubang kerian", "lojing", "machang", "pasir mas", "pasir puteh", "rantau panjang", "sabak", "tanah merah", "tumpat"], "kuala lumpur": ["ampang", "bandar utama", "bangsar", "bukit bintang", "jalan duta", "kepong", "kuala lumpur", "sentul", "sungai besi"], "labuan": ["labuan"], "melaka": ["alor gajah", "ayer keroh", "bandaraya melaka", "batu berendam", "durian tunggal", "jasin", "masjid tanah", "melaka tengah", "merlimau", "sungai udang", "tangga batu"], "negeri sembilan": ["bahau", "gemas", "jelebu", "jempol", "kuala klawang", "kuala pilah", "nilai", "port dickson", "rembau", "seremban", "tampin"], "pahang": ["bandar tun abdul razak, jengka", "bentong", "bera", "brinchang", "cameron highlands", "jerantut", "kuala lipis", "kuala rompin", "kuantan", "lipis", "maran", "mentakab", "muadzam shah", "pekan", "pulau tioman", "raub", "rompin", "tanah rata", "tanah rata - brinchang", "temerloh", "triang"], "penang": ["ayer itam", "balik pulau", "barat daya", "batu kawan", "bayan baru", "bayan lepas", "bukit mertajam", "bukit tengah", "butterworth", "gelugor", "georgetown", "jawi", "juru", "kepala batas", "nibong tebal", "perai", "seberang perai selatan", "seberang perai tengah", "seberang perai utara", "sungai ara", "sungai dua", "sungai dua - sungai nibong", "sungai nibong", "tanjung bungah", "tanjung tokong", "timur laut"], "perak": ["bagan datuk", "bagan serai", "bandar baru seri manjung", "batang padang", "batu gajah", "gerik", "gopeng", "hilir perak", "hulu perak", "ipoh", "kampar", "kerian", "kinta", "kuala kangsar", "larut", "larut matang", "larut matang dan selama", "lenggong", "manjung", "matang", "muallim", "parit buntar", "perak tengah", "selama", "serdang", "seri iskandar", "sitiawan", "slim river", "sungai siput", "taiping", "tanjong malim", "tapah", "teluk intan"], "perlis": ["kangar", "perlis"], "putrajaya": ["putrajaya"], "sabah": ["beaufort", "beluran", "kalabakan", "keningau", "kinabatangan", "kota belud", "kota kinabalu", "kota marudu", "kuala penyu", "kudat", "kunak", "labuk & sugut", "lahad datu", "membakut", "nabawan", "paitan", "papar", "penampang", "pensiangan", "pitas", "putatan", "ranau", "sandakan", "semporna", "sipitang", "sook", "tambunan", "tawau", "telupid", "tenom", "tongod", "tuaran"], "sarawak": ["asajaya", "bario", "bau", "belaga", "beluru", "betong", "bintulu", "bukit mabong", "dalat", "daro", "gedong", "julau", "kabong", "kanowit", "kapit", "kuching", "lawas", "limbang", "lingga", "lubok antu", "lundu", "marudi", "matu", "meradong", "miri", "mukah", "pakan", "pantu", "pusa", "samarahan", "saratok", "sarikei", "sebauh", "sebuyau", "selangau", "serian", "sibu", "siburan", "simunjan", "song", "sri aman", "subis", "tanjung manis", "tatau", "tebedu", "telang usan"], "selangor": ["bandar baru salak tinggi", "bangi", "banting", "batu caves", "cheras", "cyberjaya", "damansara", "gombak", "gombak (taman perwira )", "hulu langat", "hulu selangor", "kajang", "kelana jaya", "klang", "kuala kubu bharu", "kuala langat", "kuala selangor", "meru", "pelabuhan klang", "petaling", "petaling jaya", "puchong", "rawang", "sabak bernam", "selayang", "semenyih", "sepang", "serendah", "seri kembangan", "setapak", "shah alam", "sri gombak", "subang jaya", "sungai besar", "tanjong karang", "telok panglima garang", "ulu kelang"], "terengganu": ["bandar al-muktafi billah shah", "bandar permaisuri", "besut", "cukai", "dungun", "hulu terengganu", "jerteh", "jertih", "kemaman", "kertih", "kuala berang", "kuala nerus", "kuala terengganu", "marang", "paka", "setiu"]}  # VOCAB (generated by scripts/build_vocab.py)
KNOWN_DISTRICTS = {"johor": ["batu pahat", "johor bahru", "kluang", "kota tinggi", "kulai", "mersing", "muar", "pontian", "segamat", "tangkak"], "kedah": ["baling", "bandar baharu", "kota setar", "kuala muda", "kubang pasu", "kulim", "langkawi", "padang terap", "pendang", "pokok sena", "sik", "yan"], "kelantan": ["bachok", "gua musang", "jeli", "kota bharu", "kuala krai", "machang", "pasir mas", "pasir puteh", "tanah merah", "tumpat"], "kuala lumpur": ["kuala lumpur"], "labuan": ["labuan"], "melaka": ["alor gajah", "jasin", "melaka tengah"], "negeri sembilan": ["jelebu", "jempol", "kuala pilah", "port dickson", "rembau", "seremban", "tampin"], "pahang": ["bentong", "bera", "cameron highlands", "jerantut", "kuantan", "lipis", "maran", "pekan", "raub", "rompin", "temerloh"], "penang": ["barat daya", "seberang perai selatan", "seberang perai tengah", "seberang perai utara", "timur laut"], "perak": ["bagan datuk", "batang padang", "hilir perak", "hulu perak", "kampar", "kerian", "kinta", "kuala kangsar", "larut matang dan selama", "manjung", "muallim", "perak tengah"], "perlis": ["perlis"], "putrajaya": ["putrajaya"], "sabah": ["beaufort", "beluran", "kalabakan", "keningau", "kinabatangan", "kota belud", "kota kinabalu", "kota marudu", "kuala penyu", "kudat", "kunak", "lahad datu", "nabawan", "papar", "penampang", "pitas", "putatan", "ranau", "sandakan", "semporna", "sipitang", "tambunan", "tawau", "telupid", "tenom", "tongod", "tuaran"], "sarawak": ["asajaya", "bau", "belaga", "beluru", "betong", "bintulu", "bukit mabong", "dalat", "daro", "gedong", "julau", "kabong", "kanowit", "kapit", "kuching", "lawas", "limbang", "lingga", "lubok antu", "lundu", "marudi", "matu", "meradong", "miri", "mukah", "pakan", "pantu", "pusa", "samarahan", "saratok", "sarikei", "sebauh", "sebuyau", "selangau", "serian", "sibu", "siburan", "simunjan", "song", "sri aman", "subis", "tanjung manis", "tatau", "tebedu", "telang usan"], "selangor": ["gombak", "hulu langat", "hulu selangor", "klang", "kuala langat", "kuala selangor", "petaling", "sabak bernam", "sepang"], "terengganu": ["besut", "dungun", "hulu terengganu", "kemaman", "kuala nerus", "kuala terengganu", "marang", "setiu"]}  # VOCAB (generated by scripts/build_vocab.py)
ALIASES = {"tanah tinggi cameron": "cameron highlands", "larut, matang dan selama": "larut matang dan selama", "larut, matang and selama": "larut matang dan selama", "larut matang and selama": "larut matang dan selama", "larut matang & selama": "larut matang dan selama", "larut, matang & selama": "larut matang dan selama", "labuk sugut": "labuk & sugut", "labuk and sugut": "labuk & sugut", "timor laut": "timur laut", "maradong": "meradong"}  # VOCAB (generated by scripts/build_vocab.py)
COMPOUNDS = ["larut, matang dan selama", "larut, matang and selama", "larut, matang & selama", "larut matang and selama", "larut matang & selama", "larut matang dan selama", "labuk & sugut", "labuk and sugut"]  # VOCAB (generated by scripts/build_vocab.py)
OVERLAPS = {"nabawan": ["pensiangan"], "pensiangan": ["nabawan"], "beluran": ["labuk & sugut", "paitan"], "labuk & sugut": ["beluran"], "paitan": ["beluran"], "larut matang dan selama": ["larut matang", "larut", "matang", "selama"], "larut matang": ["larut matang dan selama", "larut", "matang"], "larut": ["larut matang dan selama", "larut matang"], "matang": ["larut matang dan selama", "larut matang"], "selama": ["larut matang dan selama"]}  # VOCAB (generated by scripts/build_vocab.py)
SUBREGIONS = ["kinarut", "tamparuli", "membakut", "kemabong", "weston", "pensiangan", "sook", "paitan", "labuk & sugut", "spaoh", "bario", "bintangor", "pulau tioman", "larut", "matang", "larut matang", "selama"]  # VOCAB (generated by scripts/build_vocab.py)
PARENTS = {"kinarut": "papar", "tamparuli": "tuaran", "membakut": "beaufort", "kemabong": "tenom", "weston": "beaufort", "pensiangan": "nabawan", "sook": "keningau", "paitan": "beluran", "labuk & sugut": "beluran", "spaoh": "betong", "bario": "miri", "bintangor": "meradong", "pulau tioman": "rompin", "larut": "larut matang dan selama", "matang": "larut matang dan selama", "larut matang": "larut matang dan selama", "selama": "larut matang dan selama"}  # VOCAB (generated by scripts/build_vocab.py)
STATE_KEYS = {"perlis", "kedah", "penang", "perak", "kelantan", "terengganu", "pahang", "selangor", "kuala lumpur", "putrajaya",
              "negeri sembilan", "melaka", "johor", "sarawak", "sabah", "labuan"}


def _norm(s):
    s = re.sub(r"\s+", " ", (s or "").replace("\x01", ".")).strip()
    return re.sub(r"\.$", "", s).strip().lower()


def _canon(n):
    x = _norm(n)
    return ALIASES.get(x, x)


def _spaced(n):
    return re.escape(n).replace("\\ ", r"\s+")


KNOWN_ST = {k: set(v) for k, v in KNOWN_BY_STATE.items()}
KNOWN_DIST = {k: set(v) for k, v in KNOWN_DISTRICTS.items()}
for _st, _d in DIVISIONS.items():
    _k = _st.lower()
    KNOWN_ST.setdefault(_k, set())
    KNOWN_DIST.setdefault(_k, set())
    for _dv, _ds in _d.items():
        KNOWN_ST[_k].add(_dv.lower())
        for _x in _ds:
            KNOWN_ST[_k].add(_canon(_x))
            KNOWN_DIST[_k].add(_canon(_x))
for _set in KNOWN_DIST.values():  # towns, islands and sub-districts are recognised but are not peer districts
    for _x in SUBREGIONS:
        _set.discard(_canon(_x))
SORTED_COMPOUNDS = sorted(COMPOUNDS, key=len, reverse=True)
COMPOUND_RES = [re.compile(r"(^|[^A-Za-z])" + _spaced(c) + r"(?![A-Za-z])", re.I) for c in SORTED_COMPOUNDS]
_PFX = re.compile(r"^\s*((?:W\.?P\.?\s+|Wilayah\s+Persekutuan\s+)?(?:" + "|".join(_spaced(x) for x in OTHER_STATES) + r"))\s*:\s*(.*)$", re.I | re.S)
INTRO_RE = re.compile(r"^(?:(?:thunderstorms?|continuous rain|continuous heavy rain|continuous torrential rain|heavy rain|torrential rain|rain|strong winds?|winds?|,|and|\s)+(?:(?:is|are)\s+expected(?:\s+to\s+occur)?)?\s*)?$", re.I)
_DATE = r"(?:[A-Za-z]+,\s*)?\d{1,2}\s+[A-Za-z]+\s+\d{4}"
_TIME = r"\d{1,2}[:.]\d{2}\s*(?:AM|PM)"
TAIL_RE = re.compile(r"^\s*(?:until\s+" + _TIME + r"(?:\s*;\s*" + _DATE + r")?|until\s+" + _DATE + r"|within the period of\s+" + _DATE + r"\s+until\s+" + _DATE + r")\s*\.?\s*$", re.I)
NEGATION_RE = re.compile(r"\b(?:no|not|nil|none|except|excluding|excludes?|excluded|exclusion|without|unless|other than|apart from|cancel\w*|withdrawn|lifted|revoked)\b", re.I)
GENERIC_HEAD = {"WARNING", "WARNINGS", "THUNDERSTORM", "THUNDERSTORMS", "RAIN", "CONTINUOUS", "HEAVY", "STRONG", "WINDS", "WIND", "ALERT", "SEVERE", "DANGER",
                "CATEGORY", "SECTION", "A", "B", "C", "D", "FOR", "LAND", "AREAS", "STATE", "STATES", "OF", "THE", "AND", "I", "II", "III", "IV", "V", "WP", "W", "P",
                "WILAYAH", "PERSEKUTUAN", "PULAU", "PINANG", "NEGERI", "SEMBILAN", "KUALA", "LUMPUR", "N"}


def _state_key(n):
    x = re.sub(r"^(?:w\.?p\.?|wilayah persekutuan)\s+", "", _norm(n))
    x = {"pulau pinang": "penang", "n. sembilan": "negeri sembilan", "malacca": "melaka"}.get(x, x)
    return x if x in STATE_KEYS else None


def _norm_states(t):
    """State names and the federal-territory prefixes match across line breaks."""
    for n in [x for x in OTHER_STATES if " " in x]:
        t = re.sub(_spaced(n), lambda m: re.sub(r"\s+", " ", m.group(0)), t, flags=re.I)
    return re.sub(r"\b(W\.?\s?P\.?|Wilayah\s+Persekutuan)\s+(?=Labuan|Kuala\s+Lumpur|Putrajaya)", lambda m: re.sub(r"\s+", " ", m.group(1)) + " ", t, flags=re.I)


def _protect_compounds(t):
    for i, rx in enumerate(COMPOUND_RES):
        t = rx.sub(lambda m, i=i: m.group(1) + "\x02%d\x03" % i, t)
    return t


def _restore_compounds(t):
    return re.sub(r"\x02(\d+)\x03", lambda m: SORTED_COMPOUNDS[int(m.group(1))], t).replace("\x01", ".")


def _clean_scope(ctx, alt):
    """A heading or scope names the state 'plainly' only when nothing but the state and generic warning words is left."""
    t = re.sub(alt, " ", (ctx or "").upper(), flags=re.I)
    t = re.sub("(?:" + "|".join(_spaced(x) for x in OTHER_STATES) + ")", " ", t, flags=re.I)
    return all(w in GENERIC_HEAD for w in re.findall(r"[A-Z]+", t))


def _extract_clause(text):
    """(intro, clause, tail): the clause is the text after "over [the states of]" up to the first depth-0 until / within the period / sentence end."""
    t = _protect_compounds(re.sub(r"\b(W\.P|N)\.", "\\1\x01", text, flags=re.I))
    t = re.sub(r"^\s*(?:[IVX]+|\d+)\)\s*", "", t)
    mo = re.search(r"\bover\s+(?:the\s+(?:areas?|states?|districts?|divisions?)\s+of\s+)?", t, re.I)
    intro = t[:mo.start()] if mo else ""
    c = t[mo.end():] if mo else t
    term = re.compile(r"(?:\s(?:until|within the period)\b|\.(?:\s|$))", re.I)
    depth = 0
    for i, ch in enumerate(c):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and term.match(c, i):
            return intro, c[:i], _restore_compounds(c[i:])
    return intro, c, ""


def _split_clause(c):
    """Entries at bracket depth 0, split on , ; • newline and the words and / dan."""
    out, depth, cur, bal, i = [], 0, "", True, 0
    while i < len(c):
        ch = c[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                bal, depth = False, 0
        if depth == 0:
            if ch in ",;•\n":
                out.append(cur)
                cur = ""
                i += 1
                continue
            m = re.match(r"\s(?:and|dan)\s", c[i:], re.I)
            if m:
                out.append(cur)
                cur = ""
                i += len(m.group(0))
                continue
        cur += ch
        i += 1
    out.append(cur)
    if depth != 0:
        bal = False
    return [e for e in (_restore_compounds(x).strip() for x in out) if e], bal


def area_coverage(text, state, division=None, district=None, ctx=""):
    """Same reading as areaCoverage() in index.html: True / False / None (cannot tell).
    A closed reading of a warning's area clause: the whole text must fit the grammar (a short weather intro, "over [the states of]", a
    clause of entries that are state names, known divisions or names in the owning state's vocabulary with at most one bracket list each,
    and a strict time tail). Anything else is None. A positive survives only an unresolved *list* inside a recognised entry; free text in the
    name position, negation, overlapping place names and unknown parentage are structural failures."""
    if not state:
        return None
    sk = _state_key(state)
    alt = "(?:" + "|".join(_spaced(x) for x in STATE_ALIAS.get(state, [state])) + ")"
    s_re = re.compile(r"(^|[^A-Za-z])" + alt + r"(?=$|[^A-Za-z])", re.I)
    q_re = re.compile(r"\b(?:Eastern|Western|Northern|Southern|Central|Coastal|Interior|Upper|Lower)\s+" + alt + r"(?=$|[^A-Za-z])", re.I)
    ctx_text = ctx or ""
    text = _norm_states(text)

    def plain(t):
        return q_re.sub(" ", t)

    in_text, in_ctx = bool(s_re.search(plain(text))), bool(s_re.search(plain(ctx_text)))
    if not in_text and not in_ctx:  # the state is not named: not covered (a qualified region such as "Eastern Sabah" is unresolved)
        return None if (q_re.search(text) or q_re.search(ctx_text)) else False
    if NEGATION_RE.search(text) or NEGATION_RE.search(ctx_text):  # negation, exclusion or cancellation wording in the text or its heading
        return None
    if ctx_text.strip() and not _clean_scope(ctx_text, alt):  # a heading with anything but the state and generic warning words is unresolved
        return None
    intro, clause, tail = _extract_clause(text)
    if not INTRO_RE.match(re.sub(r"\s+", " ", intro).strip()) or not TAIL_RE.match(tail):  # unsupported introduction or time tail
        return None
    entries, balanced = _split_clause(clause)
    if not balanced or not entries:
        return None
    divs = DIVISIONS.get(state)
    dkey = {k.lower(): k for k in divs} if divs else {}
    vocab = KNOWN_ST.get(sk, set())
    dist_set = KNOWN_DIST.get(sk, set())
    dist_names = {_canon(d) for ds in (divs or {}).values() for d in ds}

    def district_divs(n):
        return {_norm(dv) for dv, ds in (divs or {}).items() if any(_canon(d) == n for d in ds)}

    dist = _canon(district) if district else None
    adiv = _norm(division) if division else None
    if not adiv and dist:  # derive a missing division from a known district
        dd = district_divs(dist)
        if len(dd) == 1:
            adiv = next(iter(dd))
    overlap = OVERLAPS.get(dist, []) if dist else []
    flags = {"overlap": False, "town": False}

    def note(ns):
        for n in ns:
            if n in overlap or (dist and PARENTS.get(n) == dist):  # a listed smaller region touches its own parent district, and only that
                flags["overlap"] = True
            if n in PARENTS:
                continue
            if n not in dist_set and not (divs and n in dkey):  # a town or island with unknown containment can never prove exclusion
                dd = district_divs(n)
                if not (adiv and dd and adiv not in dd):
                    flags["town"] = True

    def eval_list(lst, parent_div):
        items = [_restore_compounds(x).strip() for x in re.split(r"\s*(?:,|;|•)\s*|\s+(?:and|dan)\s+", _protect_compounds(lst), flags=re.I)]
        if not items or any(not x for x in items):
            return None
        ns = [_canon(x) for x in items]
        for n in ns:
            if n not in vocab:
                return None
            if parent_div and divs:
                if n in dkey and n not in dist_names:
                    return None
                if parent_div not in district_divs(n):
                    return None  # unknown, other-state, division-only or unrelated-division members: unresolved
        return ns

    cur = sk if in_ctx else None
    pos = unres = bad = pending = restricted = False
    pending_owner = None
    for raw in entries:
        prefix, rest = None, raw
        pm = _PFX.match(raw)
        if pm:
            prefix, rest = pm.group(1), pm.group(2)
        mm = re.match(r"^\s*([^()]*?)\s*(?:\(([^()]*)\))?\s*$", rest, re.S)
        name = mm.group(1).strip() if mm else ""
        lst = mm.group(2) if mm and mm.group(2) is not None else None
        pk = _state_key(prefix) if prefix else None
        nk = _state_key(name) if name else None
        if pending and (prefix or nk):  # an unfinished header is fulfilled only by a subordinate entry, never by another state
            if pending_owner == sk or pending_owner is None:
                bad = True
            pending = False
        elif pending and name:
            pending = False
        if prefix:
            pending = (not name) and lst is None
            pending_owner = pk
            cur = pk
        if prefix and nk and nk != pk:  # a prefix and a state name that disagree: ownership unclear
            if nk == sk or pk == sk or s_re.search(plain(raw)):
                bad = True
            continue
        if not mm or (not name and not prefix) or (not name and lst is not None):
            if cur == sk or cur is None or s_re.search(plain(raw)):
                bad = True  # an entry that does not parse belongs to the state in effect, and must not hide the queried state
            continue
        if not name:
            continue  # "Sabah:" on its own only sets the state (checked when the next entry or the end arrives)
        if nk:
            cur = nk
            if nk != sk:
                if s_re.search(plain(raw)):
                    bad = True  # another state's entry must not hide this state, even inside its bracket list
                continue
            if lst is None:  # a state entry: the whole state, or only the districts in its list
                pos = True
                continue
            ns = eval_list(lst, None)
            if ns is None:
                unres = True
                continue
            restricted = True
            note(ns)
            if divs:
                for n in ns:  # a division named in a state list counts only when it is not also a district name
                    if n in dkey and n not in dist_names and adiv == n:
                        pos = True
            if dist and dist in ns:
                pos = True
            if not pos and not dist:  # with no district, a list wholly outside the known division still excludes
                outside = all(adiv and ((n in dkey and n not in dist_names and adiv != n) or (district_divs(n) and adiv not in district_divs(n))) for n in ns)
                if not outside:
                    unres = True
            continue
        if cur is None:
            bad = True  # cannot say which state this item belongs to
            continue
        if cur != sk:
            if s_re.search(plain(rest)):
                bad = True
            continue
        n = _canon(name)
        if divs and n in dkey:  # a Sabah/Sarawak division, optionally restricted to a list of its districts
            if lst is None:
                if not adiv:
                    unres = True
                elif adiv == n:
                    pos = True
                continue
            ns = eval_list(lst, n)
            if ns is None:
                unres = True
                continue
            restricted = True
            note(ns)
            if dist:
                if dist in ns and (not adiv or adiv == n):
                    pos = True
            elif not adiv or adiv == n:
                unres = True
            continue
        if n in vocab and lst is None:  # a district item
            restricted = True
            note([n])
            if not dist:
                dd = district_divs(n)
                if adiv and dd and adiv not in dd:
                    continue
                unres = True
                continue
            if n == dist:
                pos = True
            continue
        bad = True  # free text or an unknown name where a place is expected: structural failure
    if pending and (pending_owner == sk or pending_owner is None):
        bad = True  # a state header with nothing under it
    if bad:
        return None
    if pos:
        return True
    if unres or flags["overlap"] or flags["town"]:
        return None
    return None if (restricted and dist and dist not in dist_set) else False


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
