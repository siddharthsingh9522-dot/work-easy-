// Tests of static/modules/billing/core.js (ADR/NET query, branch-wise, region). Run: node --test tests/js/
// The pytest wrapper runs this file under several time zones (the audited tools broke in IST).
const test = require('node:test');
const assert = require('node:assert/strict');
const XLSX = require('xlsx');
const B = require('../../static/modules/billing/core.js');

/* ------------------------------------------------------------------ helpers */
function roundTrip(wb) { return B.util.readWorkbook(new Uint8Array(XLSX.write(wb, { type: 'array', bookType: 'xlsx' })), 't.xlsx'); }
function wbFrom(sheets, tweak) {
  const wb = { SheetNames: Object.keys(sheets), Sheets: {} };
  wb.SheetNames.forEach(n => { wb.Sheets[n] = XLSX.utils.aoa_to_sheet(sheets[n]); });
  if (tweak) tweak(wb);
  return roundTrip(wb);
}
const grid = (wb, name) => XLSX.utils.sheet_to_json(wb.Sheets[name], { header: 1, defval: '' });
const col = (g, c) => g.map(r => r[c]);

/* ------------------------------------------------------------------ dates */
test('date parser: every supported format, no time-zone dependence, real calendar only', () => {
  const p = B.util.parseDateParts;
  assert.deepEqual(p('29-APR-25'), { y: 2025, m: 4, d: 29 });
  assert.deepEqual(p('26-APR-25 12.00.00.000000 AM'), { y: 2025, m: 4, d: 26 });
  assert.deepEqual(p('29 Apr 2025'), { y: 2025, m: 4, d: 29 });
  assert.deepEqual(p('2026-05-01'), { y: 2026, m: 5, d: 1 });
  assert.deepEqual(p('2026-05-01T10:30:00'), { y: 2026, m: 5, d: 1 });
  assert.deepEqual(p('01/05/2026'), { y: 2026, m: 5, d: 1 });           // day first
  assert.deepEqual(p('01-05-26'), { y: 2026, m: 5, d: 1 });
  assert.deepEqual(p(46143), { y: 2026, m: 5, d: 1 });                  // Excel serial
  assert.deepEqual(p('46143'), { y: 2026, m: 5, d: 1 });
  for (const bad of ['31-FEB-26', '2026', '45000x', 'hello', '', null, undefined, 12, 99999999, '00-JAN-26', '13/13/2026']) {
    assert.equal(p(bad), null, 'should not parse: ' + bad);
  }
});

/* ------------------------------------------------------------------ 1) ADR / NET query */
const H = ['CN_NO', 'CN_FREIGHT_PAID_MODE', 'ADR_NO_DATE_BRANCH', 'CN_POD_UPLOAD_STS', 'CN_DELIVERY_DATE', 'CN_POD_REC_DATE', 'PERIOD'];
function adrTable(rows, headers) { return { headers: headers || H, rows }; }
function adrMap(headers) { const m = B.adr.detectColumns(headers || H); return m; }

test('ADR: header spelling differences (case, spaces, underscores) never break column detection', () => {
  const m = B.adr.detectColumns(['cn no', 'CN FREIGHT PAID MODE ', ' adr_no_date_branch', 'cn-pod-upload-sts', 'Cn Delivery Date', 'CN_POD_REC_DATE']);
  assert.deepEqual([m.cn, m.freight, m.adr, m.podStatus, m.goDelivery, m.podRec], [0, 1, 2, 3, 4, 5]);
});

test('ADR: a required column that is neither mapped nor skipped blocks the run (the old tool silently deleted rows)', () => {
  const t = adrTable([['1', 'TBB', '', 'UPLOADED', '', '', '']]);
  const map = adrMap(); delete map.podStatus; map.podStatus = null;
  const r = B.adr.run(t, { map, excludeModes: [] });
  assert.ok(r.error && r.missing.includes('podStatus'));
  // explicit skip: nothing is removed for that reason, and the report says so
  map.podStatus = -1;
  const ok = B.adr.run(t, { map, excludeModes: [] });
  assert.equal(ok.stats.remaining, 1);
  assert.ok(ok.warnings.some(w => w.kind === 'skipped'));
});

