/**
 * Landing-page filter pills — in-place hero reroll.
 *
 * Wires click handlers on the pill buttons in the right column. On click,
 * toggles the corresponding URL param via History API and fetches a new
 * landing film from /api/landing-film matching the new filter combination.
 * Updates the backdrop, credit corner, "See this film" link href, and the
 * primary CTA form (re-targets between /next_movie and /filtered_movie
 * depending on whether any filters are active).
 *
 * Respects prefers-reduced-motion (skips the backdrop fade animations).
 *
 * No external dependencies.
 */

(() => {
  'use strict';

  const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // URL schema mappings — must mirror movies/landing_filter_url.py and
  // movies/filter_parser.VALID_GENRES. Keep these in sync; on mismatch the
  // server silently drops invalid params, so the UI would render a "filter is
  // set" state for a value the backend ignores.
  const RUNTIME_VALID = new Set(['lt90', 'lt120', 'gt150']);
  const RATING_VALID = new Set(['6plus', '7plus', '8plus']);
  const DECADE_VALID = new Set(['1970s', '1980s', '1990s', '2000s', '2010s', '2020s']);
  const GENRE_VALID = new Set([
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime',
    'Documentary', 'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History',
    'Horror', 'Music', 'Musical', 'Mystery', 'News', 'Romance', 'Sci-Fi',
    'Short', 'Sport', 'Thriller', 'War', 'Western',
  ]);

  const root = document.getElementById('landing-pills');
  if (!root) return;

  const bg = document.getElementById('landing-bg');
  const credit = document.getElementById('landing-credit');
  const headline = document.getElementById('landing-headline');
  const sub = document.getElementById('landing-sub');
  const seeThisLink = document.getElementById('landing-see-this');
  const actions = document.querySelector('.landing-actions');

  // === URL state helpers ===

  function readActiveFilters() {
    const params = new URLSearchParams(window.location.search);
    const active = {};
    if (params.has('genre') && GENRE_VALID.has(params.get('genre'))) {
      active.genre = params.get('genre');
    }
    if (params.has('decade') && DECADE_VALID.has(params.get('decade'))) {
      active.decade = params.get('decade');
    }
    if (params.has('runtime') && RUNTIME_VALID.has(params.get('runtime'))) {
      active.runtime = params.get('runtime');
    }
    if (params.has('rating') && RATING_VALID.has(params.get('rating'))) {
      active.rating = params.get('rating');
    }
    return active;
  }

  function writeActiveFilters(active) {
    const params = new URLSearchParams();
    Object.keys(active).forEach((k) => params.set(k, active[k]));
    const qs = params.toString();
    const url = qs ? `?${qs}` : window.location.pathname;
    window.history.pushState({ active }, '', url);
  }

  // === Pill state UI ===

  function syncPillsToActive(active) {
    const pills = root.querySelectorAll('.landing-pill[data-filter-key]');
    pills.forEach((p) => {
      const key = p.dataset.filterKey;
      const value = p.dataset.filterValue;
      const isActive = active[key] === value;
      p.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
  }

  // === Form action sync ===

  function rewriteCtaForm(active, film) {
    if (!actions) return;
    if (!film) return; // Empty-state has no Pick-Another form to rewrite.

    const form = actions.querySelector('form');
    if (!form) return;

    // Compute form-schema hidden inputs from active URL state.
    // Keys MUST match what infra/filter_normalizer.py:normalize_filters reads:
    // 'genres[]' (getlist), 'year_min', 'year_max', 'imdb_score_min'.
    // Runtime has no form-key counterpart in normalize_filters.
    const formInputs = {};
    if (active.genre) formInputs['genres[]'] = active.genre;
    if (active.decade) {
      const yearsByDecade = {
        '1970s': [1970, 1979], '1980s': [1980, 1989], '1990s': [1990, 1999],
        '2000s': [2000, 2009], '2010s': [2010, 2019], '2020s': [2020, 2029],
      };
      const yrs = yearsByDecade[active.decade];
      if (yrs) {
        formInputs.year_min = String(yrs[0]);
        formInputs.year_max = String(yrs[1]);
      }
    }
    // Runtime is intentionally dropped — /filtered_movie's normalize_filters has no runtime field.
    if (active.rating === '6plus') formInputs.imdb_score_min = '6.0';
    else if (active.rating === '7plus') formInputs.imdb_score_min = '7.0';
    else if (active.rating === '8plus') formInputs.imdb_score_min = '8.0';

    const hasFilters = Object.keys(formInputs).length > 0;
    form.action = hasFilters ? '/filtered_movie' : '/next_movie';

    // Remove existing filter-input children (keep csrf_token).
    Array.from(form.querySelectorAll('input[type="hidden"]')).forEach((inp) => {
      if (inp.name !== 'csrf_token') inp.remove();
    });
    // Add fresh ones.
    Object.keys(formInputs).forEach((k) => {
      const inp = document.createElement('input');
      inp.type = 'hidden';
      inp.name = k;
      inp.value = formInputs[k];
      form.appendChild(inp);
    });
  }

  // === Empty state DOM ===

  function renderEmptyState() {
    if (headline) headline.textContent = 'No films match these filters.';
    if (sub) sub.textContent = 'Try removing one.';
    if (bg) {
      bg.style.backgroundImage = "url('/static/img/backdrop-placeholder.svg')";
    }
    if (credit) credit.style.display = 'none';
    if (seeThisLink) seeThisLink.style.display = 'none';
    // Replace primary CTA with Clear-filters link.
    if (actions) {
      const form = actions.querySelector('form');
      if (form) {
        const link = document.createElement('a');
        link.className = 'landing-cta-primary';
        link.href = '/';
        link.textContent = 'Clear filters';
        form.replaceWith(link);
      }
    }
  }

  // === Hydrate film into the page ===

  function renderFilm(film) {
    if (!film) {
      renderEmptyState();
      return;
    }
    if (bg) {
      bg.style.backgroundImage = `url('${film.backdrop_url}')`;
    }
    if (credit) {
      const titleAndYear = film.year
        ? `Film still: ${film.title} (${film.year})`
        : `Film still: ${film.title}`;
      credit.textContent = titleAndYear;
      credit.style.display = '';
    }
    if (seeThisLink) {
      // Use the canonical path the server built — never reproduce the
      // slugifier client-side (a bare /movie/<public_id> 404s because the
      // route regex requires <slug>-<public_id>; see movies/movie_url.py).
      seeThisLink.href = film.movie_path || '/';
      seeThisLink.style.display = '';
    }
    // Restore Pick-Another form if it was replaced by Clear-filters.
    if (actions && !actions.querySelector('form')) {
      const link = actions.querySelector('a.landing-cta-primary[href="/"]');
      if (link) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.style.display = 'inline';
        const csrf = document.createElement('input');
        csrf.type = 'hidden';
        csrf.name = 'csrf_token';
        // Re-use the value rendered by the server elsewhere if present.
        const existing = document.querySelector('input[name="csrf_token"]');
        csrf.value = existing ? existing.value : '';
        form.appendChild(csrf);
        const btn = document.createElement('button');
        btn.type = 'submit';
        btn.className = 'landing-cta-primary';
        btn.textContent = 'Pick another →';
        form.appendChild(btn);
        link.replaceWith(form);
      }
    }
    // Restore default headline / sub if we're coming out of empty state.
    // Apostrophes here MUST match templates/home.html exactly (ASCII U+0027,
    // not U+2019) so empty-state restore doesn't visibly flicker the text.
    if (headline && headline.textContent.startsWith('No films match')) {
      headline.textContent = '';
      headline.appendChild(document.createTextNode("A film you haven't seen."));
      headline.appendChild(document.createElement('br'));
      headline.appendChild(document.createTextNode('Every time you ask.'));
    }
    if (sub && sub.textContent.startsWith('Try removing')) {
      sub.textContent = "Mark what you've seen. Filter the rest. Every pick is fresh.";
    }
  }

  // === Fetch + apply ===

  async function fetchAndApply(active) {
    const params = new URLSearchParams();
    Object.keys(active).forEach((k) => params.set(k, active[k]));
    const url = `/api/landing-film${params.toString() ? '?' + params.toString() : ''}`;

    if (!REDUCED_MOTION && bg) bg.classList.add('is-loading');

    try {
      const resp = await fetch(url, { headers: { Accept: 'application/json' } });
      if (resp.status === 204) {
        renderFilm(null);
      } else if (resp.ok) {
        const film = await resp.json();
        renderFilm(film);
      } else {
        // 4xx/5xx — leave the page unchanged.
        // eslint-disable-next-line no-console
        console.warn('landing-film fetch failed', resp.status);
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn('landing-film fetch error', err);
    } finally {
      if (!REDUCED_MOTION && bg) {
        // Tiny delay so the fade-out is perceivable.
        setTimeout(() => bg.classList.remove('is-loading'), 60);
      }
      rewriteCtaForm(active, true);
    }
  }

  // === Click handler ===

  root.addEventListener('click', (ev) => {
    const target = ev.target.closest('.landing-pill[data-filter-key]');
    if (!target) return;

    ev.preventDefault();
    const key = target.dataset.filterKey;
    const value = target.dataset.filterValue;

    const active = readActiveFilters();
    if (active[key] === value) {
      // Click on active pill — deactivate.
      delete active[key];
    } else {
      // Click on inactive pill — activate (replaces any prior value at this key).
      active[key] = value;
    }

    writeActiveFilters(active);
    syncPillsToActive(active);
    fetchAndApply(active);
  });

  // === Browser back/forward ===

  window.addEventListener('popstate', () => {
    const active = readActiveFilters();
    syncPillsToActive(active);
    fetchAndApply(active);
  });

  // === Initial state sync ===
  // Server already rendered the right pills in the active state and the
  // right CTA form. This is a defensive no-op for direct navigation.
  syncPillsToActive(readActiveFilters());
})();
