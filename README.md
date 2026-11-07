# Sandy · Kota Kinabalu weather dashboard

Live page: https://roychiew-cell.github.io/sandy-weather-kk/

- `index.html` — the dashboard. Everything on it is derived from `data/latest.json` and `call.json` when the page opens; nothing about the weather is written into the page itself. While open it re-checks the data every 30 minutes and on return to the tab, re-judges warning status every minute, and re-renders at midnight.
- `scripts/fetch.py` — fetches every source and writes `data/latest.json`. Raw responses, with URL and retrieval time, are archived under `data/raw/`; the last 28 run snapshots are kept under `data/history/`.
- `call.json` — Sandy's written forecast for one date (`valid_for`). Written by Claude (Sandy) when Roy asks for a weather update, with the time it was written, the data time it was based on and the radar image looked at (`data/radar/`). After that date the page shows it as an archived forecast and drops the "Today high" tile.
- `.github/workflows/refresh.yml` — at 07:00 and 19:00 Malaysia time, on GitHub's servers: fetch → commit `data/` (rebase and retry if the branch moved) → deploy the site to GitHub Pages with `actions/deploy-pages`. A push to the page, call, data, scripts or workflow also deploys (without fetching). GitHub pauses scheduled workflows on repositories with no activity for 60 days; the job's own commits count as activity. GitHub may delay or skip a scheduled run; the page shows a banner when the data is over 18 hours old.

Sources are chosen for Kota Kinabalu: MetMalaysia forecast (town Tn196 and district Ds554) and warnings (split into dated subsections) via api.data.gov.my; Kota Kinabalu airport (WBKK) observations (48 h) and TAF via aviationweather.gov; the ECMWF ensemble, eight named global models plus the Open-Meteo blend, their 24-hour-ahead runs for the daily comparison, and CAMS air quality via open-meteo.com; MetMalaysia's composite radar image is embedded for reference. The page's last table lists each one with its status in the latest run.

To refresh by hand: run the workflow from the Actions tab, or `python3 -I scripts/fetch.py` locally and push `data/`.