const FIX = [
  ['A1', 'TBB', '111,10-JAN-26,101', 'UPLOADED', '', '', 'x'],        // ADR, date ok
  ['A2', 'to pay ', '112,10-JAN-26,101', 'UPLOADED', '', '', 'x'],    // freight removed (case/space)
  ['A3', 'TBB', '113,20-JUN-26,102', 'UPLOADED', '', '', 'x'],        // ADR, after cutoff -> removed
  ['A4', 'TBB', '114,,103', 'UPLOADED', '', '', 'x'],                 // ADR with blank date -> kept
  ['A5', 'TBB', '115,garbage,104', 'UPLOADED', '', '', 'x'],          // ADR with unreadable date -> kept + warned
  ['R1', 'TBB', '', 'UPLOADED', '10-FEB-26', '11-FEB-26', 'x'],       // remaining, kept
  ['R2', 'TBB', '', '', '10-FEB-26', '11-FEB-26', 'x'],               // POD status empty -> removed
  ['R3', 'TBB', '', 'UPLOADED', '10-AUG-26', '11-FEB-26', 'x'],       // delivery after cutoff -> removed
  ['R4', 'TBB', '', 'UPLOADED', '10-FEB-26', '10-AUG-26', 'x'],       // POD received after cutoff -> removed
  ['R5', 'TBB', '', 'UPLOADED', '', '', 'x'],                         // blank dates -> kept
  ['R6', 'PREPAID', '', 'UPLOADED', '', '', 'x'],                     // freight removed
  ['A1', 'TBB', '', 'UPLOADED', '10-FEB-26', '', 'x']                 // duplicate CN
];
function runFix(extra) {
  return B.adr.run(adrTable(FIX), Object.assign({ map: adrMap(), excludeModes: ['TO PAY', 'PREPAID'],
    adrCutoff: '2026-03-31', goCutoff: '2026-06-30', podCutoff: '2026-06-30', delimiter: ',' }, extra || {}));
}

test('ADR: pipeline semantics, stage by stage (hand-verified)', () => {
  const r = runFix(), s = r.stats;
  assert.equal(s.raw, 12);
  assert.equal(s.freightExcluded, 2);
  assert.equal(s.adrConfirmedBeforeDate, 4);
  assert.equal(s.adrDateRemoved, 1);
  assert.equal(s.adr, 3);                                  // A1, A4, A5
  assert.equal(s.podStatusRemoved, 1);
  assert.equal(s.goDeliveryRemoved, 1);
  assert.equal(s.podRecRemoved, 1);
  assert.equal(s.remaining, 3);                            // R1, R5, duplicate A1
  assert.equal(s.deleted, 6);
  assert.equal(s.reconciled, true);                        // raw = adr + remaining + removed
  assert.deepEqual(col(r.adr, 0).slice(0, 3), ['A1', 'A4', 'A5']);
  assert.ok(r.warnings.some(w => w.kind === 'date' && /1 ADR row/.test(w.message)));
  assert.ok(r.warnings.some(w => w.kind === 'duplicate' && /1 duplicate/.test(w.message)));
});

test('ADR: cutoff is inclusive; blank cutoffs disable the stage; removed rows say why', () => {
  const same = B.adr.run(adrTable([['x1', 'TBB', '1,31-MAR-26,5', 'U', '', '', '']]), { map: adrMap(), excludeModes: [], adrCutoff: '2026-03-31' });
  assert.equal(same.stats.adr, 1);
  const none = runFix({ adrCutoff: '', goCutoff: '', podCutoff: '' });
  assert.equal(none.stats.adrDateRemoved + none.stats.goDeliveryRemoved + none.stats.podRecRemoved, 0);
  const why = runFix().removed.map(x => x.stage + ': ' + x.reason).join('\n');
  assert.match(why, /Freight Filter: Freight mode "to pay" is excluded/);
  assert.match(why, /POD Status/);
  assert.match(why, /ADR Date Filter: ADR DATE 20-JUN-2026 is after cutoff 31-MAR-2026/);
});

test('ADR: freight mode counts expose modes that are NOT excluded (e.g. PAID vs PREPAID)', () => {
  const r = runFix();
  assert.equal(r.modeCounts['TBB'], 10);
  assert.equal(r.modeCounts['TO PAY'], 1);
  assert.equal(r.modeCounts['PREPAID'], 1);
});

