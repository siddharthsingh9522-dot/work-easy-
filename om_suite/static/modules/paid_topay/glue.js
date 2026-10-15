/* Connects the shell sidebar (sub-links like /paid-topay#topaygen) to the Paid/ToPay tool's own mode switcher. */
(function () {
  function hashMode() { return (location.hash || '').replace('#', '') || 'dashboard'; }
  function highlight(mode) {
    document.querySelectorAll('[data-sub-mode]').forEach(function (a) {
      a.classList.toggle('active', a.getAttribute('data-sub-mode') === mode);
    });
  }
  function go(mode) {
    var el = document.querySelector('.ptp-app .sideNav-item[data-mode="' + mode + '"]');
    if (el) el.click();          // the tool's own delegated click handler does the switch
    highlight(mode);
  }
  // keep URL + sidebar highlight in sync when the user clicks the tool's own dashboard cards / back links
  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target.closest('.ptp-app [data-mode]') : null;
    if (!el || el.classList.contains('sideNav-item')) return;
    var m = el.getAttribute('data-mode');
    history.replaceState(null, '', m === 'dashboard' ? location.pathname : '#' + m);
    highlight(m);
  });
  window.addEventListener('hashchange', function () { go(hashMode()); });
  go(hashMode());
})();
