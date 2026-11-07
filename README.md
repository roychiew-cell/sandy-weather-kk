# Sandy · Kota Kinabalu weather dashboard

Live page: see the repository's GitHub Pages link.

- `index.html` — the dashboard. Everything on it is derived from `data/latest.json` and `call.json` when the page opens; nothing about the weather is written into the page itself.
- `scripts/fetch.py` — fetches every source and writes `data/latest.json`. Raw responses, with URL and retrieval time, are archived under `data/raw/`; a copy of each run is kept under `data/history/` for 14 days.
- `call.json` — Sandy's written forecast. Written by Claude (Sandy) when Roy asks for a weather update; carries the time it was written and the data time it was based on. The page flags it when it is more than 30 hours old.
- `.github/workflows/refresh.yml` — runs the fetcher at 07:00 and 19:00 Malaysia time on GitHub's servers and commits the data. GitHub pauses scheduled workflows on repositories with no activity for 60 days; the job's own commits count as activity.

Sources are chosen for Kota Kinabalu: MetMalaysia forecast (town and district) and warnings via api.data.gov.my; Kota Kinabalu airport (WBKK) observations and TAF via aviationweather.gov; ECMWF ensemble, nine global models, their previous-day runs, the Open-Meteo blend and CAMS air quality via open-meteo.com. The page's last table lists each one with its status in the latest run.

To refresh by hand: run the workflow from the Actions tab, or `python3 -I scripts/fetch.py` locally and commit `data/`.
