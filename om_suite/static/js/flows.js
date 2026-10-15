/* TBB / Bill / Previous mail preview page: confirm, review, send (all calls are JSON) */
(function () {
  var box = document.getElementById('flow'); if (!box) return;
  var base = box.getAttribute('data-base'), slug = box.getAttribute('data-slug'), aiOn = box.getAttribute('data-ai') === '1';
  var msg = document.getElementById('msg');
  function say(t, bad) { msg.textContent = t; msg.style.color = bad ? '#B3261E' : ''; }
  function api(path, body, method) {
    return fetch(base + path, { method: method || 'POST', credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        if ((r.headers.get('content-type') || '').indexOf('application/json') === -1) throw new Error('Server ne data ki jagah page bheja (HTTP ' + r.status + ') - Ctrl+F5 se refresh karein.');
        return r.json().then(function (j) { j._status = r.status; return j; });
      });
  }
  function rows() { return Array.prototype.slice.call(document.querySelectorAll('.flow-table tbody tr')); }
  function items() {
    var o = {}; rows().forEach(function (tr) { o[tr.dataset.code] = { email: tr.querySelector('.em').value, cc: tr.querySelector('.cc').value, bcc: tr.querySelector('.bcc').value }; });
    return o;
  }
  function opts() {
    var test = document.getElementById('testOn').checked;
    return { test: test, test_to: document.getElementById('testTo').value, via: document.querySelector('input[name=via]:checked').value };
  }
  var pillMap = { SENT: 'live', FAILED: 'blocked', SKIPPED: 'warn', SENDING: 'soon', QUEUED: 'soon', DRAFT: '' };
  var rvMap = { ok: 'live', manual_ok: 'live', review: 'warn', missing: 'blocked', ai_error: 'blocked', unavailable: '', pending: '' };
  function pill(text, cls) { var s = document.createElement('span'); s.className = 'pill ' + (cls || ''); s.textContent = text; return s; }
  function setStatus(tr, s) {
    var td = tr.querySelector('.st'); td.innerHTML = ''; td.appendChild(pill(s.status, pillMap[s.status]));
    var e = document.createElement('div'); e.className = 'muted small er'; e.textContent = s.error || ''; td.appendChild(e);
    var sel = tr.querySelector('.sel'); if (sel) sel.disabled = s.status !== 'DRAFT';
    ['em', 'cc', 'bcc'].forEach(function (c) { tr.querySelector('.' + c).readOnly = s.status === 'SENT'; });
  }
  function setReview(tr, status, reason) {
    var td = tr.querySelector('.rv'); td.innerHTML = ''; td.appendChild(pill(status === 'manual_ok' ? 'reviewed' : status, rvMap[status]));
    var r = document.createElement('div'); r.className = 'muted small rs'; r.textContent = reason || ''; td.appendChild(r);
  }
  function refresh() {
    return api('/status', null, 'GET').then(function (d) {
      if (!d.ok) return;
      Object.keys(d.counts).forEach(function (k) { var el = document.querySelector('[data-k=' + k + ']'); if (el) el.textContent = d.counts[k]; });
      rows().forEach(function (tr) { var s = d.rows[tr.dataset.code]; if (s) { setStatus(tr, s); setReview(tr, s.review, s.reason); } });
      return d;
    });
  }
  // test mode toggles the address box
  var testOn = document.getElementById('testOn'), testBox = document.getElementById('testBox');
  function tm() { testBox.style.display = testOn.checked ? '' : 'none'; } testOn.addEventListener('change', tm); tm();

  document.getElementById('selAll').onclick = function () { rows().forEach(function (tr) { var c = tr.querySelector('.sel'); if (!c.disabled) c.checked = true; }); };
  document.getElementById('selNone').onclick = function () { rows().forEach(function (tr) { var c = tr.querySelector('.sel'); if (!c.disabled) c.checked = false; }); };
  document.getElementById('confirm').onclick = function () {
    var chosen = rows().filter(function (tr) { return tr.querySelector('.sel').checked; }).map(function (tr) { return tr.dataset.code; });
    if (!window.confirm(chosen.length + ' row(s) QUEUED hongi; baaki DRAFT rows SKIPPED (kabhi nahi bheji jayengi). Pakka?')) return;
    api('/confirm', { selected: chosen }).then(function (d) { say(d.ok ? 'Selection confirm ho gayi.' : (d.error || 'Error'), !d.ok); return refresh(); });
  };
  document.getElementById('reviewAll').onclick = function () {
    if (!window.confirm('Jin rows me recipient email hai unhe "Reviewed" mark karein? (Pehle preview/recipients khud check kar lein.)')) return;
    api('/review_all', { items: items() }).then(function (d) { say(d.ok ? d.marked + ' row(s) reviewed.' : (d.error || 'Error'), !d.ok); return refresh(); });
  };
  document.getElementById('sendAll').onclick = function () {
    var o = opts();
    if (o.test && (o.test_to || '').indexOf('@') < 1) { say('Test address daalein.', true); return; }
    var q = o.test ? 'TEST address par' : 'ASLI recipients ko';
    if (!window.confirm('Queued + reviewed saari mails ' + q + ' bhejni hain?')) return;
    say('Bhej rahe hain…');
    api('/send_all', Object.assign({ items: items() }, o)).then(function (d) {
      if (!d.ok) { say(d.error || 'Error', true); return; }
      say('Sent: ' + d.sent + ' • Thunderbird queue: ' + d.queued + ' • Failed: ' + d.failed + (d.skipped && d.skipped.length ? ' • Not reviewed/no email: ' + d.skipped.length : ''), d.failed > 0);
      return refresh();
    }).catch(function (e) { say(e.message, true); });
  };
  document.getElementById('retry').onclick = function () { api('/retry_failed', {}).then(function (d) { say((d.retried || []).length + ' failed row(s) dobara QUEUED.'); return refresh(); }); };

  // per-row actions
  document.querySelector('.flow-table tbody').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    var tr = b.closest('tr'), code = tr.dataset.code, em = tr.querySelector('.em').value;
    if (b.classList.contains('a-view')) {
      api('/mail/' + encodeURIComponent(code), null, 'GET').then(function (d) {
        document.getElementById('vSubject').textContent = d.subject; document.getElementById('vAtt').textContent = d.attachment ? '📎 ' + d.attachment : '';
        var f = document.getElementById('vFrame'), t = document.getElementById('vText');
        if (d.html) { f.hidden = false; t.hidden = true; f.srcdoc = d.html; } else { f.hidden = true; t.hidden = false; t.textContent = d.text; }
        document.getElementById('viewer').showModal();
      });
    } else if (b.classList.contains('a-ai')) {
      api('/ai_check/' + encodeURIComponent(code), { email: em }).then(function (d) { setReview(tr, d.status, d.reason); });
    } else if (b.classList.contains('a-fix')) {
      api('/ai_fix/' + encodeURIComponent(code), { email: em }).then(function (d) { if (d.email) tr.querySelector('.em').value = d.email; setReview(tr, d.status, d.reason); });
    } else if (b.classList.contains('a-rev')) {
      api('/review/' + encodeURIComponent(code), { email: em }).then(function (d) { if (d.ok) setReview(tr, 'manual_ok', 'Reviewed'); else say(d.error, true); });
    } else if (b.classList.contains('a-send')) {
      var o = opts();
      if (o.test && (o.test_to || '').indexOf('@') < 1) { say('Test address daalein.', true); return; }
      if (!o.test && !window.confirm(code + ': mail ASLI recipients ko bhejni hai?')) return;
      api('/send/' + encodeURIComponent(code), Object.assign({ email: em, cc: tr.querySelector('.cc').value, bcc: tr.querySelector('.bcc').value }, o)).then(function (d) {
        say(d.ok ? (code + ': ' + (d.status === 'already_sent' ? 'pehle hi bheji ja chuki hai' : d.status)) : (code + ': ' + d.error), !d.ok); return refresh();
      }).catch(function (e2) { say(e2.message, true); });
    } else if (b.classList.contains('a-find')) {
      say('Thunderbird me purani mail dhoondh rahe hain… (extension ko ~1 minute lag sakta hai)');
      api('/find/' + encodeURIComponent(code), {}, 'POST').then(function (d) {
        if (!d.ok) { say(d.error || 'Error', true); return; }
        var tries = 0, t = setInterval(function () {
          api('/find_result/' + d.job_id, null, 'GET').then(function (r) {
            tries++;
            if (r.status === 'DONE' || r.status === 'ERROR' || tries > 60) {
              clearInterval(t);
              var p = r.profile || {};
              if (r.status === 'DONE' && (p.to || []).length) {
                tr.querySelector('.em').value = (p.to || []).join(', '); tr.querySelector('.cc').value = (p.cc || []).join(', '); tr.querySelector('.bcc').value = (p.bcc || []).join(', ');
                say(code + ': purani mail se recipients mil gaye (confidence ' + (p.confidence != null ? p.confidence : '?') + '). Check karke Reviewed karein.');
              } else say(code + ': ' + (r.error || 'Thunderbird me matching mail nahi mili.'), true);
            }
          });
        }, 3000);
      });
    }
  });
  document.getElementById('vClose').onclick = function () { document.getElementById('viewer').close(); };
  // pick up Thunderbird results while anything is still SENDING
  setInterval(function () { if (document.querySelector('.st .pill.soon') ) refresh(); }, 5000);
})();
