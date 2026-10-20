// UI tests of static/modules/billing/app.js in jsdom (SheetJS + core.js + app.js evaluated into a bare page).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const XLSX = require('xlsx');

const SRC = p => fs.readFileSync(path.join(__dirname, '../../static/modules/billing', p), 'utf8');
const bytes = wb => new Uint8Array(XLSX.write(wb, { type: 'array', bookType: 'xlsx' }));
const book = sheets => ({ SheetNames: Object.keys(sheets), Sheets: Object.fromEntries(Object.entries(sheets).map(([n, a]) => [n, XLSX.utils.aoa_to_sheet(a)])) });
const tick = (ms = 60) => new Promise(r => setTimeout(r, ms));

async function boot(hash) {
  const downloads = [];
  const dom = new JSDOM('<!doctype html><body><div id="bsuite"></div></body>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/billing/' + (hash || '') });
  const w = dom.window;
  w.XLSX = Object.assign({}, XLSX, { writeFile: (wb, name) => downloads.push({ wb, name }) });
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.eval(SRC('core.js')); w.eval(SRC('app.js'));
  const d = w.document;
  async function upload(inputIndex, fileBytes, name) {
    const input = d.querySelectorAll('input.bs-file')[inputIndex];
    Object.defineProperty(input, 'files', { value: [new w.File([fileBytes], name)], configurable: true });
    input.dispatchEvent(new w.Event('change'));
    await tick(120);
  }
  const text = () => d.getElementById('bsuite').textContent;
  const click = el => { el.click(); return tick(150); };
  const btn = label => [...d.querySelectorAll('button')].find(b => b.textContent.includes(label));
  return { w, d, upload, text, click, btn, downloads };
}

const LR_H = ['CN_NO', 'CN_FREIGHT_PAID_MODE ', 'adr no date branch', 'CN_POD_UPLOAD_STS', 'CN_DELIVERY_DATE', 'CN_POD_REC_DATE', 'BILLING BR', 'BILLING PARTY NAME'];
const LR_ROWS = [
  ['C1', 'TBB', '111,10-JAN-26,101', 'UPLOADED', '', '', 101, 'ACME LTD'],
  ['C2', 'TO PAY', '', 'UPLOADED', '', '', 101, 'ACME LTD'],
  ['C3', 'TBB', '', 'UPLOADED', '10-FEB-26', '', 102, 'ZETA CORP'],
  ['C4', 'TBB', '', '', '10-FEB-26', '', 102, 'ZETA CORP'],
  ['<img src=x onerror="window.PWNED=1">', 'TBB', '', 'UPLOADED', '11-FEB-26', '', 102, '<b>bold</b>']
];

test('three tabs, hash routing and the sidebar highlight hook', async () => {
  const { d, w, click } = await boot('#region');
  const tabs = [...d.querySelectorAll('.bs-tabs .tab')].map(t => t.textContent);
  assert.deepEqual(tabs, ['1. ADR / NET Query', '2. Branch-wise Data', '3. Region Creation']);
  assert.ok(d.querySelector('.bs-tabs .tab.active').textContent.includes('Region'));
  await click(d.querySelectorAll('.bs-tabs .tab')[0]);
  assert.equal(w.location.hash, '#query');
});

test('ADR query: odd header spellings are detected, run reconciles, and file contents can never inject HTML', async () => {
  const t = await boot();
  await t.upload(0, bytes(book({ LR: [LR_H, ...LR_ROWS] })), 'lr.xlsx');
  assert.match(t.text(), /All required columns found/);
  await t.click(t.btn('Apply filters'));
  assert.match(t.text(), /Reconciled: raw = ADR \+ remaining \+ removed \(5 = 1 \+ 2 \+ 2\)/);
  await t.click([...t.d.querySelectorAll('.tab')].find(b => /Remaining LR Report/.test(b.textContent)));
  assert.match(t.text(), /<img src=x onerror/);                                 // shown as plain text in the preview...
  assert.match(t.text(), /<b>bold<\/b>/);
  assert.equal(t.d.querySelectorAll('#bsuite img, #bsuite td b').length, 0);        // ...never as an element
  assert.equal(t.w.PWNED, undefined);
});

test('ADR query: a missing required column blocks the run with a clear message (old tool deleted rows silently)', async () => {
  const t = await boot();
  const h = LR_H.filter(x => x !== 'CN_POD_UPLOAD_STS');
  await t.upload(0, bytes(book({ LR: [h, ...LR_ROWS.map(r => r.filter((_, i) => i !== 3))] })), 'lr.xlsx');
  assert.match(t.text(), /Choose the column for: POD upload status/);
  await t.click(t.btn('Apply filters'));
  assert.match(t.text(), /Column\(s\) not mapped: POD upload status/);
  assert.equal(t.d.querySelector('.bs-stats'), null);                            // nothing was processed
});

