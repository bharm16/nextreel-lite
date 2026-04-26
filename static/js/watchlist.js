(function () {
  "use strict";

  var grid = document.getElementById("watched-grid");
  if (!grid) return;

  var toolbar = document.getElementById("watched-toolbar");
  var searchInput = document.getElementById("watched-search");
  var sortSelect = document.getElementById("watched-sort");
  var loadMoreBtn = document.getElementById("watched-load-more");
  var gridFooter = document.getElementById("watched-grid-footer");
  var toastEl = document.getElementById("watched-toast");
  var filterCountEl = document.getElementById("watched-filter-count");
  var showingEl = document.getElementById("watched-showing");
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") || "" : "";
  var letterboxdTrigger = document.getElementById("watched-letterboxd-trigger");
  var letterboxdFile = document.getElementById("watched-letterboxd-file");
  var letterboxdForm = document.getElementById("watched-letterboxd-form");
  var toastTimer = null;
  var searchTimer = null;

  // ── Scroll wash on toolbar ──
  if (toolbar) {
    var scrollThreshold = toolbar.offsetTop;
    function onScroll() {
      if (window.scrollY > scrollThreshold) {
        toolbar.classList.add("is-scrolled");
      } else {
        toolbar.classList.remove("is-scrolled");
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // ── Client-side search (title filter) ──
  if (searchInput) {
    searchInput.addEventListener("input", function () {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        var query = (searchInput.value || "").trim().toLowerCase();
        var cards = grid.querySelectorAll(".watched-card");
        var visible = 0;
        cards.forEach(function (card) {
          var match = !query || (card.dataset.search || "").indexOf(query) !== -1;
          card.style.display = match ? "" : "none";
          if (match) visible++;
        });
        if (query && filterCountEl) {
          filterCountEl.hidden = false;
          if (showingEl) showingEl.textContent = visible;
        } else if (filterCountEl) {
          filterCountEl.hidden = true;
        }
      }, 100);
    });
  }

  // ── Sort change → full page reload with params ──
  if (sortSelect) {
    sortSelect.addEventListener("change", function () {
      var url = new URL(window.location.href);
      url.searchParams.set("sort", sortSelect.value);
      url.searchParams.delete("page");
      window.location.href = url.toString();
    });
  }

  // ── Filter chips ──
  var chips = document.querySelectorAll(".watched-chip");
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var filterType = chip.dataset.filter;

      if (filterType === "all") {
        window.location.href = window.location.pathname +
          (sortSelect ? "?sort=" + sortSelect.value : "");
        return;
      }

      var url = new URL(window.location.href);
      url.searchParams.delete("page");

      var paramName = filterType === "decade" ? "decades"
                    : filterType === "rating" ? "rating"
                    : "genres";

      if (filterType === "rating") {
        var currentRating = url.searchParams.get("rating");
        if (currentRating === chip.dataset.value) {
          url.searchParams.delete("rating");
        } else {
          url.searchParams.set("rating", chip.dataset.value);
        }
      } else {
        var current = url.searchParams.get(paramName);
        var values = current ? current.split(",") : [];
        var val = chip.dataset.value;
        var idx = values.indexOf(val);
        if (idx > -1) {
          values.splice(idx, 1);
        } else {
          values.push(val);
        }
        if (values.length > 0) {
          url.searchParams.set(paramName, values.join(","));
        } else {
          url.searchParams.delete(paramName);
        }
      }

      window.location.href = url.toString();
    });
  });

  // Mark active chips based on current URL params
  (function markActiveChips() {
    var url = new URL(window.location.href);
    var hasAnyFilter = url.searchParams.has("decades") ||
                       url.searchParams.has("rating") ||
                       url.searchParams.has("genres");

    chips.forEach(function (chip) {
      var filterType = chip.dataset.filter;
      if (filterType === "all") {
        chip.setAttribute("aria-pressed", hasAnyFilter ? "false" : "true");
        if (hasAnyFilter) {
          chip.classList.remove("watched-chip--active");
        } else {
          chip.classList.add("watched-chip--active");
        }
        return;
      }

      var paramName = filterType === "decade" ? "decades"
                    : filterType === "rating" ? "rating"
                    : "genres";
      var paramVal = url.searchParams.get(paramName) || "";
      var isActive = false;

      if (filterType === "rating") {
        isActive = paramVal === chip.dataset.value;
      } else {
        var vals = paramVal ? paramVal.split(",") : [];
        isActive = vals.indexOf(chip.dataset.value) > -1;
      }

      chip.setAttribute("aria-pressed", isActive ? "true" : "false");
      if (isActive) {
        chip.classList.add("watched-chip--active");
      } else {
        chip.classList.remove("watched-chip--active");
      }
    });
  })();

  // ── Load more ──
  if (loadMoreBtn) {
    loadMoreBtn.addEventListener("click", function () {
      var page = parseInt(loadMoreBtn.dataset.page, 10);
      var perPage = parseInt(loadMoreBtn.dataset.perPage, 10) || 60;
      loadMoreBtn.textContent = "Loading...";
      loadMoreBtn.disabled = true;

      var url = new URL(window.location.href);
      url.searchParams.set("page", page);
      url.searchParams.set("per_page", perPage);

      fetch(url.toString(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data.html) {
            grid.insertAdjacentHTML("beforeend", data.html);
          }
          if (data.has_more) {
            loadMoreBtn.dataset.page = page + 1;
            loadMoreBtn.textContent = "Load more";
            loadMoreBtn.disabled = false;
          } else {
            gridFooter.innerHTML =
              '<div class="watched-end-mark">' +
              '<hr class="watched-end-rule" />' +
              '<p class="watched-end-text">That\u2019s all ' + data.total + "</p>" +
              "</div>";
          }
        })
        .catch(function () {
          loadMoreBtn.textContent = "Load more";
          loadMoreBtn.disabled = false;
          showToast("Couldn\u2019t load more films. Try again.");
        });
    });
  }

  // ── Remove + Undo ──
  var lastRemoved = null;

  function showToast(message, undoCallback) {
    if (!toastEl) return;
    if (toastTimer) clearTimeout(toastTimer);

    if (undoCallback) {
      toastEl.innerHTML = "";
      var span = document.createElement("span");
      span.textContent = message;
      toastEl.appendChild(span);
      var undoBtn = document.createElement("button");
      undoBtn.className = "watched-toast-undo";
      undoBtn.textContent = "Undo";
      undoBtn.addEventListener("click", function () {
        undoCallback();
        toastEl.hidden = true;
        if (toastTimer) clearTimeout(toastTimer);
      });
      toastEl.appendChild(undoBtn);
      undoBtn.focus();
    } else {
      toastEl.textContent = message;
    }

    toastEl.hidden = false;
    toastTimer = setTimeout(function () {
      toastEl.hidden = true;
      lastRemoved = null;
    }, 5000);
  }

  grid.addEventListener("click", function (event) {
    var button = event.target.closest(".watched-remove");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();

    var card = button.closest(".watched-card");
    var tconst = button.dataset.tconst;
    if (!card || !tconst) return;

    card.style.transition = "opacity 200ms ease, transform 200ms ease";
    card.style.opacity = "0";
    card.style.transform = "scale(0.95)";

    var nextSibling = card.nextElementSibling;
    var cardHtml = card.outerHTML;

    fetch("/watchlist/remove/" + encodeURIComponent(tconst), {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken, Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Remove failed with status " + response.status);
        }

        setTimeout(function () {
          card.remove();
          lastRemoved = { tconst: tconst, html: cardHtml, nextSibling: nextSibling };

          showToast("Removed from watchlist", function () {
            var tmp = document.createElement("div");
            tmp.innerHTML = lastRemoved.html;
            var restored = tmp.firstElementChild;
            restored.style.opacity = "1";
            restored.style.transform = "";
            if (lastRemoved.nextSibling) {
              grid.insertBefore(restored, lastRemoved.nextSibling);
            } else {
              grid.appendChild(restored);
            }
            fetch("/watchlist/add/" + encodeURIComponent(lastRemoved.tconst), {
              method: "POST",
              headers: { "X-CSRFToken": csrfToken, Accept: "application/json" },
              credentials: "same-origin",
            }).catch(function (err) {
              console.error("Failed to undo remove:", err);
            });
            lastRemoved = null;
          });
        }, 200);
      })
      .catch(function (err) {
        console.error("Failed to remove:", err);
        card.style.opacity = "1";
        card.style.transform = "";
        showToast("Couldn't remove. Try again.");
      });
  });

  // ── Letterboxd import trigger ──
  if (letterboxdTrigger && letterboxdFile && letterboxdForm) {
    letterboxdTrigger.addEventListener("click", function (e) {
      e.preventDefault();
      letterboxdFile.click();
    });
    letterboxdFile.addEventListener("change", function () {
      if (letterboxdFile.files.length > 0) {
        letterboxdForm.submit();
      }
    });
  }

  // ── Enrichment progress card sync ──
  window.addEventListener("nextreel:watched-cards-added", function () {
    // Cards added by enrichment polling — no action needed
  });
})();
