/**
 * Spotlight search modal controller.
 *
 * Opens on:
 *   - Click of `#searchSpotlightTrigger` / `#searchSpotlightTriggerMobile`
 *   - `/` keypress anywhere on the page (unless a text input is focused)
 *
 * Closes on:
 *   - Escape key
 *   - Click on backdrop
 *   - Result selection
 */
(function () {
  var DEBOUNCE_MS = 150;
  var MIN_QUERY_LENGTH = 2;

  var backdrop = document.getElementById('searchSpotlightBackdrop');
  var modal = document.getElementById('searchSpotlight');
  var input = document.getElementById('searchSpotlightInput');
  var resultsEl = document.getElementById('searchSpotlightResults');
  var desktopTrigger = document.getElementById('searchSpotlightTrigger');
  var mobileTrigger = document.getElementById('searchSpotlightTriggerMobile');

  if (!backdrop || !modal || !input || !resultsEl) return;

  var debounceTimer = null;
  var currentResults = [];
  var activeIndex = -1;
  var lastFocusedElement = null;
  var currentController = null;
  var inertedSiblings = [];

  function openModal() {
    lastFocusedElement = document.activeElement;
    // Focus trap via `inert`: disable everything outside the modal/backdrop so
    // Tab (and screen-reader virtual cursors) can't escape the dialog. Falls
    // back to no-op on older browsers that ignore the attribute, which still
    // leaves aria-modal + Escape/backdrop close paths working.
    inertedSiblings = [];
    Array.prototype.forEach.call(document.body.children, function (el) {
      if (el !== backdrop && el !== modal && !el.hasAttribute('inert')) {
        el.setAttribute('inert', '');
        inertedSiblings.push(el);
      }
    });
    backdrop.classList.add('open');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    backdrop.setAttribute('aria-hidden', 'false');
    [desktopTrigger, mobileTrigger].forEach(function (t) {
      if (t) t.setAttribute('aria-expanded', 'true');
    });
    window.requestAnimationFrame(function () { input.focus(); input.select(); });
  }

  function closeModal() {
    // Cancel any in-flight search so late responses don't race into a closed modal.
    if (currentController) { currentController.abort(); currentController = null; }
    backdrop.classList.remove('open');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    backdrop.setAttribute('aria-hidden', 'true');
    [desktopTrigger, mobileTrigger].forEach(function (t) {
      if (t) t.setAttribute('aria-expanded', 'false');
    });
    input.value = '';
    renderEmpty('Start typing to search…');
    // Restore interactivity to the rest of the page.
    inertedSiblings.forEach(function (el) { el.removeAttribute('inert'); });
    inertedSiblings = [];
    if (lastFocusedElement && lastFocusedElement.focus) {
      lastFocusedElement.focus();
    }
  }

  function renderEmpty(message) {
    resultsEl.innerHTML = '';
    var li = document.createElement('li');
    li.className = 'search-spotlight-empty';
    li.textContent = message;
    resultsEl.appendChild(li);
    currentResults = [];
    activeIndex = -1;
  }

  function renderResults(results) {
    resultsEl.innerHTML = '';
    if (!results.length) {
      renderEmpty('No films found.');
      return;
    }
    currentResults = results;
    results.forEach(function (r, idx) {
      var li = document.createElement('li');
      var a = document.createElement('a');
      // Server builds the canonical /movie/<slug>-<public_id> URL — see
      // search.py. We just consume it; never construct a movie URL on the
      // client (the public_id is opaque, not derivable from tconst).
      a.href = r.url || '/';
      a.className = 'search-spotlight-result';
      a.setAttribute('role', 'option');
      a.dataset.index = String(idx);

      var thumb = document.createElement('span');
      thumb.className = 'search-spotlight-result-thumb';
      // Posters come from movie_projection when the title has been enriched.
      // The gradient remains visible behind for results that haven't.
      if (r.poster_url) {
        thumb.style.backgroundImage = "url('" + r.poster_url.replace(/'/g, "\\'") + "')";
      }

      var title = document.createElement('span');
      title.className = 'search-spotlight-result-title';
      title.textContent = r.title || 'Untitled';

      var meta = document.createElement('span');
      meta.className = 'search-spotlight-result-meta';
      var metaParts = [];
      if (r.year) metaParts.push(r.year);
      if (typeof r.rating === 'number' && r.rating > 0) {
        metaParts.push('★ ' + r.rating.toFixed(1));
      }
      meta.textContent = metaParts.join(' · ');

      a.appendChild(thumb);
      a.appendChild(title);
      if (metaParts.length) a.appendChild(meta);
      li.appendChild(a);
      resultsEl.appendChild(li);
    });
    activeIndex = -1;
  }

  function setActiveIndex(idx) {
    var rows = resultsEl.querySelectorAll('.search-spotlight-result');
    rows.forEach(function (r) { r.classList.remove('is-active'); r.setAttribute('aria-selected', 'false'); });
    if (idx < 0 || idx >= rows.length) {
      activeIndex = -1;
      return;
    }
    rows[idx].classList.add('is-active');
    rows[idx].setAttribute('aria-selected', 'true');
    activeIndex = idx;
  }

  function performSearch(q) {
    var trimmed = (q || '').trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      renderEmpty('Start typing to search…');
      return;
    }
    // Cancel any pending earlier request so a slower response can't overwrite
    // a newer one (fast typing would otherwise race result sets into view).
    if (currentController) currentController.abort();
    currentController = new AbortController();
    var signal = currentController.signal;
    fetch('/api/search?q=' + encodeURIComponent(trimmed), {
      credentials: 'same-origin',
      signal: signal,
    })
      .then(function (res) { return res.ok ? res.json() : { results: [] }; })
      .then(function (data) { renderResults((data && data.results) || []); })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;  // superseded; ignore
        renderEmpty("Couldn't reach the catalog. Try again.");
      });
  }

  input.addEventListener('input', function () {
    if (debounceTimer) clearTimeout(debounceTimer);
    var q = input.value;
    debounceTimer = setTimeout(function () { performSearch(q); }, DEBOUNCE_MS);
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { e.preventDefault(); closeModal(); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(Math.min(activeIndex + 1, currentResults.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(Math.max(activeIndex - 1, 0));
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      var rows = resultsEl.querySelectorAll('.search-spotlight-result');
      if (rows[activeIndex]) rows[activeIndex].click();
    }
  });

  backdrop.addEventListener('click', closeModal);
  if (desktopTrigger) desktopTrigger.addEventListener('click', openModal);
  if (mobileTrigger) mobileTrigger.addEventListener('click', openModal);

  // Global `/` keybind — ignored when a text input is focused.
  document.addEventListener('keydown', function (e) {
    if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
    var target = e.target;
    var isEditable = target && (
      target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.isContentEditable
    );
    if (isEditable) return;
    e.preventDefault();
    openModal();
  });
})();