test('freight modes are listed with counts and can be extended', async () => {
  const t = await boot();
  await t.upload(0, bytes(book({ LR: [LR_H, ...LR_ROWS] })), 'lr.xlsx');
  const modes = [...t.d.querySelectorAll('.bs-mode')].map(x => x.textContent.replace(/\s+/g, ' ').trim());
  assert.ok(modes.some(m => /^TBB ?4$/.test(m)) && modes.some(m => /^TO PAY ?1$/.test(m)) && modes.some(m => /^PREPAID/.test(m)), modes.join(' | '));
});

test('hand-off: ADR query result flows into Branch-wise and Region without re-uploading', async () => {
  const t = await boot();
  await t.upload(0, bytes(book({ LR: [LR_H, ...LR_ROWS.slice(0, 4)] })), 'lr.xlsx');
  await t.click(t.btn('Apply filters'));
  await t.click(t.btn('Send to Branch-wise Data'));
  assert.ok(t.d.querySelector('.bs-tabs .tab.active').textContent.includes('Branch-wise'));
  assert.match(t.text(), /ADR \/ NET Query result/);
  assert.match(t.text(), /B\.CODE column not identified/);                        // LR export has no B.CODE header: the user picks it
  // pick the B.CODE column ("BILLING BR") in the sheet column selectors
  const selects = [...t.d.querySelectorAll('details.bs-adv select')];
  for (const sel of selects.filter((_, i) => i % 3 === 0)) {                      // first select of each sheet = B.CODE
    const idx = [...sel.options].find(o => /BILLING BR/.test(o.textContent)).value;
    sel.value = idx; sel.dispatchEvent(new t.w.Event('change')); await tick(200);
  }
  assert.doesNotMatch(t.text(), /B\.CODE column not identified/);
  // single-branch mode needs no Summary
  const radios = [...t.d.querySelectorAll('input[name=bmode]')];
  radios[1].checked = true; radios[1].dispatchEvent(new t.w.Event('change'));
  t.d.querySelector('textarea').value = '101';
  await t.click(t.btn('Generate workbook'));
  assert.match(t.text(), /What will be in the workbook/);
  const stats = [...t.d.querySelectorAll('.bs-stat')].map(x => x.textContent);
  assert.deepEqual(stats, ['2Sheets', '1Records']);                               // ADR_101 (1 row) + POD_101 (no rows, header only)
  await t.click(t.btn('Download Excel'));
  assert.equal(t.downloads.length, 1);
  assert.match(t.downloads[0].name, /^Branch_Data_\d{4}-\d{2}-\d{2}\.xlsx$/);
  assert.deepEqual(t.downloads[0].wb.SheetNames, ['ADR_101', 'POD_101']);
});

test('region: raw + Summary -> workbook with reconciliation and month SUMMARY (months built from data when missing)', async () => {
  const t = await boot('#region');
  const raw = book({
    'ADR MADE BUT NOT BILLED': [['S N.', 'B.CODE', 'BRANCH', 'BILLING PARTY NAME', 'ADR DATE'], [1, 101, 'DELHI', 'ACME', '10-FEB-26'], [2, 101, 'DELHI', 'ACME', '10-APR-26'], [3, 999, 'OUT', 'ACME', '10-APR-26']],
    'NET POD BUT NOT BILLED': [['S N.', 'B.CODE', 'BRANCH', 'BILLING PARTY NAME', 'DELIVERY DATE'], [1, 101, 'DELHI', 'ACME', '10-MAR-26']]
  });
  await t.upload(0, bytes(raw), 'raw.xlsx');
  assert.match(t.text(), /"ADR MADE BUT NOT BILLED": B\.CODE = "B\.CODE"/);
  await t.upload(0, bytes(book({ Summary: [['S N.', 'B.CODE', 'BRANCH'], [1, 101, 'DELHI']] })), 'sum.xlsx');
  assert.match(t.text(), /1 branch\(es\), 0 parties, no month columns/);
  await t.click(t.btn('Generate region workbook'));
  assert.match(t.text(), /Reconciliation/);
  assert.match(t.text(), /Outside this region/);
  assert.match(t.text(), /built from the data/);
  await t.click(t.btn('Download Excel'));
  assert.deepEqual(t.downloads[0].wb.SheetNames, ['ADR', 'NET POD', 'SUMMARY']);
});

test('region: generate without a Summary or raw data explains what is missing instead of failing', async () => {
  const t = await boot('#region');
  await t.click(t.btn('Generate region workbook'));
  assert.match(t.text(), /Load the raw workbook first/);
});