test('ADR: workbook export has report, remaining, removed-with-reasons, summary and optional raw data; technical columns hidden', () => {
  const t = adrTable(FIX), r = runFix();
  const wb = B.adr.buildWorkbook(r, t, { include: { removed: true, summary: true, raw: true }, hide: ['PERIOD'], fileName: 'lr.xlsx', generated: '2026-10-15' });
  assert.deepEqual(wb.SheetNames, ['ADR Report', 'Remaining LR Report', 'Removed Records', 'Summary', 'Raw Data']);
  assert.equal(grid(wb, 'ADR Report')[0].includes('PERIOD'), false);
  assert.deepEqual(grid(wb, 'ADR Report')[0].slice(-3), ['ADR NO', 'ADR DATE', 'ADR BRANCH']);
  assert.equal(grid(wb, 'Removed Records').length, 1 + 6);
  assert.equal(grid(wb, 'Raw Data').length, 1 + 12);
  const sum = grid(wb, 'Summary').map(x => x.join('|')).join('\n');
  assert.match(sum, /Reconciled .*\|YES/);
  assert.match(sum, /Excluded freight modes\|TO PAY, PREPAID/);
  const lean = B.adr.buildWorkbook(r, t, { include: { removed: false, summary: false, raw: false } });
  assert.deepEqual(lean.SheetNames, ['ADR Report', 'Remaining LR Report']);
});

test('ADR: real Excel date cells become DD-MMM-YYYY text (the old export wrote bare serial numbers)', () => {
  const ws = XLSX.utils.aoa_to_sheet([['CN_NO', 'CN_DELIVERY_DATE'], ['1', 46143]]);
  ws.B2.t = 'n'; ws.B2.z = 'dd-mmm-yy';
  const wb = roundTrip({ SheetNames: ['S'], Sheets: { S: ws } });
  const t = B.util.sheetToTable(wb.Sheets.S);
  assert.equal(t.rows[0][1], '01-MAY-2026');                       // 1st of the month stays in May, in every time zone
  assert.equal(t.rows[0][0], '1');
});

test('ADR: duplicate / blank headers are made unique and fully blank rows are dropped', () => {
  const t = B.util.sheetToTable(XLSX.utils.aoa_to_sheet([['A', 'A', ''], [1, 2, 3], ['', '', ''], [4, 5, 6]]));
  assert.deepEqual(t.headers, ['A', 'A_2', 'COL_3']);
  assert.equal(t.rows.length, 2);
});

test('ADR -> next tools: hand-off workbook is accepted by prepareRaw (no sheet renaming / re-upload)', () => {
  const r = runFix(), hand = roundTrip(B.adr.toHandoffWorkbook(r));
  assert.deepEqual(hand.SheetNames, ['ADR MADE BUT NOT BILLED', 'NET POD BUT NOT BILLED']);
  // the ADR tool's own export names are understood as well
  const exported = roundTrip(B.adr.buildWorkbook(r, adrTable(FIX), {}));
  const raw = B.sheets.prepareRaw(exported, { adr: { bcodeCol: 0 }, pod: { bcodeCol: 0 } });
  assert.equal(raw.adrSheet, 'ADR Report');
  assert.equal(raw.podSheet, 'Remaining LR Report');
});

/* ------------------------------------------------------------------ sheets / detection */
const RAW_H = ['S N.', 'B.CODE', 'BRANCH', 'BILLING PARTY NAME', 'ADR DATE', 'CN NO'];
test('raw sheets: aliases found, B.CODE auto-detected, missing column reported, manual override wins', () => {
  const wb = wbFrom({ 'ADR Report': [RAW_H, [1, 101, 'DELHI', 'ACME', '01-MAY-26', 'c']], 'Remaining LR Report': [RAW_H, [1, 102, 'PUNE', 'ZETA', '', 'd']] });
  const raw = B.sheets.prepareRaw(wb);
  assert.equal(raw.ok, true);
  assert.equal(raw.adrMeta.bcodeCol, 1);
  assert.equal(raw.podMeta.partyCol, 3);
  const miss = B.sheets.prepareRaw(wbFrom({ 'ADR MADE BUT NOT BILLED': [RAW_H, [1, 101, 'D', 'A', '', 'c']] }));
  assert.equal(miss.ok, false);
  assert.match(miss.items.map(i => i.text).join(' '), /NET POD sheet not found/);
  // layout with no B.CODE-like header: the user picks the column
  const odd = wbFrom({ 'ADR MADE BUT NOT BILLED': [['X', 'Y'], ['101', 'a']], 'NET POD BUT NOT BILLED': [['X', 'Y'], ['101', 'a']] });
  assert.equal(B.sheets.prepareRaw(odd).ok, false);
  assert.equal(B.sheets.prepareRaw(odd, { adr: { bcodeCol: 0 }, pod: { bcodeCol: 0 } }).ok, true);
});

