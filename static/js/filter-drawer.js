/**
 * Filter Drawer — open/close, accordion sections, AJAX submit, persistence.
 * Loaded on movie detail pages only (deferred).
 *
 * The shared _filter_form.html partial outputs sections with data-filter-group
 * attributes. This script wraps those groups into accordion panels
 * inside the drawer (one section open at a time).
 */
(function () {
  "use strict";

  // ── DOM refs ───────────────────────────────
  var drawer = document.getElementById("filterDrawer");
  var backdrop = document.getElementById("filterDrawerBackdrop");
  var tab = document.getElementById("filterDrawerTab");
  var closeBtn = document.getElementById("filterDrawerClose");
  var form = document.getElementById("drawerFilterForm");
  var applyBtn = document.getElementById("drawerApplyBtn");
  var resetBtn = document.getElementById("drawerResetBtn");
  var errorsDiv = document.getElementById("drawer-filter-errors");

  if (!drawer || !tab || !form || !closeBtn || !backdrop) return;

  // ── Build accordion sections from data-filter-group ──
  var SECTION_LABELS = {
    ratings: "Ratings & Votes",
    year: "Year & Language",
    genres: "Genres",
    watched: "Watched",
  };
  var SECTION_ORDER = ["ratings", "year", "genres", "watched"];

  function buildCollapsibleSections() {
    var body = drawer.querySelector(".filter-drawer-body");
    if (!body) return;

    // Collect sections by group
    var groups = {};
    var sections = form.querySelectorAll("[data-filter-group]");
    sections.forEach(function (el) {
      var group = el.getAttribute("data-filter-group");
      if (!groups[group]) groups[group] = [];
      groups[group].push(el);
    });

    // Remove the original 2-column grid wrapper — we're going single-column
    var grid = form.querySelector(".grid");
    if (grid) {
      while (grid.firstChild) {
        form.insertBefore(grid.firstChild, grid);
      }
      grid.remove();
    }

    // Remove all space-y-8 column wrappers left from the partial
    form.querySelectorAll(":scope > .space-y-8").forEach(function (col) {
      while (col.firstChild) {
        form.insertBefore(col.firstChild, col);
      }
      col.remove();
    });

    // Now wrap each group in an accordion section
    SECTION_ORDER.forEach(function (groupName, idx) {
      var elements = groups[groupName];
      if (!elements || elements.length === 0) return;

      var sectionId = "filterSection_" + groupName;
      var isFirst = idx === 0;

      // Create wrapper
      var wrapper = document.createElement("div");
      wrapper.className = "mb-2";

      // Toggle button with +/- icon
      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "filter-section-toggle";
      toggle.setAttribute("aria-expanded", String(isFirst));
      toggle.setAttribute("aria-controls", sectionId);
      toggle.innerHTML =
        "<span>" + (SECTION_LABELS[groupName] || groupName) + "</span>" +
        '<span class="toggle-icon">' + (isFirst ? "−" : "+") + '</span>';

      // Content container
      var content = document.createElement("div");
      content.id = sectionId;
      content.className = "filter-section-content" + (isFirst ? " open" : "");

      var inner = document.createElement("div");
      inner.className = "space-y-4 py-3";

      // Move the actual filter sections into the accordion content
      elements.forEach(function (el) {
        inner.appendChild(el);
      });

      content.appendChild(inner);
      wrapper.appendChild(toggle);
      wrapper.appendChild(content);

      form.appendChild(wrapper);
    });

    // Move the errors div to the top of the form
    if (errorsDiv && form.firstChild !== errorsDiv) {
      var csrf = form.querySelector('input[name="csrf_token"]');
      if (csrf && csrf.nextSibling) {
        form.insertBefore(errorsDiv, csrf.nextSibling);
      }
    }
  }

  buildCollapsibleSections();

  // ── Open / Close ───────────────────────────
  function openDrawer() {
    drawer.classList.add("open");
    backdrop.classList.add("open");
    tab.classList.add("hidden");
    tab.setAttribute("aria-expanded", "true");
    document.body.style.overflow = "hidden";
    setTimeout(function () {
      var first = drawer.querySelector('input:not([type="hidden"]), select, button');
      if (first) first.focus();
    }, 220);
  }

  function closeDrawer() {
    drawer.classList.remove("open");
    backdrop.classList.remove("open");
    tab.classList.remove("hidden");
    tab.setAttribute("aria-expanded", "false");
    document.body.style.overflow = "";
    sessionStorage.removeItem("filterDrawerOpen");
    tab.focus();
  }

  tab.addEventListener("click", openDrawer);
  closeBtn.addEventListener("click", closeDrawer);
  backdrop.addEventListener("click", closeDrawer);

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && drawer.classList.contains("open")) {
      closeDrawer();
    }
  });

  // ── Focus trap ─────────────────────────────
  drawer.addEventListener("keydown", function (e) {
    if (e.key !== "Tab") return;
    var focusable = drawer.querySelectorAll(
      'button, [href], input:not([type="hidden"]), select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length === 0) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });

  // ── Accordion toggle (one-at-a-time) + persistence ──
  var SECTION_STORAGE_KEY = "filterDrawerSections";

  function loadSectionStates() {
    try {
      var raw = sessionStorage.getItem(SECTION_STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function saveSectionStates() {
    var states = {};
    drawer.querySelectorAll(".filter-section-toggle").forEach(function (btn) {
      var id = btn.getAttribute("aria-controls");
      states[id] = btn.getAttribute("aria-expanded") === "true";
    });
    try {
      sessionStorage.setItem(SECTION_STORAGE_KEY, JSON.stringify(states));
    } catch (_) {}
  }

  function closeAllSections() {
    drawer.querySelectorAll(".filter-section-toggle").forEach(function (btn) {
      var targetId = btn.getAttribute("aria-controls");
      var target = document.getElementById(targetId);
      if (!target) return;
      btn.setAttribute("aria-expanded", "false");
      btn.querySelector(".toggle-icon").textContent = "+";
      target.classList.remove("open");
    });
  }

  function openSection(btn) {
    var targetId = btn.getAttribute("aria-controls");
    var target = document.getElementById(targetId);
    if (!target) return;
    btn.setAttribute("aria-expanded", "true");
    btn.querySelector(".toggle-icon").textContent = "−";
    target.classList.add("open");
  }

  function initSections() {
    var saved = loadSectionStates();
    drawer.querySelectorAll(".filter-section-toggle").forEach(function (btn) {
      var targetId = btn.getAttribute("aria-controls");
      var target = document.getElementById(targetId);
      if (!target) return;

      // Restore saved state if available
      if (saved && saved.hasOwnProperty(targetId)) {
        var isOpen = saved[targetId];
        btn.setAttribute("aria-expanded", String(isOpen));
        btn.querySelector(".toggle-icon").textContent = isOpen ? "−" : "+";
        target.classList.toggle("open", isOpen);
      }

      btn.addEventListener("click", function () {
        var wasExpanded = btn.getAttribute("aria-expanded") === "true";
        // Close all sections first (accordion behavior)
        closeAllSections();
        // If it was closed, open it; if it was open, leave all closed
        if (!wasExpanded) {
          openSection(btn);
        }
        saveSectionStates();
      });
    });
  }

  initSections();

  // ── Genre "Select All" / "Clear All" links ──
  var selectAllBtn = form.querySelector("#selectAllBtn");
  var clearAllBtn = form.querySelector("#clearAllBtn");
  var genreToggles = form.querySelector(".genre-toggles");

  function getGenreBoxes() {
    return genreToggles
      ? Array.from(genreToggles.querySelectorAll('input[type="checkbox"]'))
      : [];
  }

  if (selectAllBtn && genreToggles) {
    selectAllBtn.addEventListener("click", function () {
      getGenreBoxes().forEach(function (cb) { cb.checked = true; });
    });
  }

  if (clearAllBtn && genreToggles) {
    clearAllBtn.addEventListener("click", function () {
      getGenreBoxes().forEach(function (cb) { cb.checked = false; });
    });
  }

  // ── Error display ──────────────────────────
  function clearErrors() {
    if (errorsDiv) errorsDiv.innerHTML = "";
    form.querySelectorAll("[aria-invalid]").forEach(function (el) {
      el.removeAttribute("aria-invalid");
    });
  }

  function displayErrors(errors) {
    if (!errorsDiv) return;
    var html = "";
    for (var key in errors) {
      if (!errors.hasOwnProperty(key)) continue;
      html += '<div class="drawer-error">' + escapeHtml(errors[key]) + "</div>";
    }
    errorsDiv.innerHTML = html;
    errorsDiv.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // ── AJAX form submission ───────────────────
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearErrors();

    var formData = new FormData(form);
    var csrfToken = formData.get("csrf_token");

    if (applyBtn) {
      applyBtn.setAttribute("aria-busy", "true");
      applyBtn.disabled = true;
      var spinner = applyBtn.querySelector("svg");
      if (spinner) spinner.classList.remove("hidden");
    }

    fetch("/filtered_movie", {
      method: "POST",
      headers: {
        "X-CSRFToken": csrfToken,
        Accept: "application/json",
      },
      body: formData,
    })
      .then(function (resp) {
        return resp.json().then(function (data) {
          return { status: resp.status, data: data };
        });
      })
      .then(function (result) {
        var data = result.data;
        if (data.ok && data.redirect) {
          sessionStorage.setItem("filterDrawerOpen", "true");
          window.location.href = data.redirect;
          return;
        }
        if (data.errors) {
          displayErrors(data.errors);
        }
        resetApplyBtn();
      })
      .catch(function () {
        displayErrors({ form: "Something went wrong. Please try again." });
        resetApplyBtn();
      });
  });

  function resetApplyBtn() {
    if (!applyBtn) return;
    applyBtn.setAttribute("aria-busy", "false");
    applyBtn.disabled = false;
    var spinner = applyBtn.querySelector("svg");
    if (spinner) spinner.classList.add("hidden");
  }

  // ── Reset handler ──────────────────────────
  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      // After form reset, re-check all genre toggles
      setTimeout(function () {
        getGenreBoxes().forEach(function (cb) { cb.checked = true; });
      }, 0);
    });
  }

  // ── Drawer persistence across navigation ───
  if (sessionStorage.getItem("filterDrawerOpen") === "true") {
    openDrawer();
  }
})();
