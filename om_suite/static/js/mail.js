/* Mail Center: send-box guards + live Thunderbird batch progress */
var MailCenter = (function () {
  function initSend(form) {
    var kind = form.getAttribute('data-kind');
    var test = form.querySelector('input[name=test]'), testTo = form.querySelector('input[name=test_to]'),
        confirm = form.querySelector('input[name=confirm]'), btn = form.querySelector('button[type=submit]'),
        box = document.getElementById('testBox_' + kind), target = document.getElementById('target_' + kind);
    function update() {
      box.style.display = test.checked ? '' : 'none';
      target.textContent = test.checked ? '(TEST address par)' : '(ASLI recipients ko)';
      target.style.color = test.checked ? '' : '#B3261E'; target.style.fontWeight = test.checked ? '' : '700';
      btn.disabled = !(confirm.checked && (!test.checked || (testTo.value || '').indexOf('@') > 0));
    }
    [test, testTo, confirm].forEach(function (e) { e.addEventListener('input', update); e.addEventListener('change', update); });
    form.addEventListener('submit', function (e) {
      if (!test.checked && !window.confirm('Test mode band hai - mails ASLI recipients ko jayengi. Pakka bhejni hain?')) e.preventDefault();
    });
    update();
  }

  function initProgress(el) {
    var url = el.getAttribute('data-url'), batchUrl = el.getAttribute('data-batch-url'), batch = el.getAttribute('data-batch');
    var fill = document.getElementById('pfill'), text = document.getElementById('ptext'), note = document.getElementById('pnote'),
        body = document.querySelector('#ptable tbody'), retry = document.getElementById('pRetry'), cancel = document.getElementById('pCancel');
    var timer = null;
    function act(action) {
      fetch(batchUrl, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch: batch, action: action }) }).then(tick);
    }
    retry.onclick = function () { act('retry'); }; cancel.onclick = function () { act('cancel'); };
    function tick() {
      fetch(url, { credentials: 'same-origin' }).then(function (r) { if ((r.headers.get('content-type') || '').indexOf('application/json') === -1) throw new Error('page'); return r.json(); }).then(function (d) {
        var total = d.total || 0;
        fill.style.width = (total ? Math.round(100 * d.done / total) : 0) + '%';
        text.textContent = d.done + '/' + total + ' sent • ' + d.error + ' failed • ' + d.pending + ' pending' + (d.cancelled ? ' • ' + d.cancelled + ' cancelled' : '');
        body.innerHTML = '';
        d.rows.forEach(function (r) {
          var tr = document.createElement('tr');
          [r.branch_code + ' ' + r.branch_name, r.status, r.to, r.error].forEach(function (v, i) {
            var td = document.createElement('td'); td.textContent = v == null ? '' : v;
            if (i === 1) { var s = document.createElement('span'); s.className = 'pill ' + (r.status === 'DONE' ? 'live' : r.status === 'ERROR' ? 'blocked' : 'warn'); s.textContent = v; td.textContent = ''; td.appendChild(s); }
            tr.appendChild(td);
          });
          body.appendChild(tr);
        });
        retry.hidden = !d.error; cancel.hidden = !d.pending;
        var stale = !d.seen || (Date.now() / 1000 - d.seen) > 180;
        note.textContent = d.pending
          ? (stale ? '⚠ Extension abhi connect nahi dikh raha - Thunderbird khula hai? (Thunderbird setup page dekhein.) Mails queue me safe hain.'
                   : 'Mails Thunderbird extension se uthti hain (har 1 min; extension popup me "Sync" dabane par turant). Thunderbird khula rehna chahiye.')
          : '';
        if (!d.pending && timer) { clearInterval(timer); timer = null; }
      }).catch(function () { note.textContent = 'Progress load nahi hui - network check karein.'; });
    }
    tick(); timer = setInterval(tick, 3000);
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form.send-box').forEach(initSend);
    var p = document.getElementById('progress'); if (p) initProgress(p);
    var c = document.getElementById('copyCode'); if (c) c.addEventListener('click', function () {
      var t = document.getElementById('codeBox'); t.select(); (navigator.clipboard ? navigator.clipboard.writeText(t.value) : Promise.resolve(document.execCommand('copy')))
        .then(function () { c.textContent = '✔ Copied'; setTimeout(function () { c.textContent = 'Copy code'; }, 2000); });
    });
  });
  return {};
})();
