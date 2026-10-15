/* AI Chatbot UI: safe mini-markdown + fetch to /dbp/chatbot/ask */
var DBPChat = (function () {
  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function md(s) {
    return esc(String(s))
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/^\s*---+\s*$/gm, '<hr>')
      .replace(/\n/g, '<br>');
  }
  function add(chat, role, text, raw) {
    var m = document.createElement('div'); m.className = 'msg ' + role;
    var b = document.createElement('div'); b.className = 'bubble';
    if (raw) b.textContent = text; else b.innerHTML = md(text);
    m.appendChild(b); chat.appendChild(m); chat.scrollTop = chat.scrollHeight; return b;
  }
  function init(askUrl) {
    var chat = document.getElementById('chat'), form = document.getElementById('chatForm'), q = document.getElementById('chatQ');
    chat.querySelectorAll('.bubble[data-md]').forEach(function (b) { b.innerHTML = md(b.getAttribute('data-md')); });
    chat.scrollTop = chat.scrollHeight;
    var busy = false;
    function ask(text) {
      text = (text || '').trim(); if (!text || busy) return;
      busy = true; add(chat, 'user', text, true);
      var wait = add(chat, 'assistant', '…', true);
      fetch(askUrl, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ q: text }) })
        .then(function (r) {
          if ((r.headers.get('content-type') || '').indexOf('application/json') === -1) throw new Error('Server ne data ki jagah page bheja (HTTP ' + r.status + ') - Ctrl+F5 se refresh karein.');
          return r.json().then(function (j) { return { ok: r.ok, j: j }; });
        })
        .then(function (res) { wait.innerHTML = res.ok ? md(res.j.answer) : '⚠ ' + esc(res.j.error || 'Error'); })
        .catch(function (e) { wait.textContent = '⚠ ' + (e && e.message ? e.message : 'Network error - dobara try karein.'); })
        .then(function () { busy = false; chat.scrollTop = chat.scrollHeight; });
    }
    form.addEventListener('submit', function (e) { e.preventDefault(); var t = q.value; q.value = ''; ask(t); });
    document.querySelectorAll('.quick [data-q]').forEach(function (b) { b.addEventListener('click', function () { ask(b.getAttribute('data-q')); }); });
  }
  return { init: init };
})();
