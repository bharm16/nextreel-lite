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

  function currentScroll() {
    // body is the scroller because html, body { height: 100% } in base CSS;
    // fall back to window for any page that doesn't set that.
    return document.body.scrollTop || document.documentElement.scrollTop || window.scrollY || 0;
  }

  function update() {
    var nextSolid = currentScroll() > THRESHOLD;
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

  // Listen on both: window covers document-scrolled pages, body covers ones
  // where the body itself is the scroll container.
  window.addEventListener('scroll', onScroll, { passive: true });
  document.body.addEventListener('scroll', onScroll, { passive: true });
  // Handle browser-restored scroll position on back/forward nav.
  update();
})();