test('raw sheets: B.CODE beats a generic CODE column, and no billing-party header means "not found" (old tool returned column 0)', () => {
  const wb = wbFrom({ 'ADR MADE BUT NOT BILLED': [['CODE', 'B.CODE', 'NAME'], ['x', 101, 'n']], 'NET POD BUT NOT BILLED': [['S N.', 'B.CODE'], [1, 101]] });
  const raw = B.sheets.prepareRaw(wb);
  assert.equal(raw.adrMeta.bcodeCol, 1);                          // not the first "CODE" column
  assert.equal(raw.podMeta.partyCol, -1);
});

/* ------------------------------------------------------------------ summary parsing */
test('summary (nested, branch-wise): party under branches, banner rows are never parties, party with no codes warns', () => {
  const wb = wbFrom({ Summary: [
    ['SCM PUNE REGION PENDING BILLING REPORT (ADR MADE BUT NOT BILLED)'],
    ['S N.', 'B.CODE', 'BRANCH'],
    [1, 101, 'DELHI'], [2, 102, 'PUNE'], [],
    ['ACME LTD'], [null, 104, 'SURAT'], [null, 205, 'JAIPUR'], [],
    ['LONELY PARTY'], [],
    ['TOTAL']] });
  const s = B.sheets.parseSummary(wb, 'nested');
  assert.equal(s.ok, true);
  assert.deepEqual(s.branches.map(b => b.bcode), ['101', '102']);
  assert.deepEqual(s.parties.map(p => [p.name, p.bcodes.join(',')]), [['ACME LTD', '104,205']]);
  assert.ok(s.partyWarnings.some(w => /LONELY PARTY/.test(w)));
  assert.ok(!s.parties.some(p => /REPORT/.test(p.name)));          // banner row not mistaken for a party
});

test('summary (flat, region): parties and the branch list are separate blocks, month columns and block titles are read', () => {
  const hdr = ['S N.', 'B.CODE', 'BRANCH', 'April - 25', 'May - 25', 'TOTAL CN'];
  const wb = wbFrom({ Summary: [['SCM TEST REGION PENDING BILLING REPORT (ADR MADE BUT NOT BILLED)'], hdr, ['*', 'TATA MOTOR'], ['*', 'BOSCH'], [1, 101, 'DELHI'], [2, 102, 'PUNE'], ['', '', 'TOTAL']] });
  const s = B.sheets.parseSummary(wb, 'flat');
  assert.deepEqual(s.branches.map(b => b.bcode), ['101', '102']);
  assert.deepEqual(s.parties.map(p => p.name), ['TATA MOTOR', 'BOSCH']);
  assert.deepEqual(s.monthCols.map(m => m.key), ['APR-25', 'MAY-25']);
  assert.equal(s.totalLabel, 'TOTAL CN');
  assert.match(s.blockTitles.adr, /ADR MADE BUT NOT BILLED/);
});

test('summary: unusable file gives a clear error, not a crash', () => {
  const s = B.sheets.parseSummary(wbFrom({ S: [['hello', 'world'], ['a', 'b']] }), 'flat');
  assert.equal(s.ok, false);
  assert.match(s.error, /B\.CODE/);
});

/* ------------------------------------------------------------------ 2) branch-wise */
function rawWb(adrRows, podRows, extraH) {
  const h = extraH || RAW_H;
  return B.sheets.prepareRaw(wbFrom({ 'ADR MADE BUT NOT BILLED': [h, ...adrRows], 'NET POD BUT NOT BILLED': [h, ...podRows] }));
}
test('branch-wise: SINGLE branch mode works with no Summary at all (the old tool crashed on state.summary.partyWarnings)', () => {
  const raw = rawWb([[1, 101, 'DELHI', 'ACME', '', 'a'], [2, 102, 'PUNE', 'ZETA', '', 'b']], [[1, 101, 'DELHI', 'ACME', '', 'c']]);
  const r = B.branchwise.generate(raw, [{ type: 'branch', bcode: '101', name: '' }], { sources: ['adr', 'pod'], single: true });
  assert.deepEqual(r.wb.SheetNames, ['ADR_101', 'POD_101']);
  assert.equal(r.stats.records, 2);
  const multi = B.branchwise.generate(raw, ['101', '102'].map(c => ({ type: 'branch', bcode: c, name: '' })), { sources: ['adr'], single: true });
  assert.deepEqual(multi.wb.SheetNames, ['ADR_101', 'ADR_102']);
});

