// Integration test: the page's own assessWarnings() with stubbed warnings, so the presentation layer is checked together with the parser.
//   node tests/test_assess.js            (from the repository root)
//   INDEX_HTML=path/to/index.html node test_assess.js
const fs = require('fs'), path = require('path');
const here = __dirname, root = path.resolve(here, '..');
const html = fs.readFileSync(process.env.INDEX_HTML || (fs.existsSync(path.join(root, 'index.html')) ? path.join(root, 'index.html') : path.join(here, 'index.html')), 'utf8');
const js = /<script>([\s\S]*)<\/script>/.exec(html)[1];
const line = (a, b) => { const i = js.indexOf(a); if (i < 0) throw new Error('missing ' + a); const j = js.indexOf(b, i); return js.slice(i, j); };
const block = a => { const i = js.indexOf(a); if (i < 0) throw new Error('missing ' + a); const m = /\n(?=(?:const|function|let|async function|\/\/ ===)[ \s])/.exec(js.slice(i + 1)); return js.slice(i, m ? i + 1 + m.index : undefined); };
const code = [line('const reEsc=', '\n'), line('const nameRe=', '\n'), line('const OTHER_STATES=', '\n'), line('const STATE_ALIAS=', '\n'), line('const DIVISIONS=', '\nconst divisionOf'),
  line('const P=', '\n'), line('const myt=', '\n'), line('const ended=', '\n'), block('const effFrom='), line('// COVERAGE BEGIN', '// COVERAGE END'), block('function assessWarnings(')].join('\n');
const make = warnings => new Function('D', code + '\nreturn assessWarnings;')({ status: { metmalaysia_warnings: { ok: true } }, warnings, warnings_retrieved: warnings.length });
const tail = ' until 6:00 PM; Monday, 9 November 2026.';
const sub = (text, extra = {}) => ({ text, from: '2000-01-01T00:00:00', to: '2099-01-01T00:00:00', scope: null, sea: false, title: 'Thunderstorms Warning', ...extra });
const bulletin = (text, extra = {}, sextra = {}) => ({ heading: 'THUNDERSTORMS WARNING', issued: '2026-11-09T07:00:00', kind: 'thunder', text, valid_from: null, valid_to: null, subsections: [sub(text, sextra)], ...extra });
const kk = { state: 'Sabah', division: 'West Coast', district: 'Kota Kinabalu' };
const cases = [
  ['alias spelling stays undetermined (Pulau Pinang)', 'Thunderstorms over Pulau Pinang (Unknown District)' + tail, { state: 'Penang', division: null, district: 'Timur Laut' }, { undetermined: 1 }],
  ['alias spelling stays undetermined (N. Sembilan)', 'Thunderstorms over N. Sembilan (Unknown District)' + tail, { state: 'Negeri Sembilan', division: null, district: 'Seremban' }, { undetermined: 1 }],
  ['line break inside a state name is covered', 'Thunderstorms over Pulau\nPinang (Timur Laut)' + tail, { state: 'Penang', division: null, district: 'Timur Laut' }, { active: 1 }],
  ['line break inside N. Sembilan is covered', 'Thunderstorms over N.\n Sembilan (Seremban)' + tail, { state: 'Negeri Sembilan', division: null, district: 'Seremban' }, { active: 1 }],
  ['Eastern Sabah is undetermined for KK', 'Thunderstorms over Eastern Sabah' + tail, kk, { undetermined: 1 }],
  ['West Coast covers KK', 'Thunderstorms over Sabah: West Coast' + tail, kk, { active: 1 }],
  ['Kudat alone does not cover KK', 'Thunderstorms over Sabah: Kudat' + tail, kk, {}],
  ['qualification makes KK undetermined', 'Thunderstorms over Sabah: West Coast, Subject To Confirmation' + tail, kk, { undetermined: 1 }],
  ['a bulletin that does not name the state is ignored', 'Thunderstorms over Selangor' + tail, kk, {}],
];
let bad = 0;
cases.forEach(([name, text, area, want]) => {
  const r = make([bulletin(text)])(area), got = { active: r.active.length, upcoming: r.upcoming.length, unparsed: r.unparsed.length, undetermined: r.undetermined.length };
  const exp = { active: 0, upcoming: 0, unparsed: 0, undetermined: 0, ...want }, ok = Object.keys(exp).every(k => got[k] === exp[k]);
  if (!ok) { bad++; console.log('FAIL', name, JSON.stringify(got), 'expected', JSON.stringify(exp)); }
});
const noEnd = make([bulletin('Thunderstorms over Sabah: West Coast' + tail, {}, { to: null })])(kk);
if (noEnd.unparsed.length !== 1) { bad++; console.log('FAIL a matching warning without an end time must be unparsed', noEnd.unparsed.length); }
console.log('assessWarnings: ' + bad + ' failures of ' + (cases.length + 1));
process.exit(bad ? 1 : 0);
