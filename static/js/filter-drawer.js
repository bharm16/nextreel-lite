/**
 * Filter drawer interactions.
 *
 * Wires:
 *  - Drawer open/close (tab, backdrop, ESC).
 *  - Chip ↔ slider sync for range filters (Year, IMDb, Vote Count).
 *  - Genre chip cloud with explicit "All".
 *  - Single-select language chips with "More languages" disclosure.
 *  - Live count badge fetch (debounced) — see attachLiveCount().
 *  - Modified-state diff against rendered default_filters — see attachModifiedDiff().
 *  - Save-default toast — see attachSaveToast().
 */
(function () {
  "use strict";

  // The <script> tag uses `defer`, which already guarantees the DOM is
  // fully parsed by the time this IIFE runs — no need to wait for
  // DOMContentLoaded.
  init();

  function init() {
    var drawer = document.getElementById("filterDrawer");
    var tab = document.getElementById("filterDrawerTab");
    var closeBtn = document.getElementById("filterDrawerClose");
    var backdrop = document.getElementById("filterDrawerBackdrop");
    var form = document.getElementById("drawerFilterForm");
    if (!drawer || !tab || !form || !closeBtn || !backdrop) return;

    attachOpenClose(drawer, tab, closeBtn, backdrop);
    attachGenreChips(form);
    attachRangeChipsAndSliders(form);
    attachLanguageChips(form);
    attachLanguageMore(form);
    attachLiveCount(form);
    attachModifiedDiff(form);
    attachSaveToast(form);
  }

  // ── Open / close ────────────────────────────────────────
  function attachOpenClose(drawer, tab, closeBtn, backdrop) {
    function open() {
      drawer.classList.add("is-open");
      backdrop.classList.add("is-visible");
      tab.setAttribute("aria-expanded", "true");
      document.body.classList.add("filter-drawer-open");
    }
    function close() {
      drawer.classList.remove("is-open");
      backdrop.classList.remove("is-visible");
      tab.setAttribute("aria-expanded", "false");
      document.body.classList.remove("filter-drawer-open");
    }
    tab.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    backdrop.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer.classList.contains("is-open")) close();
    });
  }

  // ── Genre chips ─────────────────────────────────────────
  function attachGenreChips(form) {
    var allGenres = [
      "Action","Adventure","Animation","Biography","Comedy","Crime","Documentary",
      "Drama","Fantasy","Horror","Musical","Sci-Fi","Sport","Thriller","War","Western"
    ];
    var hiddenContainer = form.querySelector("[data-genre-hidden-inputs]");
    var chipRow = form.querySelector('[data-filter-chips="genres"]');
    if (!hiddenContainer || !chipRow) return;

    function syncHidden() {
      // Use replaceChildren() to clear safely (no innerHTML, no XSS surface)
      hiddenContainer.replaceChildren();
      var allActive = chipRow.querySelector('[data-genre-chip="__all__"]').classList.contains("is-active");
      if (allActive) {
        form.dispatchEvent(new CustomEvent("filter:changed"));
        return;
      }
      chipRow.querySelectorAll('[data-genre-chip]').forEach(function (c) {
        var v = c.getAttribute("data-genre-chip");
        if (v === "__all__") return;
        if (c.classList.contains("is-active")) {
          var input = document.createElement("input");
          input.type = "hidden";
          input.name = "genres[]";
          input.value = v;
          hiddenContainer.appendChild(input);
        }
      });
      form.dispatchEvent(new CustomEvent("filter:changed"));
    }

    chipRow.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-genre-chip]");
      if (!btn) return;
      var value = btn.getAttribute("data-genre-chip");
      if (value === "__all__") {
        chipRow.querySelectorAll('[data-genre-chip]').forEach(function (c) {
          c.classList.remove("is-active");
          c.setAttribute("aria-pressed", "false");
        });
        btn.classList.add("is-active");
        btn.setAttribute("aria-pressed", "true");
      } else {
        var allChip = chipRow.querySelector('[data-genre-chip="__all__"]');
        allChip.classList.remove("is-active");
        allChip.setAttribute("aria-pressed", "false");
        btn.classList.toggle("is-active");
        btn.setAttribute("aria-pressed", btn.classList.contains("is-active") ? "true" : "false");

        var activeSpecifics = chipRow.querySelectorAll('[data-genre-chip].is-active:not([data-genre-chip="__all__"])');
        if (activeSpecifics.length === 0 || activeSpecifics.length === allGenres.length) {
          chipRow.querySelectorAll('[data-genre-chip]').forEach(function (c) {
            c.classList.remove("is-active");
            c.setAttribute("aria-pressed", "false");
          });
          allChip.classList.add("is-active");
          allChip.setAttribute("aria-pressed", "true");
        }
      }
      syncHidden();
    });
  }

  // ── Range chips + sliders ───────────────────────────────
  function attachRangeChipsAndSliders(form) {
    form.querySelectorAll("[data-dual-slider]").forEach(function (sliderRoot) {
      var minHandle = sliderRoot.querySelector('[data-slider-handle="min"]');
      var maxHandle = sliderRoot.querySelector('[data-slider-handle="max"]');
      var label = sliderRoot.querySelector("[data-slider-label]");
      var hiddenMin = sliderRoot.parentElement.querySelector("[data-hidden-min]");
      var hiddenMax = sliderRoot.parentElement.querySelector("[data-hidden-max]");
      var section = sliderRoot.closest("section");
      var chipRow = section ? section.querySelector("[data-filter-chips]") : null;
      var step = parseFloat(sliderRoot.getAttribute("data-step")) || 1;
      var isFloat = step < 1;

      function fmt(v) {
        return isFloat ? Number(v).toFixed(1) : Math.round(Number(v)).toString();
      }
      function updateLabel() {
        if (label) label.textContent = fmt(minHandle.value) + " – " + fmt(maxHandle.value);
      }
      function clamp() {
        var mn = parseFloat(minHandle.value);
        var mx = parseFloat(maxHandle.value);
        if (mn > mx) {
          minHandle.value = String(Math.min(mn, mx));
          maxHandle.value = String(Math.max(mn, mx));
        }
      }
      function syncHidden() {
        hiddenMin.value = minHandle.value;
        hiddenMax.value = maxHandle.value;
      }
      function deactivateChips() {
        if (!chipRow) return;
        chipRow.querySelectorAll(".filter-chip").forEach(function (c) {
          c.classList.remove("is-active");
          c.setAttribute("aria-pressed", "false");
        });
      }
      function syncChipsToValues() {
        if (!chipRow) return;
        var mn = minHandle.value, mx = maxHandle.value;
        chipRow.querySelectorAll("[data-range-chip]").forEach(function (c) {
          var cm = c.getAttribute("data-min");
          var cmax = c.getAttribute("data-max");
          if (cm === mn && cmax === mx) {
            c.classList.add("is-active");
            c.setAttribute("aria-pressed", "true");
          } else {
            c.classList.remove("is-active");
            c.setAttribute("aria-pressed", "false");
          }
        });
      }

      [minHandle, maxHandle].forEach(function (h) {
        h.addEventListener("input", function () {
          clamp();
          updateLabel();
          syncHidden();
          deactivateChips();
        });
        h.addEventListener("change", function () {
          form.dispatchEvent(new CustomEvent("filter:changed"));
        });
      });

      if (chipRow) {
        chipRow.addEventListener("click", function (e) {
          var btn = e.target.closest("[data-range-chip]");
          if (!btn) return;
          minHandle.value = btn.getAttribute("data-min");
          maxHandle.value = btn.getAttribute("data-max");
          updateLabel();
          syncHidden();
          chipRow.querySelectorAll("[data-range-chip]").forEach(function (c) {
            c.classList.remove("is-active");
            c.setAttribute("aria-pressed", "false");
          });
          btn.classList.add("is-active");
          btn.setAttribute("aria-pressed", "true");
          form.dispatchEvent(new CustomEvent("filter:changed"));
        });
      }

      updateLabel();
      syncHidden();
      syncChipsToValues();
    });
  }

  // ── Language chips ─────────────────────────────────────
  function attachLanguageChips(form) {
    var hidden = form.querySelector("[data-hidden-language]");
    if (!hidden) return;
    form.querySelectorAll("[data-lang-chip]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var v = chip.getAttribute("data-lang-chip");
        hidden.value = v;
        form.querySelectorAll("[data-lang-chip]").forEach(function (c) {
          c.classList.remove("is-active");
          c.setAttribute("aria-pressed", "false");
        });
        chip.classList.add("is-active");
        chip.setAttribute("aria-pressed", "true");
        form.dispatchEvent(new CustomEvent("filter:changed"));
      });
    });
  }

  function attachLanguageMore(form) {
    var btn = form.querySelector("[data-lang-more]");
    var row = form.querySelector("[data-lang-more-row]");
    if (!btn || !row) return;
    btn.addEventListener("click", function () {
      var hidden = row.classList.toggle("hidden");
      btn.setAttribute("aria-expanded", hidden ? "false" : "true");
    });
  }

  // ── Stubs filled in by Task 7/8 ─────────────────────────
  function attachLiveCount(form) {
    var badge = document.querySelector("[data-filter-count-badge]");
    var text = document.querySelector("[data-filter-count-text]");
    if (!badge || !text) return;

    var debounceMs = 150;
    var timer = null;
    var inflight = null;

    function fmt(n) {
      if (n >= 1000) return "~" + (Math.round(n / 10) * 10).toLocaleString() + " matches";
      return n.toLocaleString() + " matches";
    }

    function schedule() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(fetchCount, debounceMs);
    }

    function fetchCount() {
      if (inflight) inflight.abort();
      inflight = new AbortController();
      badge.classList.add("is-loading");
      text.textContent = "Counting…";

      var data = new FormData(form);
      fetch("/api/filter_count", {
        method: "POST",
        body: data,
        signal: inflight.signal,
        credentials: "same-origin",
      })
        .then(function (r) {
          if (!r.ok) throw new Error("count_failed");
          return r.json();
        })
        .then(function (j) {
          text.textContent = fmt(j.count);
          badge.classList.remove("is-loading");
        })
        .catch(function (err) {
          if (err && err.name === "AbortError") return;
          badge.classList.remove("is-loading");
          text.textContent = "Count unavailable";
        });
    }

    form.addEventListener("filter:changed", schedule);
    // Also fire on every drawer open so the badge refreshes when reopened on a
    // new movie or after upstream state changes (no { once: true } — we want
    // every reopen to recount).
    document.getElementById("filterDrawerTab").addEventListener("click", schedule);
  }
  function attachModifiedDiff(form) {
    var statusRow = form.querySelector("[data-filter-status-row]");
    var resetLink = form.querySelector("[data-filter-reset-link]");
    var defaultsScript = document.getElementById("default-filters-data");
    if (!statusRow || !resetLink || !defaultsScript) return;

    var defaults = {};
    try { defaults = JSON.parse(defaultsScript.textContent || "{}"); } catch (e) { return; }

    function readCurrent() {
      var fd = new FormData(form);
      return {
        year_min: parseInt(fd.get("year_min") || "0", 10),
        year_max: parseInt(fd.get("year_max") || "0", 10),
        imdb_score_min: parseFloat(fd.get("imdb_score_min") || "0"),
        imdb_score_max: parseFloat(fd.get("imdb_score_max") || "0"),
        num_votes_min: parseInt(fd.get("num_votes_min") || "0", 10),
        num_votes_max: parseInt(fd.get("num_votes_max") || "0", 10),
        language: fd.get("language") || "any",
        genres_selected: fd.getAll("genres[]").slice().sort(),
        exclude_watched: fd.getAll("exclude_watched").indexOf("on") >= 0,
        exclude_watchlist: fd.getAll("exclude_watchlist").indexOf("on") >= 0,
      };
    }

    function eq(cur, def) {
      var defGenres = (def.genres_selected || []).slice().sort();
      return (
        cur.year_min === def.year_min &&
        cur.year_max === def.year_max &&
        cur.imdb_score_min === def.imdb_score_min &&
        cur.imdb_score_max === def.imdb_score_max &&
        cur.num_votes_min === def.num_votes_min &&
        cur.num_votes_max === def.num_votes_max &&
        cur.language === def.language &&
        JSON.stringify(cur.genres_selected) === JSON.stringify(defGenres) &&
        cur.exclude_watched === def.exclude_watched &&
        cur.exclude_watchlist === def.exclude_watchlist
      );
    }

    function refresh() {
      var modified = !eq(readCurrent(), defaults);
      statusRow.classList.toggle("hidden", !modified);
    }

    function applyDefaultsToForm() {
      setRange("year_min", "year_max", defaults.year_min, defaults.year_max);
      setRange("imdb_score_min", "imdb_score_max", defaults.imdb_score_min, defaults.imdb_score_max);
      setRange("num_votes_min", "num_votes_max", defaults.num_votes_min, defaults.num_votes_max);

      var langHidden = form.querySelector("[data-hidden-language]");
      if (langHidden) {
        langHidden.value = defaults.language || "any";
        form.querySelectorAll("[data-lang-chip]").forEach(function (c) {
          var active = c.getAttribute("data-lang-chip") === langHidden.value;
          c.classList.toggle("is-active", active);
          c.setAttribute("aria-pressed", active ? "true" : "false");
        });
      }

      var hiddenContainer = form.querySelector("[data-genre-hidden-inputs]");
      var chipRow = form.querySelector('[data-filter-chips="genres"]');
      if (hiddenContainer && chipRow) {
        hiddenContainer.replaceChildren();
        var defGenres = defaults.genres_selected || [];
        var allActive = defGenres.length === 0;
        chipRow.querySelectorAll("[data-genre-chip]").forEach(function (c) {
          var v = c.getAttribute("data-genre-chip");
          var active = (v === "__all__" && allActive) || (v !== "__all__" && defGenres.indexOf(v) >= 0);
          c.classList.toggle("is-active", active);
          c.setAttribute("aria-pressed", active ? "true" : "false");
        });
        defGenres.forEach(function (g) {
          var input = document.createElement("input");
          input.type = "hidden";
          input.name = "genres[]";
          input.value = g;
          hiddenContainer.appendChild(input);
        });
      }

      var ew = form.querySelector("#excludeWatched");
      if (ew) ew.checked = !!defaults.exclude_watched;
      var ewl = form.querySelector("#excludeWatchlist");
      if (ewl) ewl.checked = !!defaults.exclude_watchlist;

      form.dispatchEvent(new CustomEvent("filter:changed"));
      refresh();
    }

    function setRange(minName, maxName, mnVal, mxVal) {
      var minHidden = form.querySelector('[name="' + minName + '"][data-hidden-min]');
      var maxHidden = form.querySelector('[name="' + maxName + '"][data-hidden-max]');
      if (minHidden) minHidden.value = mnVal;
      if (maxHidden) maxHidden.value = mxVal;
      var section = minHidden ? minHidden.closest("section") : null;
      if (!section) return;
      var minHandle = section.querySelector('[data-slider-handle="min"]');
      var maxHandle = section.querySelector('[data-slider-handle="max"]');
      if (minHandle) minHandle.value = mnVal;
      if (maxHandle) maxHandle.value = mxVal;
      var label = section.querySelector("[data-slider-label]");
      if (label && minHandle && maxHandle) {
        var step = parseFloat(section.querySelector("[data-dual-slider]").getAttribute("data-step")) || 1;
        var isFloat = step < 1;
        var fmt = function (v) {
          return isFloat ? Number(v).toFixed(1) : Math.round(Number(v)).toString();
        };
        label.textContent = fmt(minHandle.value) + " – " + fmt(maxHandle.value);
      }
      section.querySelectorAll("[data-range-chip]").forEach(function (c) {
        var match = c.getAttribute("data-min") === String(mnVal) && c.getAttribute("data-max") === String(mxVal);
        c.classList.toggle("is-active", match);
        c.setAttribute("aria-pressed", match ? "true" : "false");
      });
    }

    resetLink.addEventListener("click", applyDefaultsToForm);
    form.addEventListener("filter:changed", refresh);
    form.addEventListener("change", refresh);
    refresh();
  }
  function attachSaveToast(form) {
    // The save button lives in .filter-drawer-footer (sibling of the form,
    // associated via the HTML5 form="drawerFilterForm" attribute), so it is
    // NOT a DOM descendant of `form` — scope this lookup to the document.
    var saveBtn = document.querySelector("[data-save-default-btn]");
    var toast = document.querySelector("[data-filter-toast]");
    if (!saveBtn || !toast) return;

    // Submit save-defaults via fetch so we can show a toast without losing the
    // user's current filter selection (which a full POST navigation would clear).
    saveBtn.addEventListener("click", function (e) {
      e.preventDefault();
      var data = new FormData(form);
      fetch(saveBtn.getAttribute("formaction"), {
        method: "POST",
        body: data,
        credentials: "same-origin",
      })
        .then(function (r) {
          if (!r.ok) throw new Error("save_failed");
          showToast("Saved as your defaults");
        })
        .catch(function () {
          showToast("Couldn't save — try again");
        });
    });

    function showToast(msg) {
      toast.textContent = msg;
      toast.classList.remove("hidden");
      toast.classList.remove("is-visible");
      void toast.offsetWidth; // restart CSS animation
      toast.classList.add("is-visible");
      setTimeout(function () {
        toast.classList.add("hidden");
        toast.classList.remove("is-visible");
      }, 2400);
    }
  }
})();
