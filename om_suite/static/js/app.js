(function () {
  var sidebar = document.getElementById('sidebar'), scrim = document.getElementById('scrim'),
      menuBtn = document.getElementById('menuBtn');
  function toggle(open) {
    if (!sidebar) return;
    sidebar.classList.toggle('open', open);
    scrim.classList.toggle('show', open);
  }
  if (menuBtn) menuBtn.addEventListener('click', function () { toggle(!sidebar.classList.contains('open')); });
  if (scrim) scrim.addEventListener('click', function () { toggle(false); });
  if (sidebar) sidebar.addEventListener('click', function (e) {
    if (e.target.closest('a')) toggle(false);
  });

  // user menu: close on outside click / Esc
  var um = document.getElementById('userMenu');
  document.addEventListener('click', function (e) { if (um && um.open && !um.contains(e.target)) um.open = false; });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && um) um.open = false; });

  // confirm dialogs: <button data-confirm="...">
  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-confirm]') : null;
    if (b && !window.confirm(b.getAttribute('data-confirm'))) e.preventDefault();
  });

  // forms with data-busy="message": disable buttons + show the message while the server works
  document.addEventListener('submit', function (e) {
    var f = e.target, msg = f.getAttribute && f.getAttribute('data-busy');
    if (!msg) return;
    setTimeout(function () {
      f.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
      var note = document.createElement('span'); note.className = 'muted small'; note.style.marginLeft = '.7rem'; note.textContent = msg;
      var last = f.querySelector('button[type=submit]'); if (last && last.parentNode) last.parentNode.appendChild(note);
    }, 0);
  });

  // auto-hide plain success flashes
  setTimeout(function () {
    document.querySelectorAll('.flash.ok').forEach(function (f) { f.style.display = 'none'; });
  }, 6000);

  // same-origin fetch() POST/PUT/DELETE automatically carries the CSRF token
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  if (csrfMeta && window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      init = init || {};
      var method = String(init.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
      if (method !== 'GET' && method !== 'HEAD') {
        try {
          var u = new URL(typeof input === 'string' ? input : input.url, location.href);
          if (u.origin === location.origin) {
            var h = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined) || {});
            h.set('X-CSRF-Token', csrfMeta.content);
            init.headers = h;
          }
        } catch (e) { /* ignore */ }
      }
      return origFetch.call(this, input, init);
    };
  }

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(function () {});
})();
