/**
 * Navbar scroll-aware surface toggle.
 *
 * Adds `.navbar--solid` to `[data-navbar]` when scrollY > THRESHOLD.
 * rAF-throttled so the listener is cheap even on long pages.
 */
(function () {
  var THRESHOLD = 40;
  var navbar = document.querySelector('[data-navbar]');
  if (!navbar) return;

  var ticking = false;
  var isSolid = false;

  function update() {
    var nextSolid = window.scrollY > THRESHOLD;
    if (nextSolid !== isSolid) {
      isSolid = nextSolid;
      navbar.classList.toggle('navbar--solid', isSolid);
    }
    ticking = false;
  }

  function onScroll() {
    if (!ticking) {
      window.requestAnimationFrame(update);
      ticking = true;
    }
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  // Handle browser-restored scroll position on back/forward nav.
  update();
})();
