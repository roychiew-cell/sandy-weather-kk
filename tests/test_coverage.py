"""Run the shared warning-coverage cases through fetch.py's area_coverage.
    python3 tests/test_coverage.py            (from the repository root)
    FETCH_PY=path/to/fetch.py python3 test_coverage.py   (when fetch.py sits elsewhere, e.g. in a review bundle)"""
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
root = here.parent
fetch = pathlib.Path(os.environ["FETCH_PY"]) if os.environ.get("FETCH_PY") else next(p for p in (root / "scripts" / "fetch.py", root / "fetch.py") if p.exists())
src = fetch.read_text(encoding="utf-8")
start, end = src.index("DIVISION_STATES"), src.index("def covers_kk")
ns = {}
exec("import re\n" + src[start:src.index("\n\n", end + 10)], ns)
data = json.loads((here / "coverage_cases.json").read_text(encoding="utf-8"))
bad = 0
for c in data["cases"]:
    a = data["areas"][c["area"]]
    got = ns["area_coverage"](c["text"], a["state"], a["division"], a["district"], c["ctx"])
    if got != c["expect"]:
        bad += 1
        print("FAIL", c["area"], repr(c["ctx"]), "->", got, "expected", c["expect"], "|", c["text"][:80])
print("python: %d failures of %d" % (bad, len(data["cases"])))
sys.exit(1 if bad else 0)
