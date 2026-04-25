/**
 * Navbar interactions: mobile menu open/close, avatar dropdown, theme sync.
 *
 * Lives in an external file rather than inline because the production CSP
 * (`script-src 'self' …`, no `'unsafe-inline'`) blocks inline <script> blocks.
 */
(function () {
  // Mobile menu open/close
  var menuBtn = document.getElementById('menuBtn');
  var menuClose = document.getElementById('menuClose');
  var mobileMenu = document.getElementById('mobileMenu');
  if (menuBtn && mobileMenu) {
    menuBtn.addEventListener('click', function () {
      mobileMenu.classList.add('open');
      menuBtn.setAttribute('aria-expanded', 'true');
    });
  }
  if (menuClose && mobileMenu) {
    menuClose.addEventListener('click', function () {
      mobileMenu.classList.remove('open');
      if (menuBtn) menuBtn.setAttribute('aria-expanded', 'false');
    });
  }

  // Avatar dropdown
  var avatarBtn = document.getElementById('avatarBtn');
  var avatarMenu = document.getElementById('avatarMenu');
  if (avatarBtn && avatarMenu) {
    avatarBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = avatarMenu.classList.toggle('open');
      avatarBtn.setAttribute('aria-expanded', String(isOpen));
    });
    document.addEventListener('click', function () {
      avatarMenu.classList.remove('open');
      avatarBtn.setAttribute('aria-expanded', 'false');
    });
    avatarMenu.addEventListener('click', function (e) { e.stopPropagation(); });
  }

  // Mirror persisted theme preference to data-theme on load.
  try {
    var pref = localStorage.getItem('nr-theme');
    if (pref === 'dark' || pref === 'light') {
      document.documentElement.setAttribute('data-theme', pref);
    }
  } catch (e) {}
})();