test('branch-wise: codes match across number/text/leading zeros; branch-name mismatch is reported, not silent', () => {
  const raw = rawWb([[1, 207, 'KOTA', 'A', '', 'a'], [2, '0207', 'KOTA', 'A', '', 'b'], [3, '207', 'WRONGNAME', 'A', '', 'c']], []);
  const r = B.branchwise.generate(raw, [{ type: 'branch', bcode: '207', name: 'KOTA' }], { sources: ['adr'] });
  assert.equal(r.stats.records, 2);
  assert.ok(r.warnings.some(w => w.kind === 'name' && /1 row/.test(w.message)));
});

test('branch-wise: party sheets use the party column and the party\'s own branch codes; overlaps and unlisted branches are flagged', () => {
  const raw = rawWb([[1, 104, 'SURAT', 'ACME LTD', '', 'a'], [2, 104, 'SURAT', 'ZETA', '', 'b'], [3, 205, 'JAIPUR', 'ACME LTD', '', 'c'], [4, 999, 'OTHER', 'ACME LTD', '', 'd']], []);
  const entries = [{ type: 'branch', bcode: '104', name: 'SURAT' }, { type: 'party', name: 'ACME LTD', bcodes: ['104', '205'] }];
  const r = B.branchwise.generate(raw, entries, { sources: ['adr'] });
  assert.deepEqual(r.wb.SheetNames, ['ADR_104_SURAT', 'ADR_ACME LTD']);
  assert.equal(grid(r.wb, 'ADR_ACME LTD').length, 1 + 2);          // 104 + 205 rows of ACME, not 999, not ZETA
  assert.ok(r.warnings.some(w => w.kind === 'overlap'));
  assert.ok(r.warnings.some(w => w.kind === 'audit' && /999/.test(w.message)));
});

test('branch-wise: long/duplicate/illegal sheet names are made safe and unique', () => {
  const raw = rawWb([[1, 1, 'A'.repeat(40) + '/x', 'P', '', 'a'], [2, 2, 'A'.repeat(40) + '/x', 'P', '', 'b']], []);
  const r = B.branchwise.generate(raw, [{ type: 'branch', bcode: '1', name: 'A'.repeat(40) + '/x' }, { type: 'branch', bcode: '2', name: 'A'.repeat(40) + '/x' }], { sources: ['adr'] });
  assert.equal(new Set(r.wb.SheetNames.map(n => n.toUpperCase())).size, 2);
  r.wb.SheetNames.forEach(n => { assert.ok(n.length <= 31); assert.ok(!/[\/\\?*\[\]:]/.test(n)); });
});

test('branch-wise: 40,000 raw rows x 200 branches finish quickly (indexed, not O(rows x branches))', () => {
  const adrRows = [];
  for (let i = 0; i < 40000; i++) adrRows.push([i, 100 + (i % 200), 'B' + (i % 200), 'P' + (i % 7), '', 'c' + i]);
  const raw = rawWb(adrRows, []);
  const entries = []; for (let c = 100; c < 300; c++) entries.push({ type: 'branch', bcode: String(c), name: 'B' + (c - 100) });
  const t0 = Date.now();
  const r = B.branchwise.generate(raw, entries, { sources: ['adr'] });
  const ms = Date.now() - t0;
  assert.equal(r.stats.records, 40000);
  assert.ok(ms < 8000, 'took ' + ms + ' ms');
});

/* ------------------------------------------------------------------ 3) region */
const SUM_H = ['S N.', 'B.CODE', 'BRANCH', 'April - 25', 'May - 25', 'June - 25', 'TOTAL CN'];
function regionSetup(adrRows, podRows, summaryRows, tweak) {
  const raw = B.sheets.prepareRaw(wbFrom({
    'ADR MADE BUT NOT BILLED': [RAW_H, ...adrRows],
    'NET POD BUT NOT BILLED': [['S N.', 'B.CODE', 'BRANCH', 'BILLING PARTY NAME', 'DELIVERY DATE', 'CN NO'], ...podRows]
  }, tweak));
  const sum = B.sheets.parseSummary(wbFrom({ Summary: summaryRows }), 'flat');
  return { raw, sum };
}
const SUMMARY = [SUM_H, ['*', 'TATA MOTOR'], [1, 101, 'DELHI'], [2, 102, 'PUNE'], ['', '', 'TOTAL']];

