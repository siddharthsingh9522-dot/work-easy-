/*
 * OM Suite - ADR / NET Billing Suite: core logic (no DOM, runs in the browser and in Node for tests).
 *
 * Consolidates and hardens three standalone tools:
 *   1) ADR_NET_QUERY            -> OMB.adr.*        (LR export -> ADR Report / Remaining LR Report)
 *   2) BRANCH_WISE_DATA_CREATION-> OMB.branchwise.* (raw ADR/NET sheets -> one sheet per branch / party)
 *   3) REGION_CREATION          -> OMB.region.*     (raw ADR/NET sheets -> region workbook + month-wise SUMMARY)
 * Original tool: "ADR NET QUERY - Created by Harsh" (ADR_NET_QUERY.html). Audit fixes are listed in
 * docs/BILLING_SUITE_AUDIT.md.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('xlsx'));
  else root.OMBilling = factory(root.XLSX);
}(typeof self !== 'undefined' ? self : this, function (XLSX) {
  'use strict';

  /* ===================================================================== helpers */
  var MONTH_ABBR = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  var MONTH_FULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  var INVALID_SHEET_CHARS = /[\/\\\?\*\[\]:]/g;
  var TOTAL_PATTERN = /^(GRAND)?(SUB)?TOTAL$/;

  function str(v) { return v === null || v === undefined ? '' : String(v); }
  function isBlank(v) {
    return v === null || v === undefined || (typeof v === 'string' && v.trim() === '') || (typeof v === 'number' && isNaN(v));
  }
  function normHeader(v) { return str(v).toUpperCase().replace(/[^A-Z0-9]/g, ''); }
  function normalizeSheetName(s) { return str(s).toUpperCase().replace(/\s+/g, ' ').trim(); }
  function normalizeParty(v) { return str(v).toUpperCase().replace(/\s+/g, ' ').trim(); }
  function normalizeName(v) { return str(v).toUpperCase().replace(/[^A-Z0-9]+/g, ' ').replace(/\s+/g, ' ').trim(); }
  function normalizeCode(v) {
    if (v === null || v === undefined) return '';
    var s = String(v).trim().replace(/^["']+|["']+$/g, '').trim();
    if (s === '') return '';
    if (/^-?\d+(\.\d+)?$/.test(s)) { var n = Number(s); if (!isNaN(n)) return String(n); }
    return s.toUpperCase().replace(/\s+/g, '');
  }
  function branchNameMatches(summaryName, rawName) {
    var s = normalizeName(summaryName), r = normalizeName(rawName);
    if (!s || !r) return true;
    return s === r || r.indexOf(s) === 0;
  }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function sortedCodes(codes) {
    return codes.slice().sort(function (a, b) {
      var na = Number(a), nb = Number(b);
      if (!isNaN(na) && !isNaN(nb)) return na - nb;
      return String(a).localeCompare(String(b));
    });
  }
  function warn(kind, message) { return { kind: kind, message: message }; }

  /* ===================================================================== dates
   * Everything is handled as plain {y,m,d} (calendar) - never through JS Date objects, because SheetJS
   * `cellDates` returns timezone-shifted dates (e.g. 1-May in IST arrives as 30-Apr 23:59:50 -> wrong month).
   */
  function ymdValid(y, m, d) {
    if (!(m >= 1 && m <= 12 && d >= 1 && y >= 1900 && y <= 2200)) return false;
    return d <= new Date(y, m, 0).getDate();
  }
  function monthFromName(name) {
    var i = MONTH_ABBR.indexOf(str(name).slice(0, 3).toUpperCase());
    return i === -1 ? 0 : i + 1;
  }
  function fullYear(y) { return y < 100 ? 2000 + y : y; }
  function serialToParts(v) {
    if (typeof v !== 'number' || !isFinite(v) || v < 20000 || v > 80000) return null;
    var p = XLSX.SSF.parse_date_code(v);
    return p && ymdValid(p.y, p.m, p.d) ? { y: p.y, m: p.m, d: p.d } : null;
  }
  /** -> {y,m,d} or null (blank / not a recognisable date). */
  function parseDateParts(val) {
    if (val === null || val === undefined) return null;
    if (val instanceof Date) {
      if (isNaN(val.getTime())) return null;
      return { y: val.getFullYear(), m: val.getMonth() + 1, d: val.getDate() };
    }
    if (typeof val === 'number') return serialToParts(val);
    var s = String(val).trim();
    if (!s) return null;
    var m, p;
    // 29-APR-25 | 29 Apr 2025 | 29/Apr/25 | "26-APR-25 12.00.00.000000 AM"
    if ((m = s.match(/^(\d{1,2})[\s\-\/.]+([A-Za-z]{3,})[\s\-\/.,]+(\d{2,4})(?!\d)/))) {
      var mon = monthFromName(m[2]);
      p = { y: fullYear(Number(m[3])), m: mon, d: Number(m[1]) };
      return mon && ymdValid(p.y, p.m, p.d) ? p : null;
    }
    // 2026-05-01 | 2026/05/01 (+ optional time)
    if ((m = s.match(/^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})(?!\d)/))) {
      p = { y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) };
      return ymdValid(p.y, p.m, p.d) ? p : null;
    }
    // 01-05-2026 | 01/05/2026 | 01.05.2026 (day first - Indian convention)  and 01-05-26
    if ((m = s.match(/^(\d{1,2})[-\/.](\d{1,2})[-\/.](\d{4}|\d{2})(?!\d)/))) {
      p = { y: fullYear(Number(m[3])), m: Number(m[2]), d: Number(m[1]) };
      return ymdValid(p.y, p.m, p.d) ? p : null;
    }
    if (/^\d{5}(\.\d+)?$/.test(s)) return serialToParts(Number(s));      // serial stored as text
    return null;
  }
  function dateKey(p) { return p.y * 10000 + p.m * 100 + p.d; }
  function partsFromIso(iso) {                                            // 'YYYY-MM-DD' from <input type=date>
    var m = str(iso).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    return m ? { y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) } : null;
  }
  function fmtParts(p) { return pad2(p.d) + '-' + MONTH_ABBR[p.m - 1] + '-' + p.y; }
  function monthKeyOfParts(p) { return MONTH_ABBR[p.m - 1] + '-' + String(p.y).slice(-2); }
  function monthOrdinal(p) { return p.y * 12 + p.m - 1; }
  /** "April - 25" / "APR-25" / "Apr 2025" -> {key:'APR-25', ord} or null */
  function monthLabelToInfo(label) {
    var m = str(label).match(/([A-Za-z]{3,})\D*(\d{2,4})/);
    if (!m) return null;
    var mon = monthFromName(m[1]);
    if (!mon || MONTH_ABBR.indexOf(m[1].slice(0, 3).toUpperCase()) === -1) return null;
    var y = fullYear(m[2].length >= 4 ? Number(m[2]) : Number(m[2]));
    if (m[2].length === 1) y = 2000 + Number(m[2]);
    return { key: MONTH_ABBR[mon - 1] + '-' + String(y).slice(-2), ord: y * 12 + mon - 1, y: y, m: mon };
  }
  function monthLabel(y, m) { return MONTH_FULL[m - 1] + ' - ' + String(y).slice(-2); }

  /* ===================================================================== reading workbooks */
  function readWorkbook(data, filename) {
    var isCsv = /\.csv$/i.test(filename || '');
    // cellDates:false on purpose (see "dates" above); cellNF keeps number formats so date cells stay dates.
    return XLSX.read(data, { type: 'array', cellDates: false, cellNF: true, raw: isCsv });
  }
  function serialToText(v, z) {
    var p = XLSX.SSF.parse_date_code(v);
    return p && ymdValid(p.y, p.m, p.d) ? fmtParts(p) : v;
  }
  /** Sheet -> 2-D array (relative to the sheet range). dateAsText: Excel date cells become DD-MMM-YYYY text. */
  function sheetToAoa(ws, dateAsText) {
    var ref = ws['!ref'];
    if (!ref) return { aoa: [], range: null };
    var range = XLSX.utils.decode_range(ref);
    var rows = range.e.r - range.s.r + 1, cols = range.e.c - range.s.c + 1;
    var aoa = new Array(rows);
    for (var i = 0; i < rows; i++) { aoa[i] = new Array(cols); for (var j = 0; j < cols; j++) aoa[i][j] = ''; }
    var keys = Object.keys(ws);
    for (var k = 0; k < keys.length; k++) {
      var key = keys[k];
      if (key.charCodeAt(0) === 33) continue;                       // '!ref', '!merges', ...
      var cell = ws[key];
      var rc = XLSX.utils.decode_cell(key);
      var r = rc.r - range.s.r, c = rc.c - range.s.c;
      if (r < 0 || c < 0 || r >= rows || c >= cols || !cell) continue;
      var v = cell.v;
      if (cell.t === 'n') {
        if (dateAsText && cell.z && XLSX.SSF.is_date(cell.z)) v = serialToText(v, cell.z);
      } else if (cell.t === 'b') v = v ? 'TRUE' : 'FALSE';
      else if (cell.t === 'e' || cell.t === 'z' || v === undefined || v === null) v = '';
      else if (cell.t === 'd' && v instanceof Date) v = fmtParts({ y: v.getFullYear(), m: v.getMonth() + 1, d: v.getDate() });
      aoa[r][c] = v;
    }
    return { aoa: aoa, range: range };
  }
  function uniqueHeaders(row) {
    var seen = {}, out = [];
    for (var i = 0; i < row.length; i++) {
      var h = str(row[i]).trim() || ('COL_' + (i + 1));
      if (seen[h]) { seen[h]++; h = h + '_' + seen[h]; } else seen[h] = 1;
      out.push(h);
    }
    return out;
  }
  /** First usable sheet as {headers, rows} for the ADR/NET query (header = first non-empty row). */
  function sheetToTable(ws) {
    var a = sheetToAoa(ws, true).aoa, h = 0;
    while (h < a.length && a[h].every(function (c) { return isBlank(c); })) h++;
    if (h >= a.length) return { headers: [], rows: [] };
    var headers = uniqueHeaders(a[h]);
    var rows = [];
    for (var r = h + 1; r < a.length; r++) {
      if (a[r].every(function (c) { return isBlank(c); })) continue;       // fully blank spreadsheet rows
      rows.push(a[r]);
    }
    return { headers: headers, rows: rows };
  }
  function autoSizeCols(aoa) {
    var n = 0; aoa.forEach(function (r) { if (r.length > n) n = r.length; });
    var cols = [];
    for (var c = 0; c < n; c++) {
      var w = 8;
      for (var r = 0; r < Math.min(aoa.length, 200); r++) { var l = str(aoa[r][c]).length; if (l > w) w = l; }
      cols.push({ wch: Math.min(w + 2, 42) });
    }
    return cols;
  }
  function aoaSheet(aoa) { var ws = XLSX.utils.aoa_to_sheet(aoa); ws['!cols'] = autoSizeCols(aoa); return ws; }
  function safeSheetName(name, used) {
    var base = str(name).replace(INVALID_SHEET_CHARS, '_').replace(/\s+/g, ' ').trim() || 'Sheet';
    if (base.length > 31) base = base.substring(0, 31);
    var out = base, i = 1;
    while (used[out.toUpperCase()]) { var suf = '_' + i++; out = base.substring(0, 31 - suf.length) + suf; }
    used[out.toUpperCase()] = true;
    return out;
  }

  /* ===================================================================== 1) ADR / NET query */
  var ADR_ROLES = {
    freight: { label: 'Freight paid mode', aliases: ['CNFREIGHTPAIDMODE', 'FREIGHTPAIDMODE', 'FREIGHTMODE', 'PAIDMODE'], stage: 'Freight filter' },
    adr: { label: 'Combined ADR column (no, date, branch)', aliases: ['ADRNODATEBRANCH', 'ADRNODATEBRNCH', 'ADRDETAILS'], stage: 'ADR split' },
    podStatus: { label: 'POD upload status', aliases: ['CNPODUPLOADSTS', 'PODUPLOADSTS', 'CNPODUPLOADSTATUS', 'PODUPLOADSTATUS'], stage: 'POD status filter' },
    goDelivery: { label: 'Delivery date', aliases: ['CNDELIVERYDATE', 'DELIVERYDATE', 'GODELIVERYDATE'], stage: 'Go-delivery cutoff' },
    podRec: { label: 'POD received date', aliases: ['CNPODRECDATE', 'PODRECDATE', 'CNPODRECEIVEDDATE', 'PODRECEIVEDDATE'], stage: 'POD-received cutoff' },
    cn: { label: 'CN number', aliases: ['CNNO', 'CNNUMBER', 'LRNO', 'CNCNNO'], stage: 'Duplicate check' }
  };
  var ADR_REQUIRED = ['freight', 'adr', 'podStatus'];
  var ADR_OUTPUT_COLUMNS = ['ADR NO', 'ADR DATE', 'ADR BRANCH'];
  var DEFAULT_EXCLUDED_MODES = ['TO PAY', 'PREPAID'];
  var DEFAULT_HIDDEN_COLUMNS = ['PERIOD', 'CN_CURRENT_BRANCH_CODE', 'CN_SOURCE_BRANCH_CODE', 'CN_DESTINATION_BRANCH_CODE',
    'CN_CONSIGNOR_CODE', 'CN_CONSIGNEE_CODE'];

  /** Header text differences (case, spaces, underscores, trailing blanks) never matter. -> {role: index | -1} */
  function adrDetectColumns(headers) {
    var norm = headers.map(normHeader), map = {};
    Object.keys(ADR_ROLES).forEach(function (role) {
      var idx = -1;
      for (var a = 0; a < ADR_ROLES[role].aliases.length && idx === -1; a++) idx = norm.indexOf(ADR_ROLES[role].aliases[a]);
      map[role] = idx;
    });
    return map;
  }
  function normMode(v) { return str(v).trim().toUpperCase().replace(/\s+/g, ' '); }
  function splitAdr(val, delimiter) {
    var parts = str(val).split(delimiter || ',').map(function (p) { return p.trim(); });
    return [parts[0] || '', parts[1] || '', parts[2] || ''];
  }
  /** Distinct freight modes with counts - shown so a mode such as "PAID" that is not excluded is never a surprise. */
  function adrModeCounts(table, freightIdx) {
    var counts = {};
    if (freightIdx < 0) return counts;
    table.rows.forEach(function (r) { var m = normMode(r[freightIdx]) || '(blank)'; counts[m] = (counts[m] || 0) + 1; });
    return counts;
  }
  /**
   * opts: {map:{role:index|-1(skip stage)|null(unset)}, excludeModes:[..], adrCutoff, goCutoff, podCutoff ('YYYY-MM-DD'|''),
   *        delimiter}.  Returns {error} when a required column is neither mapped nor explicitly skipped.
   */
  function adrRun(table, opts) {
    var map = opts.map || {}, warnings = [], skipped = [];
    var unset = ADR_REQUIRED.filter(function (r) { return map[r] === undefined || map[r] === null; });
    if (unset.length) {
      return { error: 'Column(s) not mapped: ' + unset.map(function (r) { return ADR_ROLES[r].label; }).join(', '), missing: unset };
    }
    ADR_REQUIRED.forEach(function (r) { if (map[r] === -1) skipped.push(ADR_ROLES[r].stage); });
    var excluded = {};
    (opts.excludeModes || []).forEach(function (m) { excluded[normMode(m)] = true; });
    var cutoffs = { adr: partsFromIso(opts.adrCutoff), go: partsFromIso(opts.goCutoff), pod: partsFromIso(opts.podCutoff) };
    ['goDelivery', 'podRec'].forEach(function (r) {
      var need = r === 'goDelivery' ? cutoffs.go : cutoffs.pod;
      if (need && (map[r] === undefined || map[r] === null || map[r] < 0)) {
        skipped.push(ADR_ROLES[r].stage);
        warnings.push(warn('column', ADR_ROLES[r].label + ' column not found - that cutoff was NOT applied.'));
      }
    });

    var headers = table.headers.concat(ADR_OUTPUT_COLUMNS);
    var nCols = table.headers.length;
    var removed = [], bad = { adr: 0, go: 0, pod: 0 };
    var stage = { freight: 0, adrDate: 0, podStatus: 0, goDelivery: 0, podRec: 0 };
    function drop(row, st, reason) { removed.push({ row: row, stage: st, reason: reason }); }

    // 1) freight filter
    var afterFreight = [];
    table.rows.forEach(function (row) {
      if (map.freight >= 0 && excluded[normMode(row[map.freight])]) {
        drop(row.concat(['', '', '']), 'Freight Filter', 'Freight mode "' + str(row[map.freight]).trim() + '" is excluded (Paid/To-Pay)');
        stage.freight++;
      } else afterFreight.push(row);
    });

    // 2) split combined ADR column; ADR-confirmed = ADR NO or ADR BRANCH present
    var adrConfirmed = [], candidates = [];
    afterFreight.forEach(function (row) {
      var parts = map.adr >= 0 ? splitAdr(row[map.adr], opts.delimiter) : ['', '', ''];
      var out = row.slice(0, nCols).concat(parts);
      if (!isBlank(parts[0]) || !isBlank(parts[2])) adrConfirmed.push(out); else candidates.push(out);
    });

    // 3) ADR date cutoff - blank / unparseable dates are kept (never auto-deleted) but counted
    var adrRows = adrConfirmed;
    if (cutoffs.adr) {
      adrRows = [];
      adrConfirmed.forEach(function (row) {
        var p = parseDateParts(row[nCols + 1]);
        if (!p) { if (!isBlank(row[nCols + 1])) bad.adr++; adrRows.push(row); }
        else if (dateKey(p) <= dateKey(cutoffs.adr)) adrRows.push(row);
        else { drop(row, 'ADR Date Filter', 'ADR DATE ' + fmtParts(p) + ' is after cutoff ' + fmtParts(cutoffs.adr)); stage.adrDate++; }
      });
    }

    // 4) remaining LR: drop rows with empty POD upload status
    var podOk = [];
    candidates.forEach(function (row) {
      if (map.podStatus >= 0 && isBlank(row[map.podStatus])) {
        drop(row, 'POD Status', ADR_ROLES.podStatus.label + ' is empty (no ADR made, POD not uploaded)'); stage.podStatus++;
      } else podOk.push(row);
    });

    // 5) go-delivery cutoff, 6) POD-received cutoff (blanks kept)
    function dateCut(rows, col, cut, name, stageName, key, label) {
      if (!cut || col === undefined || col === null || col < 0) return rows;
      var keep = [];
      rows.forEach(function (row) {
        var p = parseDateParts(row[col]);
        if (!p) { if (!isBlank(row[col])) bad[key]++; keep.push(row); }
        else if (dateKey(p) <= dateKey(cut)) keep.push(row);
        else { drop(row, stageName, label + ' ' + fmtParts(p) + ' is after cutoff ' + fmtParts(cut)); stage[name]++; }
      });
      return keep;
    }
    var goKept = dateCut(podOk, map.goDelivery, cutoffs.go, 'goDelivery', 'Go Delivery Date', 'go', 'Delivery date');
    var remaining = dateCut(goKept, map.podRec, cutoffs.pod, 'podRec', 'POD Received Date', 'pod', 'POD received date');

    // audit
    if (bad.adr) warnings.push(warn('date', bad.adr + ' ADR row(s) have a date that could not be read - kept (never auto-deleted).'));
    if (bad.go) warnings.push(warn('date', bad.go + ' row(s) have an unreadable delivery date - kept.'));
    if (bad.pod) warnings.push(warn('date', bad.pod + ' row(s) have an unreadable POD-received date - kept.'));
    if (map.cn >= 0 && map.cn !== undefined) {
      var seen = {}, dup = 0;
      table.rows.forEach(function (r) { var k = normalizeCode(r[map.cn]); if (!k) return; if (seen[k]) dup++; else seen[k] = 1; });
      if (dup) warnings.push(warn('duplicate', dup + ' duplicate CN number(s) in the raw data (all copies were processed).'));
    }
    skipped.forEach(function (s) { warnings.push(warn('skipped', 'Stage skipped: ' + s)); });

    var totalRemoved = removed.length;
    var stats = {
      raw: table.rows.length, freightExcluded: stage.freight, adrConfirmedBeforeDate: adrConfirmed.length, adrDateRemoved: stage.adrDate,
      adr: adrRows.length, remainingCandidates: candidates.length, podStatusRemoved: stage.podStatus,
      goDeliveryRemoved: stage.goDelivery, podRecRemoved: stage.podRec, remaining: remaining.length, deleted: totalRemoved
    };
    stats.reconciled = stats.raw === stats.adr + stats.remaining + stats.deleted;
    if (!stats.reconciled) warnings.push(warn('audit', 'Row count does not reconcile (raw ' + stats.raw + ' vs ' + (stats.adr + stats.remaining + stats.deleted) + ').'));
    return { headers: headers, adr: adrRows, remaining: remaining, removed: removed, stats: stats, warnings: warnings, skipped: skipped,
      modeCounts: adrModeCounts(table, map.freight), map: map, excludeModes: Object.keys(excluded), cutoffs: opts };
  }
  function removalColumns(headers) { return headers.concat(['Removal Stage', 'Removal Reason']); }

  /** Workbook for download. include: {removed, summary, raw}; hide: header names hidden in the two report sheets. */
  function adrBuildWorkbook(result, table, info) {
    info = info || {};
    var include = info.include || { removed: true, summary: true, raw: false };
    var hide = {};
    (info.hide || []).forEach(function (h) { hide[normHeader(h)] = true; });
    var keepIdx = [];
    result.headers.forEach(function (h, i) { if (!hide[normHeader(h)]) keepIdx.push(i); });
    function pick(rows) { return rows.map(function (r) { return keepIdx.map(function (i) { return r[i] === undefined ? '' : r[i]; }); }); }
    var wb = XLSX.utils.book_new();
    var used = {};
    XLSX.utils.book_append_sheet(wb, aoaSheet([keepIdx.map(function (i) { return result.headers[i]; })].concat(pick(result.adr))), safeSheetName('ADR Report', used));
    XLSX.utils.book_append_sheet(wb, aoaSheet([keepIdx.map(function (i) { return result.headers[i]; })].concat(pick(result.remaining))), safeSheetName('Remaining LR Report', used));
    if (include.removed) {
      var rem = [removalColumns(result.headers)].concat(result.removed.map(function (x) { return x.row.concat([x.stage, x.reason]); }));
      XLSX.utils.book_append_sheet(wb, aoaSheet(rem), safeSheetName('Removed Records', used));
    }
    if (include.summary) {
      var s = result.stats, rows = [['ADR / NET QUERY - SUMMARY', ''], ['Source file', info.fileName || ''], ['Generated', info.generated || ''], ['', ''],
        ['Raw rows', s.raw], ['Removed - freight filter', s.freightExcluded], ['ADR confirmed (before date filter)', s.adrConfirmedBeforeDate],
        ['Removed - ADR date cutoff', s.adrDateRemoved], ['ADR Report rows', s.adr], ['Remaining candidates', s.remainingCandidates],
        ['Removed - POD status empty', s.podStatusRemoved], ['Removed - delivery-date cutoff', s.goDeliveryRemoved],
        ['Removed - POD-received cutoff', s.podRecRemoved], ['Remaining LR Report rows', s.remaining], ['Total removed', s.deleted],
        ['Reconciled (raw = ADR + remaining + removed)', s.reconciled ? 'YES' : 'NO'], ['', ''], ['Excluded freight modes', result.excludeModes.join(', ')],
        ['ADR date cutoff', (result.cutoffs && result.cutoffs.adrCutoff) || '-'], ['Delivery date cutoff', (result.cutoffs && result.cutoffs.goCutoff) || '-'],
        ['POD-received cutoff', (result.cutoffs && result.cutoffs.podCutoff) || '-'], ['', '']];
      Object.keys(ADR_ROLES).forEach(function (r) {
        var idx = result.map[r];
        rows.push(['Column - ' + ADR_ROLES[r].label, idx >= 0 ? table.headers[idx] : (idx === -1 ? '(skipped)' : '(not found)')]);
      });
      rows.push(['', '']);
      result.warnings.forEach(function (w) { rows.push(['Warning (' + w.kind + ')', w.message]); });
      XLSX.utils.book_append_sheet(wb, aoaSheet(rows), safeSheetName('Summary', used));
    }
    if (include.raw) {
      XLSX.utils.book_append_sheet(wb, aoaSheet([table.headers].concat(table.rows)), safeSheetName('Raw Data', used));
    }
    return wb;
  }
  /** Hand the two result sets to the next tools as an in-memory workbook (no download / re-upload / sheet renaming). */
  function adrToHandoffWorkbook(result) {
    var wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, aoaSheet([result.headers].concat(result.adr)), 'ADR MADE BUT NOT BILLED');
    XLSX.utils.book_append_sheet(wb, aoaSheet([result.headers].concat(result.remaining)), 'NET POD BUT NOT BILLED');
    return wb;
  }

  /* ===================================================================== sheet meta (branch-wise + region) */
  var SHEET_ALIASES = {
    adr: ['ADR MADE BUT NOT BILLED', 'ADR REPORT', 'ADR BUT NOT BILLED', 'ADR'],
    pod: ['NET POD BUT NOT BILLED', 'REMAINING LR REPORT', 'REMAINING LR', 'POD ON NET BUT NOT BILLED', 'NET POD', 'NET']
  };
  var SOURCE_LABEL = { adr: 'ADR', pod: 'NET POD' };
  var BCODE_PRIORITY = ['BCODE', 'BRANCHCODE', 'BRCODE', 'BCD', 'BRNCODE', 'BRCD', 'BRNCH', 'CODE'];
  var BILLING_PARTY_KEYWORDS = ['BILLINGPARTYNAME', 'BLLINGPARTYNAME', 'BILLINGPARTY', 'BLLINGPARTY', 'BILLINGCUSTOMERNAME',
    'BLLINGCUSTOMERNAME', 'BILLINGCUSTOMER', 'BLLINGCUSTOMER'];
  var PARTY_PRIORITY = ['PARTYNAME', 'CUSTOMERNAME', 'PARTY', 'CUSTOMER', 'PARTYCUSTOMER', 'ACCOUNTNAME', 'ACCOUNT', 'CUSTOMERPARTY'];
  var NAME_KEYWORDS = ['BRANCH', 'BRANCHNAME', 'PARTICULARS', 'DESCRIPTION', 'NAME', 'BRANCHDESC', 'BRANCHDESCRIPTION'];
  var BRANCH_NAME_KEYWORDS = ['BRANCHNAME', 'BRANCH', 'BRANCHDESC', 'BRANCHDESCRIPTION', 'BRNAME', 'BRNCHNAME'];
  var ADR_DATE_KEYWORDS = ['ADRDATE'];
  var DELIVERY_DATE_KEYWORDS = ['DELIVERYDATE', 'CNDELIVERYDATE', 'DELIVEREDDATE'];

  function findSheetName(wb, kind) {
    var names = wb.SheetNames, want = SHEET_ALIASES[kind];
    for (var a = 0; a < want.length; a++) {
      for (var i = 0; i < names.length; i++) if (normalizeSheetName(names[i]) === want[a]) return names[i];
    }
    return null;
  }
  function findPrioritized(row, priority) {
    var norm = row.map(normHeader);
    for (var p = 0; p < priority.length; p++) { var i = norm.indexOf(priority[p]); if (i !== -1) return i; }
    return -1;
  }
  function findHeaderRow(aoa, groups, maxScan) {
    var limit = Math.min(aoa.length, maxScan || 30);
    for (var r = 0; r < limit; r++) {
      var row = aoa[r] || [], cells = row.map(normHeader), all = true;
      for (var g = 0; g < groups.length && all; g++) all = cells.some(function (c) { return c && groups[g].indexOf(c) !== -1; });
      var nonEmpty = row.filter(function (c) { return str(c).trim() !== ''; }).length;
      if (all && nonEmpty >= 2) return r;
    }
    return -1;
  }
  /** Billing-party column (score based, also multi-row headers). Returns -1 when nothing matches (the original returned column 0). */
  function findBillingPartyColumn(aoa, headerRow) {
    var maxRow = Math.min(aoa.length, Math.max(40, (headerRow || 0) + 8)), best = -1, bestScore = 0;
    function score(v) {
      var n = normHeader(v);
      if (!n) return 0;
      if (BILLING_PARTY_KEYWORDS.indexOf(n) !== -1) return 100;
      if (n.indexOf('BILLINGPARTYNAME') !== -1 || n.indexOf('BLLINGPARTYNAME') !== -1) return 95;
      if (n.indexOf('BILLINGPARTY') !== -1 || n.indexOf('BLLINGPARTY') !== -1) return 90;
      if (n.indexOf('BILLINGCUSTOMERNAME') !== -1 || n.indexOf('BLLINGCUSTOMERNAME') !== -1) return 85;
      if (n.indexOf('BILLINGCUSTOMER') !== -1 || n.indexOf('BLLINGCUSTOMER') !== -1) return 80;
      return 0;
    }
    for (var r = 0; r < maxRow; r++) {
      var row = aoa[r] || [];
      for (var c = 0; c < row.length; c++) {
        var cands = [score(row[c])];
        if (r + 1 < maxRow) cands.push(score(normHeader(row[c]) + normHeader((aoa[r + 1] || [])[c])));
        if (r > 0) cands.push(score(normHeader((aoa[r - 1] || [])[c]) + normHeader(row[c])));
        var sc = Math.max.apply(null, cands);
        if (sc > bestScore) { bestScore = sc; best = c; }
      }
    }
    return best;
  }
  /** overrides: {headerRow, bcodeCol, partyCol, nameCol, dateCol} (indices) win over auto-detection. */
  function buildMeta(sheet, overrides) {
    overrides = overrides || {};
    var base = { sheet: sheet, range: null, aoa: [], headerRow: -1, bcodeCol: -1, partyCol: -1, nameCol: -1, dateCol: -1, headers: [], auto: {} };
    var ref = sheet['!ref'];
    if (!ref) return base;
    var parsed = sheetToAoa(sheet, false);
    var aoa = parsed.aoa, headerRow = overrides.headerRow;
    if (headerRow === undefined || headerRow === null) {
      headerRow = findHeaderRow(aoa, [BCODE_PRIORITY], 40);
      if (headerRow === -1) {                                  // no B.CODE-like header: first non-empty row, user maps the columns
        headerRow = 0;
        while (headerRow < aoa.length && aoa[headerRow].every(function (c) { return isBlank(c); })) headerRow++;
        if (headerRow >= aoa.length) headerRow = -1;
      }
    }
    var meta = { sheet: sheet, range: parsed.range, aoa: aoa, headerRow: headerRow, bcodeCol: -1, partyCol: -1, nameCol: -1, dateCol: -1, headers: [], auto: {} };
    if (headerRow === -1) return meta;
    var hdr = aoa[headerRow];
    meta.headers = hdr.map(function (h, i) { return str(h).trim() || ('COL ' + (i + 1)); });
    var autoB = findPrioritized(hdr, BCODE_PRIORITY);
    var autoP = findBillingPartyColumn(aoa, headerRow);
    if (autoP === -1) autoP = findPrioritized(hdr, PARTY_PRIORITY);
    meta.auto = { bcodeCol: autoB, partyCol: autoP, nameCol: findPrioritized(hdr, BRANCH_NAME_KEYWORDS) };
    meta.bcodeCol = overrides.bcodeCol !== undefined && overrides.bcodeCol !== null ? overrides.bcodeCol : autoB;
    meta.partyCol = overrides.partyCol !== undefined && overrides.partyCol !== null ? overrides.partyCol : autoP;
    meta.nameCol = overrides.nameCol !== undefined && overrides.nameCol !== null ? overrides.nameCol : meta.auto.nameCol;
    meta.dateCol = overrides.dateCol !== undefined && overrides.dateCol !== null ? overrides.dateCol : -1;
    return meta;
  }
  /** Raw workbook -> {adrMeta, podMeta, items[] (status lines for the UI)}. `overrides` = {adr:{..}, pod:{..}}. */
  function prepareRaw(wb, overrides) {
    overrides = overrides || {};
    var out = { adrMeta: null, podMeta: null, items: [], adrSheet: null, podSheet: null, ok: true };
    ['adr', 'pod'].forEach(function (kind) {
      var name = findSheetName(wb, kind);
      if (!name) { out.items.push({ ok: false, text: SOURCE_LABEL[kind] + ' sheet not found (looked for: ' + SHEET_ALIASES[kind].join(' / ') + ').' }); out.ok = false; return; }
      var meta = buildMeta(wb.Sheets[name], overrides[kind]);
      out[kind + 'Meta'] = meta; out[kind + 'Sheet'] = name;
      if (meta.bcodeCol === -1) {
        out.items.push({ ok: false, text: '"' + name + '": B.CODE column not identified - choose it below.' }); out.ok = false;
      } else out.items.push({ ok: true, text: '"' + name + '": B.CODE = "' + meta.headers[meta.bcodeCol] + '"' +
        (meta.partyCol >= 0 ? ', party = "' + meta.headers[meta.partyCol] + '"' : ', party column not found') });
    });
    return out;
  }

  /* ===================================================================== summary parsing */
  function findColIndex(row, keywords) {
    for (var c = 0; c < row.length; c++) if (keywords.indexOf(normHeader(row[c])) !== -1) return c;
    return -1;
  }
  function findMonthColumns(headerRow, bcodeCol, nameCol) {
    var startCol = Math.max(bcodeCol, nameCol) + 1, totalCol = -1;
    for (var c = startCol; c < headerRow.length; c++) if (normHeader(headerRow[c]).indexOf('TOTAL') !== -1) { totalCol = c; break; }
    var end = totalCol === -1 ? headerRow.length : totalCol, cols = [];
    for (var c2 = startCol; c2 < end; c2++) {
      var label = str(headerRow[c2]).trim();
      if (!label) continue;
      var info = monthLabelToInfo(label);
      if (info) cols.push({ colIndex: c2, label: label, key: info.key, ord: info.ord });
    }
    var tl = totalCol !== -1 ? str(headerRow[totalCol]).trim() : '';
    return { monthCols: cols, totalLabel: tl || 'TOTAL CN' };
  }
  function findBlockTitles(aoa) {
    var t = { adr: null, pod: null };
    for (var r = 0; r < aoa.length; r++) {
      var text = (aoa[r] || []).map(str).join(' ').replace(/\s+/g, ' ').trim();
      if (text.length < 20) continue;
      var u = text.toUpperCase();
      if (!t.adr && u.indexOf('ADR') !== -1 && u.indexOf('NOT BILLED') !== -1 && u.indexOf('POD') === -1) t.adr = text;
      if (!t.pod && u.indexOf('NOT BILLED') !== -1 && u.indexOf('POD') !== -1) t.pod = text;
    }
    return t;
  }
  var BANNER_WORDS = /REPORT|PENDING BILLING|NOT BILLED|REGION|SUMMARY|AS ON|AS OF/i;
  function looksLikePartyText(t) { return /[A-Za-z]{2,}/.test(t) && !/\d/.test(t); }
  /**
   * layout 'nested' (branch-wise): a party name may sit in ANY cell of an otherwise code-less row (original behaviour),
   * but title/banner rows ("SCM PUNE REGION PENDING BILLING REPORT ...") are never treated as parties.
   * layout 'flat' (region): only the NAME column is trusted.
   */
  function classifySummaryRow(row, bcodeCol, nameCol, layout) {
    if (!row.some(function (c) { return str(c).trim() !== ''; })) return { type: 'blank' };
    if (row.some(function (c) { return TOTAL_PATTERN.test(normHeader(c)); })) return { type: 'total' };
    var bRaw = bcodeCol >= 0 ? row[bcodeCol] : '', bText = str(bRaw).trim();
    if (BCODE_PRIORITY.indexOf(normHeader(bText)) !== -1) return { type: 'blank' };      // repeated header row
    var name = nameCol >= 0 ? str(row[nameCol]).trim() : '';
    if (bText !== '') {
      var bNorm = normalizeCode(bRaw);
      if (bNorm === '') return { type: 'blank' };
      // party "starting names" (TATA MOTOR, BOSCH...) can sit in the B.CODE column; real codes contain digits
      if (looksLikePartyText(bText)) return { type: 'party', name: name || bText };
      return { type: 'branch', bcode: bNorm, name: name };
    }
    if (!name && layout === 'nested') {
      var alt = row.find(function (c) { return /[A-Za-z]/.test(str(c)); });
      name = alt === undefined ? '' : str(alt).trim();
    }
    if (!name || name.indexOf('\n') !== -1 || name.length > 60 || BANNER_WORDS.test(name)) return { type: 'blank' };
    return { type: 'party', name: name };
  }
  var NOT_PARTY = { 'GR': 1, 'GRAND': 1, 'GRAND TOTAL': 1, 'TOTAL': 1, 'BRANCH NAME': 1 };
  /**
   * layout 'flat'   (region tool):   party rows and branch rows are two separate blocks; branches never belong to a party.
   * layout 'nested' (branch-wise):   branch rows directly under a party row belong to that party; a blank/total row ends it.
   */
  function parseSummary(wb, layout) {
    layout = layout || 'flat';
    var sheetName = wb.SheetNames[0];
    wb.SheetNames.forEach(function (n) { if (sheetName === wb.SheetNames[0] && normalizeSheetName(n).indexOf('SUMMARY') !== -1) sheetName = n; });
    var meta = buildMeta(wb.Sheets[sheetName]);
    var fail = function (msg) { return { ok: false, error: msg, branches: [], parties: [], partyWarnings: [], monthCols: [], totalLabel: 'TOTAL CN', blockTitles: { adr: null, pod: null } }; };
    if (meta.headerRow === -1 || meta.bcodeCol === -1) return fail('B.CODE column could not be identified in the Summary.');
    var hdr = meta.aoa[meta.headerRow], nameCol = findColIndex(hdr, NAME_KEYWORDS);
    var monthInfo = findMonthColumns(hdr, meta.bcodeCol, nameCol), titles = findBlockTitles(meta.aoa);
    var branchMap = {}, branchOrder = [], partyMap = {}, partyOrder = [], current = null;
    for (var r = meta.headerRow + 1; r < meta.aoa.length; r++) {
      var cls = classifySummaryRow(meta.aoa[r] || [], meta.bcodeCol, nameCol, layout);
      if (cls.type === 'blank' || cls.type === 'total') { if (layout === 'nested') current = null; continue; }
      if (cls.type === 'party') {
        var key = normalizeParty(cls.name);
        if (!key || NOT_PARTY[key]) { current = null; continue; }
        if (layout === 'nested') { current = { name: cls.name, bcodes: [], bcodeSet: {} }; partyOrder.push(current); }
        else if (!partyMap[key]) { partyMap[key] = { name: cls.name, bcodes: [], bcodeSet: {} }; partyOrder.push(partyMap[key]); }
        continue;
      }
      if (layout === 'nested' && current) {
        if (!current.bcodeSet[cls.bcode]) { current.bcodeSet[cls.bcode] = true; current.bcodes.push(cls.bcode); }
      } else if (!branchMap[cls.bcode]) { branchMap[cls.bcode] = { bcode: cls.bcode, name: cls.name }; branchOrder.push(cls.bcode); }
    }
    var branches = branchOrder.map(function (c) { return branchMap[c]; }), partyWarnings = [], parties = partyOrder;
    if (layout === 'nested') {
      parties = partyOrder.filter(function (p) {
        if (!p.bcodes.length) { partyWarnings.push(p.name + ' found in Summary, but no associated B.CODE was identified.'); return false; }
        return true;
      });
    } else {
      partyOrder.forEach(function (p) {
        if (!p.bcodes.length) partyWarnings.push(p.name + ': branch codes will be derived from Raw data (Billing Party Name match), restricted to the Summary\'s given branch list.');
      });
    }
    if (!branches.length && !parties.length) return fail('No valid branch or party rows found in the Summary.');
    return { ok: true, sheetName: sheetName, layout: layout, branches: branches, parties: parties, partyWarnings: partyWarnings,
      monthCols: monthInfo.monthCols, totalLabel: monthInfo.totalLabel, blockTitles: titles };
  }

  /* ===================================================================== indexing + copying */
  /** One pass over a sheet: bcode -> data-row list, plus normalized party per row. O(rows) instead of O(rows x branches). */
  function indexMeta(meta) {
    if (meta._index) return meta._index;
    var byCode = {}, partyNorm = new Array(meta.aoa.length), blank = 0, total = 0;
    for (var r = meta.headerRow + 1; r < meta.aoa.length; r++) {
      var row = meta.aoa[r] || [];
      if (row.every(function (c) { return isBlank(c); })) continue;
      var code = meta.bcodeCol >= 0 ? normalizeCode(row[meta.bcodeCol]) : '';
      if (!code) { blank++; continue; }
      total++;
      (byCode[code] || (byCode[code] = [])).push(r);
      if (meta.partyCol >= 0) partyNorm[r] = normalizeParty(row[meta.partyCol]);
    }
    meta._index = { byCode: byCode, partyNorm: partyNorm, blankCodeRows: blank, totalRows: total };
    return meta._index;
  }
  function cloneCell(cell) { var o = {}; for (var k in cell) if (Object.prototype.hasOwnProperty.call(cell, k)) o[k] = cell[k]; return o; }
  function copyRow(meta, relRow, dest, destRow) {
    var absR = meta.range.s.r + relRow, span = meta.range.e.c - meta.range.s.c;
    for (var c = 0; c <= span; c++) {
      var cell = meta.sheet[XLSX.utils.encode_cell({ r: absR, c: meta.range.s.c + c })];
      if (cell !== undefined) dest[XLSX.utils.encode_cell({ r: destRow, c: c })] = cloneCell(cell);
    }
  }
  /** Digit-only branch codes are written as real numbers (lookups / SUMIF against numeric B.CODE columns just work). */
  function codeCell(code) { return /^\d{1,15}$/.test(code) ? Number(code) : code; }
  function setCell(sheet, r, c, v) { sheet[XLSX.utils.encode_cell({ r: r, c: c })] = { v: v, t: typeof v === 'number' ? 'n' : 's' }; }
  function finalize(sheet, lastRow, lastCol, meta) {
    sheet['!ref'] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: Math.max(0, lastRow), c: Math.max(0, lastCol) } });
    // NOTE: the source sheet's merged ranges are deliberately NOT copied - the rows are re-laid-out, so the old
    // coordinates would merge unrelated cells (hiding data). Column widths are safe to keep.
    if (meta && meta.sheet['!cols']) sheet['!cols'] = meta.sheet['!cols'];
    return sheet;
  }

  /* ===================================================================== 2) branch-wise */
  /**
   * raw: prepareRaw() result. entries: [{type:'branch',bcode,name}|{type:'party',name,bcodes}].
   * opts: {sources:['adr','pod'], nameCheck:true}
   */
  function branchwiseGenerate(raw, entries, opts) {
    var sources = opts.sources || ['adr', 'pod'], wb = XLSX.utils.book_new(), used = {};
    var preview = [], warnings = [], totalRecords = 0, totalSheets = 0, zero = [];
    var overlap = {};
    entries.forEach(function (entry) {
      sources.forEach(function (source) {
        var meta = raw[source + 'Meta'], label = source === 'adr' ? 'ADR' : 'POD';
        if (!meta) return;
        if (entry.type === 'party' && meta.partyCol === -1) {
          warnings.push(warn('column', 'Party/customer column not identified in the ' + label + ' sheet - skipped ' + entry.name + '.')); return;
        }
        var ix = indexMeta(meta), match = [], nameMismatch = 0;
        if (entry.type === 'branch') {
          (ix.byCode[entry.bcode] || []).forEach(function (r) {
            var ok = !entry.name || opts.nameCheck === false || branchNameMatches(entry.name, meta.nameCol >= 0 ? meta.aoa[r][meta.nameCol] : '');
            if (ok) match.push(r); else nameMismatch++;
          });
        } else {
          var key = normalizeParty(entry.name);
          entry.bcodes.forEach(function (code) { (ix.byCode[code] || []).forEach(function (r) { if (ix.partyNorm[r] === key) match.push(r); }); });
          match.sort(function (a, b) { return a - b; });
        }
        match.forEach(function (r) { var k = source + ':' + r; overlap[k] = (overlap[k] || 0) + 1; });
        var display = entry.type === 'branch' ? (entry.name ? entry.bcode + '_' + entry.name : entry.bcode) : entry.name;
        var codes = entry.type === 'branch' ? entry.bcode : sortedCodes(entry.bcodes).join(', ');
        preview.push({ type: entry.type === 'branch' ? 'Branch' : 'Party', name: display, codes: codes, source: label, count: match.length });
        totalRecords += match.length;
        if (nameMismatch) warnings.push(warn('name', display + ' (' + label + '): ' + nameMismatch + ' row(s) have this B.CODE but a different branch name - not included.'));
        if (!match.length) { zero.push(display + ' (' + label + ')'); if (!opts.single) return; }
        var dest = {};
        copyRow(meta, meta.headerRow, dest, 0);
        match.forEach(function (r, i) { copyRow(meta, r, dest, i + 1); });
        finalize(dest, match.length, meta.range.e.c - meta.range.s.c, meta);
        var base = (entry.type === 'branch' ? (entry.name ? entry.bcode + '_' + entry.name : entry.bcode) : entry.name);
        XLSX.utils.book_append_sheet(wb, dest, safeSheetName(label + '_' + base, used));
        totalSheets++;
      });
    });
    if (zero.length) warnings.push(warn('zero', 'No matching records for ' + zero.length + ' branch/party sheet(s): ' + zero.join(', ')));
    var dup = 0; Object.keys(overlap).forEach(function (k) { if (overlap[k] > 1) dup++; });
    if (dup) warnings.push(warn('overlap', dup + ' raw row(s) appear in more than one output sheet (e.g. a branch that is also part of a party).'));
    // audit: raw branches that are not in the Summary at all
    var audit = { notInSummary: [] };
    if (!opts.single) {
      var inSummary = {};
      entries.forEach(function (e) { if (e.type === 'branch') inSummary[e.bcode] = 1; else e.bcodes.forEach(function (c) { inSummary[c] = 1; }); });
      sources.forEach(function (source) {
        var meta = raw[source + 'Meta']; if (!meta) return;
        var ix = indexMeta(meta), n = 0, codes = [];
        Object.keys(ix.byCode).forEach(function (c) { if (!inSummary[c]) { n += ix.byCode[c].length; codes.push(c); } });
        if (n) audit.notInSummary.push({ source: SOURCE_LABEL[source], rows: n, codes: sortedCodes(codes) });
      });
      audit.notInSummary.forEach(function (a) {
        warnings.push(warn('audit', a.source + ': ' + a.rows + ' row(s) belong to ' + a.codes.length + ' branch code(s) that are not in the Summary (not exported): ' + a.codes.slice(0, 15).join(', ') + (a.codes.length > 15 ? ' ...' : '')));
      });
    }
    return { wb: wb, preview: preview, warnings: warnings, stats: { sheets: totalSheets, records: totalRecords }, audit: audit };
  }

  /* ===================================================================== 3) region */
  function startsWithParty(rawName, summaryName) {
    var r = normalizeParty(rawName), k = normalizeParty(summaryName);
    if (!r || !k) return false;
    var rc = r.replace(/[^A-Z0-9]+/g, ''), kc = k.replace(/[^A-Z0-9]+/g, '');
    return r.indexOf(k) === 0 || (kc !== '' && rc.indexOf(kc) === 0);
  }
  function partyMatches(mode, rawName, summaryName) {
    return mode === 'exact' ? normalizeParty(rawName) === normalizeParty(summaryName) && normalizeParty(rawName) !== '' : startsWithParty(rawName, summaryName);
  }
  /** Rows of `meta` that belong to party `name` AND whose code is in `codes`. */
  function partyRows(meta, name, codes, mode) {
    var out = [];
    if (!meta || meta.partyCol === -1 || meta.bcodeCol === -1 || !codes.length) return out;
    var ix = indexMeta(meta);
    codes.forEach(function (code) {
      (ix.byCode[code] || []).forEach(function (r) { if (partyMatches(mode, meta.aoa[r][meta.partyCol], name)) out.push(r); });
    });
    return out.sort(function (a, b) { return a - b; });
  }
  function rawDateToMonthInfo(raw) {
    var p = parseDateParts(raw);
    return p ? { key: monthKeyOfParts(p), ord: monthOrdinal(p), y: p.y, m: p.m } : null;
  }
  /**
   * summary: parseSummary(...,'flat'); opts: {sources, partyMatch:'prefix'|'exact', autoMonths:true,
   *   olderPolicy:'last'|'first', auditSheet:false, dateCols:{adr,pod}}
   */
  function regionGenerate(raw, summary, opts) {
    opts = opts || {};
    var sources = opts.sources || ['adr', 'pod'], mode = opts.partyMatch || 'prefix', warnings = summary.partyWarnings.map(function (m) { return warn('party', m); });
    var wb = XLSX.utils.book_new(), preview = [], totalRecords = 0, totalSheets = 0;
    var parties = summary.parties.map(function (p) { return { name: p.name, bcodes: p.bcodes.slice(), bcodeSet: Object.assign({}, p.bcodeSet) }; });

    // --- the Summary's own given branch universe (a region branch listed under a party is still "given")
    var given = {};
    summary.branches.forEach(function (b) { var n = normalizeCode(b.bcode); if (n) given[n] = true; });
    parties.forEach(function (p) { p.bcodes.forEach(function (c) { var n = normalizeCode(c); if (n) given[n] = true; }); });

    // --- parties without codes: derive from raw (Billing Party Name match), restricted to the given list
    var metas = sources.map(function (s) { return raw[s + 'Meta']; }).filter(Boolean);
    parties.forEach(function (p) {
      if (p.bcodes.length) return;
      var key = normalizeParty(p.name);
      if (!key || NOT_PARTY[key]) return;
      var found = {}, anyName = false;
      metas.forEach(function (meta) {
        if (meta.partyCol === -1 || meta.bcodeCol === -1) return;
        var ix = indexMeta(meta);
        Object.keys(ix.byCode).forEach(function (code) {
          ix.byCode[code].forEach(function (r) {
            if (!partyMatches(mode, meta.aoa[r][meta.partyCol], p.name)) return;
            anyName = true;
            if (given[code]) found[code] = true;
          });
        });
      });
      Object.keys(found).forEach(function (code) { if (!p.bcodeSet[code]) { p.bcodeSet[code] = true; p.bcodes.push(code); } });
      if (!Object.keys(found).length) {
        warnings.push(warn('party', anyName ? 'Party "' + p.name + '" matched Billing Party Name in Raw data, but none of those branch codes are in the Summary\'s given branch list; no B.CODE assigned.'
          : 'Party "' + p.name + '" was not found in Raw data (Billing Party Name match); no B.CODE assigned.'));
      }
    });
    var branchCodes = [], seen = {};
    function addCode(c) { var n = normalizeCode(c); if (n && !seen[n]) { seen[n] = true; branchCodes.push(n); } }
    summary.branches.forEach(function (b) { addCode(b.bcode); });
    parties.forEach(function (p) { p.bcodes.forEach(addCode); });
    var nameOf = {}; summary.branches.forEach(function (b) { nameOf[normalizeCode(b.bcode)] = b.name; });

    // --- rows owned by a party are shown only in PARTY WISE (never duplicated in the branch sheets)
    function excludedFor(meta) {
      var ex = {}, perRow = {}, overlapRows = 0;
      if (!meta) return { rows: ex, overlap: 0 };
      parties.forEach(function (p) {
        var key = normalizeParty(p.name);
        if (!key || NOT_PARTY[key]) return;
        partyRows(meta, p.name, p.bcodes, mode).forEach(function (r) { ex[r] = true; perRow[r] = (perRow[r] || 0) + 1; });
      });
      Object.keys(perRow).forEach(function (r) { if (perRow[r] > 1) overlapRows++; });
      return { rows: ex, overlap: overlapRows };
    }
    var excl = { adr: excludedFor(raw.adrMeta), pod: excludedFor(raw.podMeta) };
    sources.forEach(function (s) {
      if (excl[s].overlap) warnings.push(warn('overlap', SOURCE_LABEL[s] + ': ' + excl[s].overlap + ' row(s) match more than one party (they appear in each matching party\'s section). Use "exact" party match to avoid this.'));
    });

    // --- 1) ADR / NET POD sheets: one sheet each, branch-wise sections
    var zero = [], recon = {};
    sources.forEach(function (source) {
      var meta = raw[source + 'Meta'], label = SOURCE_LABEL[source];
      if (!meta) return;
      var ix = indexMeta(meta), out = {}, outRow = 0, has = false, branchRows = 0;
      var lastCol = meta.range.e.c - meta.range.s.c;
      branchCodes.forEach(function (code) {
        var idx = (ix.byCode[code] || []).filter(function (r) { return !excl[source].rows[r]; });
        var disp = nameOf[code] ? code + ' - ' + nameOf[code] : code;
        preview.push({ type: 'Branch', name: disp, codes: code, source: label, count: idx.length });
        totalRecords += idx.length; branchRows += idx.length;
        if (!idx.length) { zero.push(disp + ' (' + label + ')'); return; }
        if (has) outRow += 2;
        setCell(out, outRow++, 0, 'BRANCH: ' + disp);
        copyRow(meta, meta.headerRow, out, outRow++);
        idx.forEach(function (r) { copyRow(meta, r, out, outRow++); });
        has = true;
      });
      if (!has) { warnings.push(warn('zero', 'No matching branch records were found for ' + label + '.')); copyRow(meta, meta.headerRow, out, 0); outRow = 1; }
      finalize(out, outRow - 1, lastCol, meta);
      XLSX.utils.book_append_sheet(wb, out, label);
      totalSheets++;
      var partyRowsN = Object.keys(excl[source].rows).length;
      var inRegion = 0, outside = 0;
      Object.keys(ix.byCode).forEach(function (c) { if (given[c]) inRegion += ix.byCode[c].length; else outside += ix.byCode[c].length; });
      recon[source] = { label: label, totalRows: ix.totalRows, branchSheetRows: branchRows, partySheetRows: partyRowsN, outsideRegion: outside,
        blankCode: ix.blankCodeRows, inRegion: inRegion, ok: ix.totalRows === branchRows + partyRowsN + outside };
      if (!recon[source].ok) warnings.push(warn('audit', label + ': rows do not reconcile (' + ix.totalRows + ' vs ' + (branchRows + partyRowsN + outside) + ').'));
      if (outside) warnings.push(warn('audit', label + ': ' + outside + ' row(s) belong to branch codes outside this region and were not exported.'));
      if (ix.blankCodeRows) warnings.push(warn('audit', label + ': ' + ix.blankCodeRows + ' row(s) have no B.CODE and were ignored.'));
    });
    if (zero.length) warnings.push(warn('zero', 'No matching records for ' + zero.length + ' branch sheet section(s): ' + zero.join(', ')));

    // --- 2) PARTY WISE: one combined sheet (party -> ADR -> 2 blank rows -> NET POD)
    var pw = {}, pwRow = 0, pwLast = 0, pwHas = false, seenParty = {};
    parties.forEach(function (p) {
      var key = normalizeParty(p.name);
      if (!key || NOT_PARTY[key] || seenParty[key]) return;
      seenParty[key] = true;
      if (!p.bcodes.length) {
        sources.forEach(function (s) { preview.push({ type: 'Party', name: p.name, codes: 'No Summary B.CODE', source: SOURCE_LABEL[s], count: 0 }); });
        warnings.push(warn('party', 'Party "' + p.name + '" has no Summary B.CODE/region rows; skipped from PARTY WISE.'));
        return;
      }
      var got = {};
      sources.forEach(function (s) {
        var meta = raw[s + 'Meta'], rows = [];
        if (meta && meta.partyCol !== -1 && meta.bcodeCol !== -1) rows = partyRows(meta, p.name, p.bcodes, mode);
        else warnings.push(warn('column', SOURCE_LABEL[s] + ' Billing Party Name / B.CODE column could not be identified.'));
        got[s] = rows; totalRecords += rows.length;
        preview.push({ type: 'Party', name: p.name, codes: sortedCodes(p.bcodes).join(', '), source: SOURCE_LABEL[s], count: rows.length });
      });
      if (!sources.some(function (s) { return got[s].length; })) { warnings.push(warn('party', 'No ADR/NET POD records found for "' + p.name + '".')); return; }
      if (pwHas) pwRow += 2;
      setCell(pw, pwRow++, 0, 'BILLING PARTY: ' + p.name);
      var wroteAdr = false;
      ['adr', 'pod'].forEach(function (s) {
        if (sources.indexOf(s) === -1 || !got[s].length) return;
        var meta = raw[s + 'Meta'];
        if (s === 'pod' && wroteAdr) pwRow += 2;
        setCell(pw, pwRow++, 0, SOURCE_LABEL[s]);
        copyRow(meta, meta.headerRow, pw, pwRow++);
        got[s].forEach(function (r) { copyRow(meta, r, pw, pwRow++); });
        pwLast = Math.max(pwLast, meta.range.e.c - meta.range.s.c);
        if (s === 'adr') wroteAdr = true;
      });
      pwHas = true;
    });
    if (pwHas) { finalize(pw, pwRow - 1, pwLast, null); XLSX.utils.book_append_sheet(wb, pw, 'PARTY WISE'); totalSheets++; }
    else if (!parties.length) warnings.push(warn('party', 'No party names were found in the Summary file.'));

    // --- 3) SUMMARY: month-wise pivot recomputed from the raw data
    var sum = regionSummarySheet(raw, summary, parties, excl, sources, mode, opts, warnings, branchCodes);
    if (sum) { XLSX.utils.book_append_sheet(wb, sum.sheet, 'SUMMARY'); totalSheets++; }

    var result = { wb: wb, preview: preview, warnings: warnings, stats: { sheets: totalSheets, records: totalRecords },
      audit: { reconciliation: recon, summary: sum ? sum.audit : null, parties: parties.map(function (p) { return { name: p.name, codes: p.bcodes.slice() }; }) } };
    if (opts.auditSheet) {
      var rows = [['AUDIT - ' + 'region workbook reconciliation'], [], ['Source', 'Raw rows (with B.CODE)', 'In branch sheets', 'In PARTY WISE', 'Outside this region', 'Rows without B.CODE', 'Reconciled']];
      Object.keys(recon).forEach(function (s) { var x = recon[s]; rows.push([x.label, x.totalRows, x.branchSheetRows, x.partySheetRows, x.outsideRegion, x.blankCode, x.ok ? 'YES' : 'NO']); });
      rows.push([]); warnings.forEach(function (w) { rows.push([w.kind, w.message]); });
      XLSX.utils.book_append_sheet(wb, aoaSheet(rows), 'AUDIT');
    }
    return result;
  }
  function regionSummarySheet(raw, summary, parties, excl, sources, mode, opts, warnings, branchCodes) {
    var dateCols = {}, metaFor = {};
    sources.forEach(function (s) {
      var meta = raw[s + 'Meta']; if (!meta) return;
      metaFor[s] = meta;
      var col = opts.dateCols && opts.dateCols[s] !== undefined && opts.dateCols[s] !== null ? opts.dateCols[s]
        : findColIndex(meta.aoa[meta.headerRow], s === 'adr' ? ADR_DATE_KEYWORDS : DELIVERY_DATE_KEYWORDS);
      dateCols[s] = col;
      if (col === -1) warnings.push(warn('column', 'SUMMARY: ' + (s === 'adr' ? '"ADR DATE"' : '"DELIVERY DATE"') + ' column not found in the ' + SOURCE_LABEL[s] + ' sheet; that month-wise block was skipped.'));
    });
    var active = sources.filter(function (s) { return metaFor[s] && dateCols[s] !== -1; });
    if (!active.length) return null;

    // month columns: from the Summary file, or built from the data when it has none
    var monthCols = summary.monthCols.slice(), totalLabel = summary.totalLabel || 'TOTAL CN', auto = false;
    if (!monthCols.length) {
      if (opts.autoMonths === false) { warnings.push(warn('summary', 'SUMMARY sheet not generated: the uploaded Summary has no month-wise columns (enable "build months from data").')); return null; }
      var lo = null, hi = null;
      active.forEach(function (s) {
        var ix = indexMeta(metaFor[s]);
        Object.keys(ix.byCode).forEach(function (code) {
          ix.byCode[code].forEach(function (r) {
            var info = rawDateToMonthInfo(metaFor[s].aoa[r][dateCols[s]]);
            if (!info) return;
            if (lo === null || info.ord < lo.ord) lo = info; if (hi === null || info.ord > hi.ord) hi = info;
          });
        });
      });
      if (!lo) { warnings.push(warn('summary', 'SUMMARY sheet not generated: no readable dates found in the raw data.')); return null; }
      for (var o = lo.ord; o <= hi.ord; o++) {
        var y = Math.floor(o / 12), m = (o % 12) + 1;
        monthCols.push({ label: monthLabel(y, m), key: MONTH_ABBR[m - 1] + '-' + String(y).slice(-2), ord: o });
      }
      auto = true;
      warnings.push(warn('summary', 'The uploaded Summary has no month columns - built ' + monthCols.length + ' month column(s) from the data (' + monthCols[0].label + ' to ' + monthCols[monthCols.length - 1].label + ').'));
    }
    var firstOrd = monthCols[0].ord, lastOrd = monthCols[monthCols.length - 1].ord, lastIdx = monthCols.length - 1;
    var counters = { blank: 0, older: 0, newer: 0, gap: 0 };
    function monthIndexFor(rawDate) {
      var info = rawDateToMonthInfo(rawDate);
      if (!info) { counters.blank++; return lastIdx; }                       // blank/unreadable -> last month (original rule)
      for (var i = 0; i < monthCols.length; i++) if (monthCols[i].key === info.key) return i;
      if (info.ord < firstOrd) { counters.older++; return opts.olderPolicy === 'first' ? 0 : lastIdx; }
      if (info.ord > lastOrd) { counters.newer++; return lastIdx; }
      counters.gap++; return lastIdx;
    }
    var nameOf = {}; summary.branches.forEach(function (b) { nameOf[normalizeCode(b.bcode)] = b.name; });
    var sheet = {}, row = 0, any = false, lastCol = 4 + monthCols.length;
    active.forEach(function (s) {
      var meta = metaFor[s], ix = indexMeta(meta), col = dateCols[s];
      var branchCounts = {}, partyCounts = {};
      branchCodes.forEach(function (code) {
        var arr = new Array(monthCols.length).fill(0);
        (ix.byCode[code] || []).forEach(function (r) { if (!excl[s].rows[r]) arr[monthIndexFor(meta.aoa[r][col])]++; });
        branchCounts[code] = arr;
      });
      parties.forEach(function (p) {
        var arr = new Array(monthCols.length).fill(0);
        if (p.bcodes.length) partyRows(meta, p.name, p.bcodes, mode).forEach(function (r) { arr[monthIndexFor(meta.aoa[r][col])]++; });
        partyCounts[normalizeParty(p.name)] = arr;
      });
      if (any) row += 2;
      var title = (s === 'adr' ? summary.blockTitles.adr : summary.blockTitles.pod) || (s === 'adr' ? 'PENDING BILLING REPORT (ADR MADE BUT NOT BILLED)' : 'PENDING BILLING REPORT (POD ON NET BUT NOT BILLED)');
      setCell(sheet, row++, 1, title);
      setCell(sheet, row, 1, 'S N.'); setCell(sheet, row, 2, 'B.CODE'); setCell(sheet, row, 3, 'BRANCH');
      monthCols.forEach(function (mc, i) { setCell(sheet, row, 4 + i, mc.label); });
      setCell(sheet, row++, 4 + monthCols.length, totalLabel);
      var totals = new Array(monthCols.length).fill(0), seenP = {};
      parties.forEach(function (p) {
        var key = normalizeParty(p.name);
        if (!key || NOT_PARTY[key] || seenP[key]) return; seenP[key] = true;
        var arr = partyCounts[key], t = 0;
        setCell(sheet, row, 1, '*'); setCell(sheet, row, 2, p.name);
        arr.forEach(function (v, i) { setCell(sheet, row, 4 + i, v); totals[i] += v; t += v; });
        setCell(sheet, row++, 4 + monthCols.length, t);
      });
      branchCodes.forEach(function (code, bi) {
        var arr = branchCounts[code], t = 0;
        setCell(sheet, row, 1, bi + 1); setCell(sheet, row, 2, codeCell(code)); setCell(sheet, row, 3, nameOf[code] || '');
        arr.forEach(function (v, i) { setCell(sheet, row, 4 + i, v); totals[i] += v; t += v; });
        setCell(sheet, row++, 4 + monthCols.length, t);
      });
      var grand = 0;
      setCell(sheet, row, 3, 'TOTAL');
      totals.forEach(function (v, i) { setCell(sheet, row, 4 + i, v); grand += v; });
      setCell(sheet, row++, 4 + monthCols.length, grand);
      any = true;
    });
    if (counters.blank) warnings.push(warn('summary', 'SUMMARY: ' + counters.blank + ' row(s) have a blank/unreadable date - counted in the last month column (original rule).'));
    if (counters.older) warnings.push(warn('summary', 'SUMMARY: ' + counters.older + ' row(s) are older than the first month column - counted in the ' + (opts.olderPolicy === 'first' ? 'FIRST' : 'LAST') + ' month column.'));
    if (counters.newer) warnings.push(warn('summary', 'SUMMARY: ' + counters.newer + ' row(s) are newer than the last month column - counted in the last month column.'));
    if (counters.gap) warnings.push(warn('summary', 'SUMMARY: ' + counters.gap + ' row(s) fall in a month missing between the Summary\'s columns - counted in the last month column.'));
    sheet['!ref'] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: Math.max(0, row - 1), c: lastCol } });
    var cols = [{ wch: 4 }, { wch: 7 }, { wch: 26 }, { wch: 26 }]; monthCols.forEach(function () { cols.push({ wch: 13 }); }); cols.push({ wch: 11 });
    sheet['!cols'] = cols;
    return { sheet: sheet, audit: { months: monthCols.map(function (m) { return m.label; }), counters: counters, autoMonths: auto } };
  }

  /* ===================================================================== exports */
  return {
    version: '1.0.0',
    util: { isBlank: isBlank, normHeader: normHeader, normalizeCode: normalizeCode, normalizeParty: normalizeParty, normalizeName: normalizeName,
      parseDateParts: parseDateParts, fmtParts: fmtParts, monthKeyOfParts: monthKeyOfParts, monthLabelToInfo: monthLabelToInfo, sortedCodes: sortedCodes,
      readWorkbook: readWorkbook, sheetToAoa: sheetToAoa, sheetToTable: sheetToTable, startsWithParty: startsWithParty, branchNameMatches: branchNameMatches,
      safeSheetName: safeSheetName },
    adr: { ROLES: ADR_ROLES, REQUIRED: ADR_REQUIRED, DEFAULT_EXCLUDED_MODES: DEFAULT_EXCLUDED_MODES, DEFAULT_HIDDEN_COLUMNS: DEFAULT_HIDDEN_COLUMNS,
      detectColumns: adrDetectColumns, modeCounts: adrModeCounts, run: adrRun, buildWorkbook: adrBuildWorkbook, toHandoffWorkbook: adrToHandoffWorkbook },
    sheets: { SHEET_ALIASES: SHEET_ALIASES, findSheetName: findSheetName, buildMeta: buildMeta, prepareRaw: prepareRaw, indexMeta: indexMeta, parseSummary: parseSummary },
    branchwise: { generate: branchwiseGenerate },
    region: { generate: regionGenerate }
  };
}));
