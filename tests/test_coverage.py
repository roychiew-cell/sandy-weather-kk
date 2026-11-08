"""Run the shared warning-coverage cases through scripts/fetch.py's area_coverage (python3 tests/test_coverage.py)."""
import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
src = (root / "scripts" / "fetch.py").read_text(encoding="utf-8")
start, end = src.index("DIVISION_STATES"), src.index("def covers_kk")
ns = {}
exec("import re\n" + src[start:src.index("\n\n", end + 10)], ns)
data = json.loads((root / "tests" / "coverage_cases.json").read_text(encoding="utf-8"))
bad = 0
for c in data["cases"]:
    a = data["areas"][c["area"]]
    got = ns["area_coverage"](c["text"], a["state"], a["division"], a["district"], c["ctx"])
    if got != c["expect"]:
        bad += 1
        print("FAIL", c["area"], repr(c["ctx"]), "->", got, "expected", c["expect"], "|", c["text"][:70])
print("python: %d failures of %d" % (bad, len(data["cases"])))
sys.exit(1 if bad else 0)