test('region: branch sections, party sheet and month SUMMARY; party rows leave the branch sections; everything reconciles', () => {
  const { raw, sum } = regionSetup(
    [[1, 101, 'DELHI', 'ACME', '10-APR-25', 'a'], [2, 101, 'DELHI', 'TATA MOTORS LTD', '12-MAY-25', 'b'], [3, 102, 'PUNE', 'ACME', '01-JUN-25', 'c'],
     [4, 999, 'OUT', 'ACME', '10-APR-25', 'd'], [5, '', 'NOCODE', 'ACME', '10-APR-25', 'e']],
    [[1, 102, 'PUNE', 'ACME', '15-JUN-25', 'x']], SUMMARY);
  const r = B.region.generate(raw, sum, { sources: ['adr', 'pod'], partyMatch: 'prefix' });
  assert.deepEqual(r.wb.SheetNames, ['ADR', 'NET POD', 'PARTY WISE', 'SUMMARY']);
  const adr = grid(r.wb, 'ADR').map(x => x[0] + '|' + x[1]).filter(Boolean);
  assert.ok(adr.includes('BRANCH: 101 - DELHI|'));
  assert.equal(grid(r.wb, 'ADR').filter(x => x[3] === 'TATA MOTORS LTD').length, 0);      // moved to PARTY WISE
  assert.equal(grid(r.wb, 'PARTY WISE').filter(x => x[3] === 'TATA MOTORS LTD').length, 1);
  const rec = r.audit.reconciliation.adr;
  assert.deepEqual([rec.totalRows, rec.branchSheetRows, rec.partySheetRows, rec.outsideRegion, rec.blankCode, rec.ok], [4, 2, 1, 1, 1, true]);
  // SUMMARY pivot: ADR dates bucketed by month, party row first
  const s = grid(r.wb, 'SUMMARY');
  const adrBlock = s.slice(0, s.findIndex((x, i) => i > 3 && x[3] === 'TOTAL') + 1);
  assert.deepEqual(adrBlock.find(x => x[2] === 'TATA MOTOR').slice(4, 8), [0, 1, 0, 1]);   // May=1, total=1
  assert.deepEqual(adrBlock.find(x => x[2] === 101).slice(4, 8), [1, 0, 0, 1]);            // April=1
  assert.deepEqual(adrBlock.find(x => x[2] === 102).slice(4, 8), [0, 0, 1, 1]);            // June=1
  assert.deepEqual(adrBlock.find(x => x[3] === 'TOTAL').slice(4, 8), [1, 1, 1, 3]);
});

test('region: the source sheet\'s merged cells are NOT copied onto the re-laid-out sheets (the old tool merged unrelated cells)', () => {
  const { raw, sum } = regionSetup([[1, 101, 'DELHI', 'ACME', '10-APR-25', 'a'], [2, 101, 'DELHI', 'ACME', '10-APR-25', 'b']], [], SUMMARY,
    wb => { wb.Sheets['ADR MADE BUT NOT BILLED']['!merges'] = [XLSX.utils.decode_range('A2:D2')]; });
  assert.ok(raw.adrMeta.sheet['!merges'] && raw.adrMeta.sheet['!merges'].length);
  const r = B.region.generate(raw, sum, { sources: ['adr'] });
  assert.equal(r.wb.Sheets['ADR']['!merges'], undefined);
});

test('region: SUMMARY is built from the data when the Summary file has no month columns (old tool: never generated)', () => {
  const plain = [['S N.', 'B.CODE', 'BRANCH'], [1, 101, 'DELHI']];
  const { raw, sum } = regionSetup([[1, 101, 'DELHI', 'ACME', '10-FEB-26', 'a'], [2, 101, 'DELHI', 'ACME', '10-APR-26', 'b']], [], plain);
  assert.equal(sum.monthCols.length, 0);
  const r = B.region.generate(raw, sum, { sources: ['adr'], autoMonths: true });
  const s = grid(r.wb, 'SUMMARY');
  assert.deepEqual(s[1].slice(4, 8), ['February - 26', 'March - 26', 'April - 26', 'TOTAL CN']);
  assert.deepEqual(s.find(x => x[2] === 101).slice(4, 8), [1, 0, 1, 2]);
  const off = B.region.generate(raw, sum, { sources: ['adr'], autoMonths: false });
  assert.ok(!off.wb.SheetNames.includes('SUMMARY'));
  assert.ok(off.warnings.some(w => w.kind === 'summary'));
});

