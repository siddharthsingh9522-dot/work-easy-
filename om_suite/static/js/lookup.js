/* Shared UI helpers for Branch Finder / Customer Search: safe tables + background-job progress */
var Lookup = (function () {
  function readJson(r) {
    if (((r.headers && r.headers.get('content-type')) || '').indexOf('application/json') === -1) {
      return Promise.reject(new Error('Server ne data ki jagah page bheja (HTTP ' + r.status + ') - Ctrl+F5 se refresh karke dobara try karein.'));
    }
    return r.json();
  }

  function table(el, columns, rows) {
    el.innerHTML = '';
    var t = document.createElement('table'); t.className = 'tbl dbp';
    var h = document.createElement('thead'), hr = document.createElement('tr');
    columns.forEach(function (c) { var th = document.createElement('th'); th.textContent = c; hr.appendChild(th); });
    h.appendChild(hr); t.appendChild(h);
    var b = document.createElement('tbody');
    rows.forEach(function (r) {
      var tr = document.createElement('tr');
      columns.forEach(function (c) { var td = document.createElement('td'); td.textContent = r[c] == null ? '' : r[c]; tr.appendChild(td); });
      b.appendChild(tr);
    });
    t.appendChild(b); el.appendChild(t);
  }

  // box: {fill, text, stop, download, counters:{key:label}}
  function track(jobId, box) {
    var timer = null, stopped = false;
    box.root.hidden = false; box.download.innerHTML = ''; box.stop.disabled = false;
    box.stop.onclick = function () {
      box.stop.disabled = true;
      fetch('/jobs/' + jobId + '/stop', { method: 'POST', credentials: 'same-origin' });
    };
    function tick() {
      fetch('/jobs/' + jobId + '/status', { credentials: 'same-origin' }).then(readJson).then(function (d) {
        var pct = d.total ? Math.round(100 * d.current / d.total) : 0;
        box.fill.style.width = pct + '%';
        var extra = Object.keys(box.counters || {}).map(function (k) { return box.counters[k] + ': ' + ((d.extra || {})[k] || 0); }).join(' • ');
        box.text.textContent = d.current + '/' + d.total + ' (' + pct + '%)' + (extra ? ' • ' + extra : '') +
          (d.status === 'running' ? '' : ' • ' + d.status) + (d.error ? ' • ' + d.error : '');
        if (d.status !== 'running') {
          clearInterval(timer); box.stop.disabled = true;
          if (d.download_url) {
            var a = document.createElement('a'); a.className = 'btn'; a.href = d.download_url; a.textContent = '⬇ Download Excel';
            box.download.appendChild(a);
          }
        }
      }).catch(function () { box.text.textContent = 'Status load nahi hua - network check karein.'; });
    }
    tick(); timer = setInterval(tick, 2000);
  }

  function post(url, body, isJson) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: isJson ? JSON.stringify(body) : body,
      headers: isJson ? { 'Content-Type': 'application/json' } : undefined })
      .then(function (r) { return readJson(r).then(function (j) { j._status = r.status; return j; }); });
  }
  return { table: table, track: track, post: post };
})();
