/*
 * ADR / NET Billing Suite - UI. All DOM is built with textContent (never innerHTML) so file contents can never inject markup.
 * Logic lives in core.js (OMBilling); files are processed in the browser and never uploaded.
 */
(function () {
  'use strict';
  var OMB = window.OMBilling, XLSX = window.XLSX;
  var root = document.getElementById('bsuite');
  if (!root) return;
  if (!OMB || !XLSX) { root.textContent = 'SheetJS / billing core could not be loaded - check your internet connection and refresh (Ctrl+F5).'; return; }

  /* ----------------------------------------------------------------- tiny DOM helpers */
  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else if (v === true) e.setAttribute(k, ''); else if (v !== false && v !== null && v !== undefined) e.setAttribute(k, v);
    });
    (kids || []).forEach(function (c) { if (c !== null && c !== undefined) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function fmtN(n) { return Number(n).toLocaleString('en-IN'); }
  function stamp() { var d = new Date(); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function later(fn) { setTimeout(fn, 30); }                         // lets the "working…" text paint before heavy work
  function readFile(file) {
    return new Promise(function (res, rej) {
      var r = new FileReader();
      r.onload = function (e) { res(new Uint8Array(e.target.result)); };
      r.onerror = function () { rej(new Error('Could not read the file.')); };
      r.readAsArrayBuffer(file);
    });
  }
  function store(key, val) { try { localStorage.setItem('omb.' + key, JSON.stringify(val)); } catch (e) { /* private mode */ } }
  function recall(key) { try { return JSON.parse(localStorage.getItem('omb.' + key)); } catch (e) { return null; } }

  function table(host, headers, rows, limit) {
    clear(host);
    if (!rows.length) { host.appendChild(h('p', { class: 'muted', text: 'No rows.' })); return; }
    var t = h('table', { class: 'tbl dbp' }), tr = h('tr');
    headers.forEach(function (x) { tr.appendChild(h('th', { text: String(x) })); });
    t.appendChild(h('thead', {}, [tr]));
    var b = h('tbody');
    rows.slice(0, limit || 50).forEach(function (r) {
      var row = h('tr');
      headers.forEach(function (_, i) { var v = r[i]; row.appendChild(h('td', { text: v === undefined || v === null ? '' : String(v) })); });
      b.appendChild(row);
    });
    t.appendChild(b); host.appendChild(t);
    if (rows.length > (limit || 50)) host.appendChild(h('p', { class: 'muted small', text: 'Showing first ' + (limit || 50) + ' of ' + fmtN(rows.length) + ' rows. The Excel file has everything.' }));
  }
  function dropzone(label, accept, onFile) {
    var input = h('input', { type: 'file', accept: accept, class: 'bs-file' });
    var box = h('label', { class: 'bs-drop' }, [h('span', { class: 'bs-drop-ico', text: '⬆' }), h('b', { text: label }), h('span', { class: 'muted small', text: 'click or drop the file here' }), input]);
    function pick(f) { if (f) onFile(f); }
    input.addEventListener('change', function () { pick(input.files[0]); input.value = ''; });
    ['dragenter', 'dragover'].forEach(function (ev) { box.addEventListener(ev, function (e) { e.preventDefault(); box.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (ev) { box.addEventListener(ev, function (e) { e.preventDefault(); box.classList.remove('over'); }); });
    box.addEventListener('drop', function (e) { pick(e.dataTransfer && e.dataTransfer.files[0]); });
    return box;
  }
  function note(kind, text) { return h('div', { class: 'bs-note ' + kind, text: text }); }
  function card(title, kids, cls) { return h('section', { class: 'card bs-card ' + (cls || '') }, [h('h3', { text: title })].concat(kids || [])); }
  function select(options, value, onChange) {
    var s = h('select', { class: 'input bs-sel' });
    options.forEach(function (o) { var op = h('option', { value: String(o.value), text: o.label }); if (String(o.value) === String(value)) op.selected = true; s.appendChild(op); });
    s.addEventListener('change', function () { onChange(s.value); });
    return s;
  }
  function stat(label, value, cls) { return h('div', { class: 'bs-stat ' + (cls || '') }, [h('div', { class: 'bs-stat-v', text: fmtN(value) }), h('div', { class: 'bs-stat-l', text: label })]); }

  /* ----------------------------------------------------------------- shared state */
  var S = { raw: null, query: {}, branch: { summary: null }, region: { summary: null }, listeners: [] };
  function changed() { S.listeners.forEach(function (f) { f(); }); }

  /* ----------------------------------------------------------------- warnings (grouped, collapsible) */
  var KIND_LABEL = { column: 'Missing columns', date: 'Dates', duplicate: 'Duplicates', skipped: 'Skipped stages', audit: 'Audit / reconciliation', zero: 'No matching records',
    name: 'Branch-name mismatches', overlap: 'Overlaps', party: 'Parties', summary: 'SUMMARY sheet' };
  function warningsBox(list) {
    var box = h('div', { class: 'bs-warns' });
    if (!list || !list.length) { box.appendChild(note('ok', 'No warnings.')); return box; }
    var groups = {};
    list.forEach(function (w) { (groups[w.kind] = groups[w.kind] || []).push(w.message); });
    Object.keys(groups).forEach(function (k) {
      var d = h('details', { class: 'bs-warn-group', open: groups[k].length <= 3 }, [h('summary', { text: (KIND_LABEL[k] || k) + ' (' + groups[k].length + ')' })]);
      groups[k].forEach(function (m) { d.appendChild(h('div', { class: 'bs-warn', text: m })); });
      box.appendChild(d);
    });
    return box;
  }
  function downloadBtn(label, getWb, fileName, status) {
    return h('button', { class: 'btn', type: 'button', onclick: function () {
      try { XLSX.writeFile(getWb(), fileName); } catch (e) { status.textContent = 'Could not create the Excel file: ' + e.message; }
    } }, [label]);
  }

  /* ================================================================= shared RAW card (tabs 2 & 3) */
  function loadRawWorkbook(wb, name, fromHandoff) {
    S.raw = { wb: wb, name: name, overrides: { adr: {}, pod: {} }, handoff: !!fromHandoff };
    reprepare();
    changed();
  }
  function reprepare() { S.raw.prepared = OMB.sheets.prepareRaw(S.raw.wb, S.raw.overrides); }
  function colOptions(meta, withNone) {
    var o = withNone ? [{ value: -1, label: '(none)' }] : [];
    meta.headers.forEach(function (x, i) { o.push({ value: i, label: x + '  [' + XLSX.utils.encode_col(meta.range.s.c + i) + ']' }); });
    return o;
  }
  function rawCard(tabKey) {
    var host = h('div');
    function render() {
      clear(host);
      var kids = [];
      if (!S.raw) {
        kids.push(dropzone('Raw workbook (ADR + NET POD sheets)', '.xlsx,.xlsm,.xls', function (f) {
          readFile(f).then(function (buf) { loadRawWorkbook(OMB.util.readWorkbook(buf, f.name), f.name, false); })
            .catch(function (e) { host.appendChild(note('bad', e.message)); });
        }));
        var cand = S.query && S.query.result;
        if (cand) kids.push(h('button', { class: 'btn ghost', type: 'button', onclick: function () { loadRawWorkbook(OMB.adr.toHandoffWorkbook(S.query.result), 'ADR / NET Query result', true); } }, ['↪ Use the ADR / NET Query result (' + fmtN(cand.stats.adr) + ' ADR + ' + fmtN(cand.stats.remaining) + ' remaining rows)']));
        kids.push(h('p', { class: 'muted small', text: 'Sheets are recognised by name: "ADR MADE BUT NOT BILLED" / "ADR Report" and "NET POD BUT NOT BILLED" / "Remaining LR Report".' }));
      } else {
        var p = S.raw.prepared;
        kids.push(h('div', { class: 'bs-chip' }, [h('b', { text: '📄 ' + S.raw.name }), h('button', { class: 'btn ghost sm', type: 'button', onclick: function () { S.raw = null; changed(); } }, ['✕ Remove'])]));
        p.items.forEach(function (it) { kids.push(note(it.ok ? 'ok' : 'bad', (it.ok ? '✔ ' : '✖ ') + it.text)); });
        ['adr', 'pod'].forEach(function (kind) {
          var meta = p[kind + 'Meta']; if (!meta || !meta.headers.length) return;
          var label = kind === 'adr' ? 'ADR' : 'NET POD';
          var det = h('details', { class: 'bs-adv', open: meta.bcodeCol === -1 }, [h('summary', { text: 'Columns - ' + label + ' sheet' })]);
          [['bcodeCol', 'B.CODE', false], ['partyCol', 'Billing party', true], ['nameCol', 'Branch name', true]].concat(tabKey === 'region' ? [['dateCol', kind === 'adr' ? 'ADR DATE (for SUMMARY)' : 'DELIVERY DATE (for SUMMARY)', true]] : []).forEach(function (f) {
            var cur = f[0] === 'dateCol' ? (S.raw.overrides[kind].dateCol !== undefined ? S.raw.overrides[kind].dateCol : -1) : meta[f[0]];
            det.appendChild(h('label', { class: 'bs-field' }, [h('span', { text: f[1] }), select(colOptions(meta, f[2]), cur, function (v) {
              S.raw.overrides[kind][f[0]] = Number(v); reprepare(); changed();
            })]));
          });
          kids.push(det);
        });
      }
      kids.forEach(function (k) { host.appendChild(k); });
    }
    S.listeners.push(render); render();
    return card('1. Raw data', [host]);
  }

  /* ================================================================= TAB 1 - ADR / NET QUERY */
  function queryPanel() {
    var Q = S.query, out = h('div'), mapHost = h('div'), modeHost = h('div'), status = h('div', { class: 'muted small' }), resHost = h('div');
    Q.excluded = (recall('excluded') || OMB.adr.DEFAULT_EXCLUDED_MODES).slice();

    function fingerprint(headers) { return headers.map(OMB.util.normHeader).join('|'); }
    function initMap(headers) {
      var saved = recall('map.' + fingerprint(headers));
      if (saved) return saved;
      var m = OMB.adr.detectColumns(headers);
      OMB.adr.REQUIRED.forEach(function (r) { if (m[r] === -1) m[r] = null; });   // a missing required column must be chosen on purpose
      return m;
    }
    function renderMap() {
      clear(mapHost);
      if (!Q.table) return;
      var roles = OMB.adr.ROLES, grid = h('div', { class: 'bs-maps' });
      Object.keys(roles).forEach(function (role) {
        var required = OMB.adr.REQUIRED.indexOf(role) !== -1, val = Q.map[role];
        var opts = [{ value: '', label: required ? '— choose —' : '— not available —' }, { value: -1, label: '(skip this stage)' }];
        Q.table.headers.forEach(function (x, i) { opts.push({ value: i, label: x }); });
        var bad = required && (val === null || val === undefined);
        grid.appendChild(h('label', { class: 'bs-field' + (bad ? ' bad' : '') }, [
          h('span', { text: roles[role].label + (required ? ' *' : '') + (val >= 0 ? '  ✔' : '') }),
          select(opts, val === null || val === undefined ? '' : val, function (v) { Q.map[role] = v === '' ? null : Number(v); store('map.' + fingerprint(Q.table.headers), Q.map); renderMap(); renderModes(); })]));
      });
      mapHost.appendChild(grid);
      var unset = OMB.adr.REQUIRED.filter(function (r) { return Q.map[r] === null || Q.map[r] === undefined; });
      if (unset.length) mapHost.appendChild(note('bad', 'Choose the column for: ' + unset.map(function (r) { return roles[r].label; }).join(', ') + ' (or "skip this stage" on purpose). Nothing is processed until this is settled - a missing column never silently deletes rows.'));
      else if (OMB.adr.REQUIRED.some(function (r) { return Q.map[r] === -1; })) mapHost.appendChild(note('warn', 'A stage is set to "skip" - it will be listed in the Summary sheet.'));
      else mapHost.appendChild(note('ok', '✔ All required columns found.'));
    }
    function renderModes() {
      clear(modeHost);
      if (!Q.table) return;
      var counts = Q.map.freight >= 0 ? OMB.adr.modeCounts(Q.table, Q.map.freight) : {};
      var keys = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
      Q.excluded.forEach(function (m) { if (keys.indexOf(m) === -1) keys.push(m); });
      var wrap = h('div', { class: 'bs-chips' });
      keys.forEach(function (m) {
        var on = Q.excluded.indexOf(m) !== -1;
        var cb = h('input', { type: 'checkbox' }); cb.checked = on;
        cb.addEventListener('change', function () { Q.excluded = cb.checked ? Q.excluded.concat([m]) : Q.excluded.filter(function (x) { return x !== m; }); store('excluded', Q.excluded); });
        wrap.appendChild(h('label', { class: 'bs-mode' + (on ? ' on' : '') }, [cb, h('b', { text: m }), h('span', { class: 'muted small', text: counts[m] !== undefined ? fmtN(counts[m]) : '0' })]));
      });
      modeHost.appendChild(wrap);
      var add = h('input', { class: 'input', placeholder: 'add another mode to exclude, e.g. PAID', maxlength: 40 });
      modeHost.appendChild(h('div', { class: 'bs-add' }, [add, h('button', { class: 'btn ghost sm', type: 'button', onclick: function () {
        var v = add.value.trim().toUpperCase(); if (v && Q.excluded.indexOf(v) === -1) { Q.excluded.push(v); store('excluded', Q.excluded); renderModes(); }
      } }, ['Add'])]));
      modeHost.appendChild(h('p', { class: 'muted small', text: 'Ticked modes are removed first. Check the list: a mode that is NOT ticked stays in the report (e.g. "PAID" vs "PREPAID").' }));
    }
    function loadWb(wb, name) {
      Q.fileName = name; Q.wb = wb;
      var names = wb.SheetNames;
      var pickSheet = function (n) {
        Q.sheet = n; Q.table = OMB.util.sheetToTable(wb.Sheets[n]);
        Q.map = initMap(Q.table.headers);
        Q.result = null; changed(); renderAll();
      };
      // prefer the first sheet that actually has the required columns
      var best = names.find(function (n) { var t = OMB.util.sheetToTable(wb.Sheets[n]); var m = OMB.adr.detectColumns(t.headers); return OMB.adr.REQUIRED.every(function (r) { return m[r] >= 0; }); }) || names[0];
      pickSheet(best);
    }
    function renderAll() {
      clear(out);
      var drop = dropzone('LR raw export (.xlsx / .xls / .csv)', '.xlsx,.xlsm,.xls,.csv', function (f) {
        status.textContent = 'Reading ' + f.name + '…';
        readFile(f).then(function (buf) { later(function () { try { loadWb(OMB.util.readWorkbook(buf, f.name), f.name); status.textContent = ''; } catch (e) { status.textContent = 'Could not read this file: ' + e.message; } }); })
          .catch(function (e) { status.textContent = e.message; });
      });
      var srcKids = [drop, status];
      if (Q.table) {
        var info = h('div', { class: 'bs-chip' }, [h('b', { text: '📄 ' + Q.fileName }), h('span', { class: 'muted', text: fmtN(Q.table.rows.length) + ' rows · ' + Q.table.headers.length + ' columns' })]);
        if (Q.wb.SheetNames.length > 1) info.appendChild(h('label', { class: 'bs-inline' }, ['Sheet: ', select(Q.wb.SheetNames.map(function (n) { return { value: n, label: n }; }), Q.sheet, function (n) { Q.sheet = n; Q.table = OMB.util.sheetToTable(Q.wb.Sheets[n]); Q.map = initMap(Q.table.headers); Q.result = null; renderAll(); })]));
        srcKids.splice(1, 0, info);
      }
      out.appendChild(card('1. LR raw export', srcKids));
      if (!Q.table) { out.appendChild(note('info', 'Upload the raw LR export to begin. Files are processed in your browser and are not uploaded anywhere.')); return; }
      out.appendChild(card('2. Columns used', [mapHost]));
      out.appendChild(card('3. Freight modes to remove (Paid / To-Pay)', [modeHost]));
      var adrC = h('input', { type: 'date', class: 'input' }), goC = h('input', { type: 'date', class: 'input' }), podC = h('input', { type: 'date', class: 'input' });
      adrC.value = Q.adrCutoff || ''; goC.value = Q.goCutoff || ''; podC.value = Q.podCutoff || '';
      var hide = h('input', { type: 'checkbox' }); hide.checked = Q.hideTech !== false;
      out.appendChild(card('4. Cutoff dates (optional - inclusive, blank dates are kept)', [h('div', { class: 'bs-maps' }, [
        h('label', { class: 'bs-field' }, [h('span', { text: 'ADR date: keep ADRs up to' }), adrC]),
        h('label', { class: 'bs-field' }, [h('span', { text: 'Remaining LR: delivery date up to' }), goC]),
        h('label', { class: 'bs-field' }, [h('span', { text: 'Remaining LR: POD received up to' }), podC])]),
        h('label', { class: 'bs-check' }, [hide, 'Hide technical columns in the exported report sheets (PERIOD, branch/consignor code columns)'])]));
      var apply = h('button', { class: 'btn', type: 'button', onclick: function () {
        Q.adrCutoff = adrC.value; Q.goCutoff = goC.value; Q.podCutoff = podC.value; Q.hideTech = hide.checked;
        status.textContent = 'Processing…'; clear(resHost);
        later(function () {
          var r = OMB.adr.run(Q.table, { map: Q.map, excludeModes: Q.excluded, adrCutoff: Q.adrCutoff, goCutoff: Q.goCutoff, podCutoff: Q.podCutoff, delimiter: ',' });
          status.textContent = '';
          if (r.error) { resHost.appendChild(note('bad', r.error)); return; }
          Q.result = r; renderResult(resHost); changed();
        });
      } }, ['▶ Apply filters']);
      out.appendChild(h('div', { class: 'bs-actions' }, [apply]));
      out.appendChild(resHost);
      renderMap(); renderModes();
      if (Q.result) renderResult(resHost);
    }
    function renderResult(host) {
      clear(host);
      var r = Q.result, s = r.stats;
      host.appendChild(card('Result', [
        h('div', { class: 'bs-stats' }, [stat('Raw rows', s.raw), stat('Freight removed', s.freightExcluded, 'bad'), stat('ADR Report', s.adr, 'ok'), stat('Remaining LR', s.remaining, 'ok'), stat('Total removed', s.deleted, 'bad')]),
        h('div', { class: 'bs-pipe' }, [
          ['Raw', s.raw], ['− freight', s.freightExcluded], ['ADR split', s.adrConfirmedBeforeDate + '/' + s.remainingCandidates], ['− ADR date', s.adrDateRemoved],
          ['− POD status', s.podStatusRemoved], ['− delivery', s.goDeliveryRemoved], ['− POD rec.', s.podRecRemoved]].map(function (x) {
          return h('div', { class: 'bs-step' }, [h('b', { text: String(typeof x[1] === 'number' ? fmtN(x[1]) : x[1]) }), h('span', { text: x[0] })]);
        })),
        note(s.reconciled ? 'ok' : 'bad', s.reconciled ? '✔ Reconciled: raw = ADR + remaining + removed (' + fmtN(s.raw) + ' = ' + fmtN(s.adr) + ' + ' + fmtN(s.remaining) + ' + ' + fmtN(s.deleted) + ')' : '✖ Counts do not reconcile - do not use this output.'),
        warningsBox(r.warnings)]));
      var tabs = { adr: ['ADR Report', r.headers, r.adr], rem: ['Remaining LR Report', r.headers, r.remaining],
        rm: ['Removed Records', r.headers.concat(['Removal Stage', 'Removal Reason']), r.removed.map(function (x) { return x.row.concat([x.stage, x.reason]); })] };
      var body = h('div', { class: 'table-wrap bs-preview' }), bar = h('div', { class: 'tabs' });
      function show(k) { Array.prototype.forEach.call(bar.children, function (b) { b.classList.toggle('active', b.dataset.k === k); }); table(body, tabs[k][1], tabs[k][2], 50); }
      Object.keys(tabs).forEach(function (k) { bar.appendChild(h('button', { class: 'tab', type: 'button', 'data-k': k, onclick: function () { show(k); } }, [tabs[k][0] + ' (' + fmtN(tabs[k][2].length) + ')'])); });
      host.appendChild(card('Preview', [bar, body])); show('adr');
      var incRemoved = h('input', { type: 'checkbox' }), incSummary = h('input', { type: 'checkbox' }), incRaw = h('input', { type: 'checkbox' });
      incRemoved.checked = incSummary.checked = true; incRaw.checked = false;
      var st = h('span', { class: 'muted small' });
      host.appendChild(card('Export', [
        h('div', { class: 'bs-checks' }, [h('label', { class: 'bs-check' }, [incRemoved, 'Removed Records sheet (with reasons)']), h('label', { class: 'bs-check' }, [incSummary, 'Summary sheet (counts, filters, columns, warnings)']), h('label', { class: 'bs-check' }, [incRaw, 'Raw Data sheet'])]),
        h('div', { class: 'bs-actions' }, [
          downloadBtn('⬇ Download Excel', function () {
            return OMB.adr.buildWorkbook(r, Q.table, { include: { removed: incRemoved.checked, summary: incSummary.checked, raw: incRaw.checked },
              hide: Q.hideTech === false ? [] : OMB.adr.DEFAULT_HIDDEN_COLUMNS, fileName: Q.fileName, generated: new Date().toLocaleString('en-IN') });
          }, 'LR_Report_Processed_' + stamp() + '.xlsx', st),
          h('button', { class: 'btn ghost', type: 'button', onclick: function () { loadRawWorkbook(OMB.adr.toHandoffWorkbook(r), 'ADR / NET Query result', true); go('branch'); } }, ['↪ Send to Branch-wise Data']),
          h('button', { class: 'btn ghost', type: 'button', onclick: function () { loadRawWorkbook(OMB.adr.toHandoffWorkbook(r), 'ADR / NET Query result', true); go('region'); } }, ['↪ Send to Region Creation']), st])]));
    }
    renderAll();
    return out;
  }

  /* ================================================================= result view shared by tabs 2 & 3 */
  function generatedView(host, res, fileName, extras) {
    clear(host);
    var kids = [h('div', { class: 'bs-stats' }, [stat('Sheets', res.stats.sheets, 'ok'), stat('Records', res.stats.records, 'ok')])];
    if (res.audit && res.audit.reconciliation) {
      var rows = Object.keys(res.audit.reconciliation).map(function (k) {
        var x = res.audit.reconciliation[k];
        return [x.label, x.totalRows, x.branchSheetRows, x.partySheetRows, x.outsideRegion, x.blankCode, x.ok ? '✔' : '✖'];
      });
      var rec = h('div', { class: 'table-wrap' }); table(rec, ['Source', 'Rows with B.CODE', 'In branch sections', 'In PARTY WISE', 'Outside this region', 'No B.CODE', 'Reconciled'], rows, 10);
      kids.push(h('h4', { text: 'Reconciliation' }), rec);
    }
    kids.push(h('h4', { text: 'Warnings & audit' }), warningsBox(res.warnings));
    var prev = h('div', { class: 'table-wrap bs-preview' });
    table(prev, ['Type', 'Name', 'B.CODE(s)', 'Source', 'Records'], res.preview.map(function (p) { return [p.type, p.name, p.codes, p.source, p.count]; }), 80);
    kids.push(h('h4', { text: 'What will be in the workbook' }), prev);
    var st = h('span', { class: 'muted small' });
    kids.push(h('div', { class: 'bs-actions' }, [downloadBtn('⬇ Download Excel', function () { return res.wb; }, fileName, st), st]));
    (extras || []).forEach(function (e) { kids.push(e); });
    host.appendChild(card('Result', kids));
    host.scrollIntoView && host.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function sourceChecks() {
    var a = h('input', { type: 'checkbox' }), p = h('input', { type: 'checkbox' }); a.checked = p.checked = true;
    return { node: h('div', { class: 'bs-checks' }, [h('label', { class: 'bs-check' }, [a, 'ADR sheet']), h('label', { class: 'bs-check' }, [p, 'NET POD sheet'])]),
      get: function () { var s = []; if (a.checked) s.push('adr'); if (p.checked) s.push('pod'); return s; } };
  }
  function summaryCard(tabKey, layout, titleExtra) {
    var host = h('div'), T = S[tabKey];
    function render() {
      clear(host);
      if (!T.summary) {
        host.appendChild(dropzone('Summary file (.xlsx)', '.xlsx,.xlsm', function (f) {
          readFile(f).then(function (buf) {
            var s = OMB.sheets.parseSummary(OMB.util.readWorkbook(buf, f.name), layout);
            T.summary = s; T.summaryName = f.name; render(); changed();
          }).catch(function (e) { host.appendChild(note('bad', e.message)); });
        }));
        host.appendChild(h('p', { class: 'muted small', text: titleExtra }));
      } else {
        var s = T.summary;
        host.appendChild(h('div', { class: 'bs-chip' }, [h('b', { text: '📄 ' + T.summaryName }), h('button', { class: 'btn ghost sm', type: 'button', onclick: function () { T.summary = null; render(); changed(); } }, ['✕ Remove'])]));
        if (!s.ok) host.appendChild(note('bad', '✖ ' + s.error));
        else {
          host.appendChild(note('ok', '✔ ' + s.branches.length + ' branch(es), ' + s.parties.length + ' part' + (s.parties.length === 1 ? 'y' : 'ies') + (s.monthCols.length ? ', ' + s.monthCols.length + ' month column(s) (' + s.monthCols[0].label + ' … ' + s.monthCols[s.monthCols.length - 1].label + ')' : ', no month columns')));
          if (s.partyWarnings.length) host.appendChild(warningsBox(s.partyWarnings.map(function (m) { return { kind: 'party', message: m }; })));
        }
      }
    }
    render();
    return host;
  }

  /* ================================================================= TAB 2 - BRANCH-WISE */
  function branchPanel() {
    var B = S.branch, out = h('div'), resHost = h('div'), status = h('div', { class: 'muted small' });
    var summaryHost = summaryCard('branch', 'nested', 'Layout: branch rows, with a party heading directly above its own branch rows. A blank row ends a party.');
    var mode = 'summary', codes = h('textarea', { class: 'input', rows: 3, placeholder: 'e.g. 101, 102 205\n(one or many branch codes)' });
    var src = sourceChecks(), modeBox = h('div');
    function renderMode() {
      clear(modeBox);
      var r1 = h('input', { type: 'radio', name: 'bmode' }), r2 = h('input', { type: 'radio', name: 'bmode' });
      r1.checked = mode === 'summary'; r2.checked = mode === 'codes';
      r1.addEventListener('change', function () { mode = 'summary'; renderMode(); }); r2.addEventListener('change', function () { mode = 'codes'; renderMode(); });
      modeBox.appendChild(h('div', { class: 'bs-checks' }, [h('label', { class: 'bs-check' }, [r1, 'From a Summary file (all its branches and parties)']), h('label', { class: 'bs-check' }, [r2, 'Only specific branch code(s)'])]));
      if (mode === 'summary') modeBox.appendChild(summaryHost); else modeBox.appendChild(codes);
    }
    var go1 = h('button', { class: 'btn', type: 'button', onclick: function () {
      clear(resHost);
      if (!S.raw || !S.raw.prepared.ok) { resHost.appendChild(note('bad', 'Load the raw workbook first (and fix the column messages above).')); return; }
      var sources = src.get(); if (!sources.length) { resHost.appendChild(note('bad', 'Tick ADR and/or NET POD.')); return; }
      var entries, single = false;
      if (mode === 'codes') {
        var list = (codes.value || '').split(/[,\s;]+/).map(OMB.util.normalizeCode).filter(Boolean);
        list = list.filter(function (c, i) { return list.indexOf(c) === i; });
        if (!list.length) { resHost.appendChild(note('bad', 'Enter at least one branch code.')); return; }
        entries = list.map(function (c) { return { type: 'branch', bcode: c, name: '' }; }); single = true;
      } else {
        if (!B.summary || !B.summary.ok) { resHost.appendChild(note('bad', 'Upload a valid Summary file (or switch to "specific branch codes").')); return; }
        entries = B.summary.branches.map(function (b) { return { type: 'branch', bcode: b.bcode, name: b.name }; })
          .concat(B.summary.parties.map(function (p) { return { type: 'party', name: p.name, bcodes: p.bcodes }; }));
      }
      status.textContent = 'Generating…';
      later(function () {
        var res;
        try { res = OMB.branchwise.generate(S.raw.prepared, entries, { sources: sources, single: single }); } catch (e) { status.textContent = 'Failed: ' + e.message; return; }
        status.textContent = '';
        if (B.summary && mode === 'summary') (B.summary.partyWarnings || []).forEach(function (m) { res.warnings.unshift({ kind: 'party', message: m }); });
        if (!res.wb.SheetNames.length) { resHost.appendChild(note('bad', 'No matching records were found, so no workbook was created.')); resHost.appendChild(warningsBox(res.warnings)); return; }
        generatedView(resHost, res, (mode === 'codes' ? 'Branch_Data_' : 'Branch_Wise_Data_') + stamp() + '.xlsx');
      });
    } }, ['▶ Generate workbook']);
    function build() {
      clear(out);
      out.appendChild(rawCard('branch'));
      out.appendChild(card('2. What to create', [modeBox, src.node]));
      out.appendChild(h('div', { class: 'bs-actions' }, [go1, status]));
      out.appendChild(resHost);
      renderMode();
    }
    build();
    return out;
  }

  /* ================================================================= TAB 3 - REGION */
  function regionPanel() {
    var R = S.region, out = h('div'), resHost = h('div'), status = h('div', { class: 'muted small' });
    var summaryHost = summaryCard('region', 'flat', 'Layout: a block of party rows (name in the B.CODE column) and a separate block of the region\'s branch rows, with month columns and a TOTAL column.');
    var src = sourceChecks();
    var partyMatch = select([{ value: 'prefix', label: 'Starts with (TATA MOTOR → TATA MOTORS LTD)' }, { value: 'exact', label: 'Exact name only' }], 'prefix', function () {});
    var older = select([{ value: 'last', label: 'Last month column (original rule)' }, { value: 'first', label: 'First month column' }], 'last', function () {});
    var autoM = h('input', { type: 'checkbox' }), audit = h('input', { type: 'checkbox' });
    autoM.checked = true; audit.checked = false;
    var go2 = h('button', { class: 'btn', type: 'button', onclick: function () {
      clear(resHost);
      if (!S.raw || !S.raw.prepared.ok) { resHost.appendChild(note('bad', 'Load the raw workbook first (and fix the column messages above).')); return; }
      if (!R.summary || !R.summary.ok) { resHost.appendChild(note('bad', 'Upload a valid Summary file.')); return; }
      var sources = src.get(); if (!sources.length) { resHost.appendChild(note('bad', 'Tick ADR and/or NET POD.')); return; }
      var dateCols = {};
      ['adr', 'pod'].forEach(function (k) { var o = S.raw.overrides[k]; if (o && o.dateCol !== undefined && o.dateCol >= 0) dateCols[k] = o.dateCol; });
      status.textContent = 'Generating…';
      later(function () {
        var res;
        try { res = OMB.region.generate(S.raw.prepared, R.summary, { sources: sources, partyMatch: partyMatch.value, olderPolicy: older.value, autoMonths: autoM.checked, auditSheet: audit.checked, dateCols: dateCols }); }
        catch (e) { status.textContent = 'Failed: ' + e.message; return; }
        status.textContent = '';
        var extras = [];
        if (res.audit.summary) extras.push(h('div', { class: 'bs-note info', text: 'SUMMARY months: ' + res.audit.summary.months.join(', ') + (res.audit.summary.autoMonths ? '  (built from the data)' : '') }));
        generatedView(resHost, res, 'Region_Report_' + stamp() + '.xlsx', extras);
      });
    } }, ['▶ Generate region workbook']);
    out.appendChild(rawCard('region'));
    out.appendChild(card('2. Region Summary', [summaryHost]));
    out.appendChild(card('3. Options', [src.node, h('div', { class: 'bs-maps' }, [
      h('label', { class: 'bs-field' }, [h('span', { text: 'Party name matching' }), partyMatch]),
      h('label', { class: 'bs-field' }, [h('span', { text: 'SUMMARY: rows older than the first month column go to' }), older])]),
      h('label', { class: 'bs-check' }, [autoM, 'If the Summary has no month columns, build them from the data (otherwise no SUMMARY sheet)']),
      h('label', { class: 'bs-check' }, [audit, 'Add an AUDIT sheet (reconciliation + warnings) to the workbook'])]));
    out.appendChild(h('div', { class: 'bs-actions' }, [go2, status]));
    out.appendChild(resHost);
    return out;
  }

  /* ================================================================= shell: tabs + hash routing */
  var PANELS = { query: ['ADR / NET Query', queryPanel], branch: ['Branch-wise Data', branchPanel], region: ['Region Creation', regionPanel] };
  var built = {}, order = ['query', 'branch', 'region'], bar = h('div', { class: 'tabs bs-tabs' }), body = h('div');
  function go(key) {
    if (!PANELS[key]) key = 'query';
    if (!built[key]) built[key] = PANELS[key][1]();
    clear(body); body.appendChild(built[key]);
    Array.prototype.forEach.call(bar.children, function (b) { b.classList.toggle('active', b.dataset.k === key); });
    document.querySelectorAll('[data-sub-mode]').forEach(function (a) { a.classList.toggle('active', a.getAttribute('data-sub-mode') === key); });
    if (location.hash !== '#' + key) history.replaceState(null, '', '#' + key);
  }
  order.forEach(function (k, i) { bar.appendChild(h('button', { class: 'tab', type: 'button', 'data-k': k, onclick: function () { go(k); } }, [(i + 1) + '. ' + PANELS[k][0]])); });
  clear(root);
  root.appendChild(h('div', { class: 'page-head' }, [h('div', {}, [h('h2', { text: 'ADR / NET Billing Suite' }),
    h('p', { text: 'LR export → ADR & Remaining LR → branch-wise sheets → region workbook with month-wise SUMMARY. Everything runs in your browser - files are never uploaded.' })])]));
  root.appendChild(bar); root.appendChild(body);
  root.appendChild(h('p', { class: 'muted small', text: 'Based on the ADR NET QUERY / Branch-wise Data / Region Creation tools (ADR NET QUERY originally by Harsh); audited and merged - see docs/BILLING_SUITE_AUDIT.md.' }));
  window.addEventListener('hashchange', function () { go((location.hash || '').replace('#', '')); });
  go((location.hash || '').replace('#', '') || 'query');
  window.__billingGo = go;                                   // used by the sidebar links / tests
}());
