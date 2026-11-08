// Run the shared warning-coverage cases through index.html's areaCoverage.
//   node tests/test_coverage.js            (from the repository root)
//   INDEX_HTML=path/to/index.html node test_coverage.js
const fs = require('fs'), path = require('path');
const here = __dirname, root = path.resolve(here, '..');
const html = fs.readFileSync(process.env.INDEX_HTML || (fs.existsSync(path.join(root, 'index.html')) ? path.join(root, 'index.html') : path.join(here, 'index.html')), 'utf8');
const js = /<script>([\s\S]*)<\/script>/.exec(html)[1];
const span = (a, b) => { const i = js.indexOf(a); if (i < 0) throw new Error('missing ' + a); const j = js.indexOf(b, i); return js.slice(i, j); };
const code = [span('const reEsc=', '\n'), span('const nameRe=', '\n'), span('const OTHER_STATES=', '\n'), span('const STATE_ALIAS=', '\n'), span('const DIVISIONS=', '\nconst divisionOf'), span('// COVERAGE BEGIN', '// COVERAGE END')].join('\n');
const areaCoverage = new Function(code + '\nreturn areaCoverage;')();
const data = JSON.parse(fs.readFileSync(path.join(here, 'coverage_cases.json'), 'utf8'));
let bad = 0;
data.cases.forEach(c => { const r = areaCoverage(c.text, data.areas[c.area], c.ctx); if (r !== c.expect) { bad++; console.log('FAIL', c.area, JSON.stringify(c.ctx), '->', r, 'expected', c.expect, '|', c.text.slice(0, 80)); } });
console.log('javascript: ' + bad + ' failures of ' + data.cases.length);
process.exit(bad ? 1 : 0);