test('region: blank / older / newer dates are counted and reported; older-than-range can go to the FIRST month', () => {
  const rows = [[1, 101, 'DELHI', 'A', '', 'a'], [2, 101, 'DELHI', 'A', '10-JAN-25', 'b'], [3, 101, 'DELHI', 'A', '10-DEC-26', 'c'], [4, 101, 'DELHI', 'A', '10-MAY-25', 'd']];
  const { raw, sum } = regionSetup(rows, [], SUMMARY);
  const last = B.region.generate(raw, sum, { sources: ['adr'] });
  const first = B.region.generate(raw, sum, { sources: ['adr'], olderPolicy: 'first' });
  const row = r => grid(r.wb, 'SUMMARY').find(x => x[2] === 101).slice(4, 8);
  assert.deepEqual(row(last), [0, 1, 3, 4]);        // blank + older + newer all land in June (last)
  assert.deepEqual(row(first), [1, 1, 2, 4]);       // older goes to April (first)
  assert.deepEqual(last.audit.summary.counters, { blank: 1, older: 1, newer: 1, gap: 0 });
  assert.ok(last.warnings.some(w => /older than the first month/.test(w.message)));
});

test('region: real Excel date cells on the 1st of a month land in THAT month in every time zone', () => {
  const { raw, sum } = regionSetup([[1, 101, 'DELHI', 'A', 46143, 'a'], [2, 101, 'DELHI', 'A', 46113, 'b']], [], SUMMARY, wb => {
    const ws = wb.Sheets['ADR MADE BUT NOT BILLED'];
    ['E2', 'E3'].forEach(a => { ws[a].t = 'n'; ws[a].z = 'dd-mmm-yy'; });
  });                                                       // 46143 = 01-MAY-2026 ... month columns are 2025: use matching serials below
  const real = [[1, 101, 'DELHI', 'A', 45778, 'a'], [2, 101, 'DELHI', 'A', 45748, 'b']];   // 45778 = 01-MAY-2025, 45748 = 01-APR-2025
  const s2 = regionSetup(real, [], SUMMARY, wb => {
    const ws = wb.Sheets['ADR MADE BUT NOT BILLED'];
    ['E2', 'E3'].forEach(a => { ws[a].t = 'n'; ws[a].z = 'dd-mmm-yy'; });
  });
  const r = B.region.generate(s2.raw, s2.sum, { sources: ['adr'] });
  const row = grid(r.wb, 'SUMMARY').find(x => x[2] === 101).slice(4, 8);
  assert.deepEqual(row, [1, 1, 0, 2]);                      // April=1, May=1 (not shifted back a month)
  assert.equal(r.audit.summary.counters.older, 0);
});

test('region: overlapping party names are reported; "exact" matching removes the overlap', () => {
  const sumRows = [SUM_H, ['*', 'TATA'], ['*', 'TATA MOTORS'], [1, 101, 'DELHI'], ['', '', 'TOTAL']];
  const { raw, sum } = regionSetup([[1, 101, 'DELHI', 'TATA MOTORS LTD', '10-APR-25', 'a'], [2, 101, 'DELHI', 'TATA STEEL', '10-APR-25', 'b']], [], sumRows);
  const prefix = B.region.generate(raw, sum, { sources: ['adr'], partyMatch: 'prefix' });
  assert.ok(prefix.warnings.some(w => w.kind === 'overlap'));
  const exact = B.region.generate(raw, sum, { sources: ['adr'], partyMatch: 'exact' });
  assert.ok(!exact.warnings.some(w => w.kind === 'overlap'));
});

test('region: AUDIT sheet is optional and carries the reconciliation', () => {
  const { raw, sum } = regionSetup([[1, 101, 'DELHI', 'ACME', '10-APR-25', 'a']], [], SUMMARY);
  const r = B.region.generate(raw, sum, { sources: ['adr'], auditSheet: true });
  assert.ok(r.wb.SheetNames.includes('AUDIT'));
  assert.match(grid(r.wb, 'AUDIT').map(x => x.join('|')).join('\n'), /ADR\|1\|1\|0\|0\|0\|YES/);
});
