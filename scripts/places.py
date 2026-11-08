#!/usr/bin/env python3
"""Build places.json: every MetMalaysia forecast location (town, district, state) with approximate
coordinates, so the page can pick the nearest town and district to a GPS point.

Coordinates come from Open-Meteo's geocoding service by name (country MY); each match is a best
guess (preferring a result whose district matches the name, then the most populous) and is marked
with how it was chosen. Run by hand: python3 scripts/places.py  (writes places.json at the repo root).
"""
import json, sys, time, urllib.parse, urllib.request, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MM = "https://api.data.gov.my/weather/forecast/?limit=100000"
GEO = "https://geocoding-api.open-meteo.com/v1/search?name={q}&count=100&language=en&format=json"
UA = "sandy-weather-kk places builder (github.com/roychiew-cell/sandy-weather-kk)"
ALIAS = {"Georgetown": "George Town", "Bandaraya Melaka": "Melaka", "Kota Setar": "Alor Setar", "Labuk & Sugut": "Beluran",
         "Tanah Tinggi Cameron": "Tanah Rata", "Tanah Rata - Brinchang": "Tanah Rata", "Jalan Duta": "Kuala Lumpur",
         "Kelana Jaya": "Petaling Jaya", "Sri Gombak": "Gombak", "Gombak (Taman Perwira )": "Gombak", "Bayan Baru": "Bayan Lepas",
         "Sungai Dua - Sungai Nibong": "Gelugor", "Bandar Baru Seri Manjung": "Seri Manjung", "Manjung": "Seri Manjung",
         "Penawar": "Bandar Penawar", "Muallim": "Tanjung Malim", "Larut, Matang Dan Selama": "Taiping",
         "Setiu": "Permaisuri", "Besut": "Jerteh", "Samarahan": "Kota Samarahan", "Meradong": "Bintangor", "Subis": "Batu Niah",
         "Telang Usan": "Long Lama", "Bandar Tun Abdul Razak, Jengka": "Jengka", "Perak Tengah": "Seri Iskandar",
         "Hilir Perak": "Teluk Intan", "Batang Padang": "Tapah", "Kubang Pasu": "Jitra", "Kuala Muda": "Sungai Petani",
         "Lipis": "Kuala Lipis", "Alor Star": "Alor Setar", "WP Kuala Lumpur": "Kuala Lumpur", "WP Putrajaya": "Putrajaya",
         "WP Labuan": "Labuan", "Pulau Pinang": "George Town", "Barat Daya": "Balik Pulau",
         "Timur Laut": "George Town", "Seberang Perai Utara": "Butterworth", "Seberang Perai Tengah": "Bukit Mertajam",
         "Seberang Perai Selatan": "Nibong Tebal", "Hulu Langat": "Kajang", "Hulu Selangor": "Kuala Kubu Bharu",
         "Kuala Langat": "Banting", "Hulu Terengganu": "Kuala Berang", "Hulu Perak": "Gerik", "Kinta": "Ipoh",
         "Larut Matang dan Selama": "Taiping", "Larut Dan Matang": "Taiping", "Kuala Kangsar": "Kuala Kangsar"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(name):
    q = ALIAS.get(name, name)
    try:
        res = get(GEO.format(q=urllib.parse.quote(q))).get("results") or []
    except Exception as e:  # noqa: BLE001
        return None, "lookup failed: %s" % e
    res = [r for r in res if r.get("country_code") == "MY" and "latitude" in r]
    if not res:
        return None, "no match in Malaysia"
    exact = [r for r in res if (r.get("admin2") or "").lower() == q.lower()]
    if exact:
        pick, how = exact[0], "district match"
    else:
        adm = [r for r in res if str(r.get("feature_code", "")).startswith("ADM")]
        if adm:
            pick, how = adm[0], "administrative area"
        else:
            pick, how = max(res, key=lambda r: r.get("population") or 0), "most populous match"
    return {"lat": round(pick["latitude"], 4), "lon": round(pick["longitude"], 4), "state": pick.get("admin1"),
            "district": pick.get("admin2"), "matched": pick.get("name"), "how": how}, None


# state centroids (approximate, hand-set): geocoding a state name by itself is unreliable
STATES = {"Perlis": (6.44, 100.20), "Kedah": (6.0, 100.6), "Pulau Pinang": (5.40, 100.33), "Perak": (4.75, 101.0),
          "Kelantan": (5.3, 102.0), "Terengganu": (4.9, 103.0), "Pahang": (3.8, 102.5), "Selangor": (3.2, 101.5),
          "WP Kuala Lumpur": (3.14, 101.69), "WP Putrajaya": (2.93, 101.69), "Negeri Sembilan": (2.75, 102.0),
          "Melaka": (2.25, 102.3), "Johor": (2.0, 103.5), "Sarawak": (2.5, 112.5), "Sabah": (5.4, 117.0), "WP Labuan": (5.30, 115.22)}


# corrections by forecast id, applied before anything else (geocoder matches that were the wrong place; Astra R17)
OVERRIDE = {"Ds019": (5.12, 100.49, "Perak", "Kerian", "Parit Buntar, district seat (hand-set)"),
            "Ds080": (2.82, 103.49, "Pahang", "Rompin", "Kuala Rompin, district seat (hand-set)"),
            "Ds078": (2.20, 102.25, "Melaka", "Melaka Tengah", "district centre (hand-set)"),
            "Ds040": (4.02, 101.02, "Perak", "Hilir Perak", "Teluk Intan, district seat (hand-set)"),
            "Tn119": (2.196, 102.249, "Melaka", "Melaka Tengah", "Melaka city (hand-set)"),
            "Tn175": (5.33, 100.28, "Pulau Pinang", "Barat Daya", "Sungai Ara, Penang (hand-set)"),
            "Tn073": (3.17, 101.67, "WP Kuala Lumpur", "Kuala Lumpur", "Jalan Duta area (hand-set)"),
            "Tn141": (4.63, 101.43, "Kelantan", "Gua Musang", "Lojing Highlands (hand-set, approximate; ~75 km from Gua Musang town)")}
HAND_STATE = {"Nabawan": "Sabah", "Tongod": "Sabah", "Kota Marudu": "Sabah", "Yan": "Kedah", "Asajaya": "Sarawak", "Meradong": "Sarawak",
              "Setiu": "Terengganu", "Nilai": "Negeri Sembilan", "Bera": "Pahang", "Bandar Tun Abdul Razak, Jengka": "Pahang",
              "Sungai Dua - Sungai Nibong": "Pulau Pinang", "Gelugor": "Pulau Pinang"}
# names the geocoder cannot place: approximate hand-set positions (town centre or district seat)
HAND = {"Nabawan": (5.06, 116.45), "Tongod": (5.27, 117.17), "Kota Marudu": (6.48, 116.78), "Yan": (5.80, 100.38),
        "Asajaya": (1.53, 110.62), "Meradong": (2.17, 111.63), "Setiu": (5.55, 102.77), "Nilai": (2.81, 101.80),
        "Bera": (3.17, 102.52), "Bandar Tun Abdul Razak, Jengka": (3.75, 102.55), "Sungai Dua - Sungai Nibong": (5.33, 100.30),
        "Gelugor": (5.37, 100.30)}


def main():
    rows = get(MM)
    locs = {}
    for r in rows:
        loc = r.get("location") or {}
        i, n = loc.get("location_id"), loc.get("location_name")
        if i and n:
            locs[i] = n
    prev = {}
    try:  # reuse earlier matches; only retry the ones that failed
        for p in json.loads((ROOT / "places.json").read_text(encoding="utf-8")).get("places", []):
            if "lat" in p:
                prev[p["id"]] = p
    except Exception:  # noqa: BLE001
        pass
    out = {"built": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.gmtime(time.time() + 8 * 3600)),
           "source": "MetMalaysia forecast locations (api.data.gov.my) geocoded by name with Open-Meteo geocoding; states are hand-set centroids; approximate",
           "places": []}
    kinds = {"Tn": "town", "Ds": "district", "St": "state", "Dv": "division", "Rc": "recreation"}
    towns = []
    for i in sorted(locs):
        kind = kinds.get(i[:2])
        if kind not in ("town", "district", "state"):
            continue
        rec = {"id": i, "name": locs[i], "kind": kind}
        if i in OVERRIDE:
            la, lo, st, di, how = OVERRIDE[i]
            rec.update({"lat": la, "lon": lo, "state": st, "district": di, "how": how})
        elif kind == "state":
            c = STATES.get(locs[i])
            if c:
                rec.update({"lat": c[0], "lon": c[1], "state": locs[i], "how": "state centroid (hand-set)"})
            else:
                rec["error"] = "no centroid"
        elif i in prev:
            rec = prev[i]
        else:
            g, err = geocode(locs[i])
            if g:
                rec.update(g)
            else:
                rec["error"] = err
            time.sleep(0.15)
        if kind == "town" and "lat" in rec:
            towns.append(rec)
        out["places"].append(rec)
        print(i, locs[i], "->", (rec.get("lat"), rec.get("lon"), rec.get("state"), rec.get("how")) if "lat" in rec else rec.get("error"), file=sys.stderr)
    # a district with no match takes the position of a town of the same name, else of a town geocoded inside it
    for rec in out["places"]:
        if rec["kind"] != "district" or "lat" in rec:
            continue
        t = next((t for t in towns if t["name"].lower() == rec["name"].lower()), None) or \
            next((t for t in towns if (t.get("district") or "").lower() == rec["name"].lower()), None)
        if t:
            rec.pop("error", None)
            rec.update({"lat": t["lat"], "lon": t["lon"], "state": t.get("state"), "district": rec["name"], "matched": t["name"],
                        "how": "position of town " + t["name"]})
            print(rec["id"], rec["name"], "-> via town", t["name"], file=sys.stderr)
    for rec in out["places"]:
        if "lat" not in rec and rec["name"] in HAND:
            rec.pop("error", None)
            rec.update({"lat": HAND[rec["name"]][0], "lon": HAND[rec["name"]][1], "how": "hand-set (approximate)"})
        if "lat" in rec and not rec.get("state") and rec["name"] in HAND_STATE:
            rec["state"] = HAND_STATE[rec["name"]]
    # audit: records of one kind sharing a position (a tie the nearest-search cannot break) and missing states
    seen = {}
    for rec in out["places"]:
        if "lat" in rec:
            seen.setdefault((rec["kind"], rec["lat"], rec["lon"]), []).append(rec["id"] + " " + rec["name"])
    for k, v in seen.items():
        if len(v) > 1:
            print("AUDIT same position:", k[0], v, file=sys.stderr)
    for rec in out["places"]:
        if "lat" in rec and rec["kind"] != "state" and not rec.get("state"):
            print("AUDIT no state:", rec["id"], rec["name"], file=sys.stderr)
    ok = sum(1 for p in out["places"] if "lat" in p)
    out["count"] = {"total": len(out["places"]), "located": ok}
    (ROOT / "places.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print("places.json: %d places, %d located" % (len(out["places"]), ok))


if __name__ == "__main__":
    main()
