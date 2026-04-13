(function () {
  var grid = document.getElementById("watched-grid");
  if (!grid) return;

  var searchInput = document.getElementById("watched-search");
  var sortSelect = document.getElementById("watched-sort");
  var countEl = document.getElementById("watched-count");
  var toastEl = document.getElementById("watched-toast");
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") || "" : "";
  var allCards = Array.from(grid.querySelectorAll(".watched-card"));
  var total = allCards.length;
  var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var searchTimer = null;
  var toastTimer = null;

  function syncCards() {
    allCards = Array.from(grid.querySelectorAll(".watched-card"));
    total = allCards.length;
  }

  function updateCount(visible) {
    if (!countEl) return;
    countEl.textContent = visible + " of " + total;
  }

  function currentSorter() {
    var value = sortSelect ? sortSelect.value : "recent";
    return function (a, b) {
      switch (value) {
        case "oldest":
          return (a.dataset.watched || "").localeCompare(b.dataset.watched || "");
        case "year_desc":
          return (parseInt(b.dataset.year, 10) || 0) - (parseInt(a.dataset.year, 10) || 0);
        case "year_asc":
          return (parseInt(a.dataset.year, 10) || 9999) - (parseInt(b.dataset.year, 10) || 9999);
        case "rating_desc":
          return (parseFloat(b.dataset.rating) || 0) - (parseFloat(a.dataset.rating) || 0);
        case "title_asc":
          return (a.dataset.title || "").localeCompare(b.dataset.title || "");
        case "recent":
        default:
          return (b.dataset.watched || "").localeCompare(a.dataset.watched || "");
      }
    };
  }

  function applyFilter() {
    var query = searchInput ? (searchInput.value || "").trim().toLowerCase() : "";
    var visible = 0;
    allCards.forEach(function (card) {
      var match = !query || (card.dataset.search || "").indexOf(query) !== -1;
      card.style.display = match ? "" : "none";
      if (match) visible += 1;
    });
    updateCount(visible);
  }

  function applySort() {
    if (!reducedMotion) {
      grid.style.opacity = "0.4";
    }
    allCards.slice().sort(currentSorter()).forEach(function (card) {
      grid.appendChild(card);
    });
    if (!reducedMotion) {
      setTimeout(function () {
        grid.style.opacity = "1";
      }, 150);
    }
  }

  function showToast(message) {
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.hidden = true;
    }, 4000);
  }

  function resetConfirmingButton(button) {
    button.dataset.confirming = "0";
    button.classList.remove("is-confirming");
  }

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(applyFilter, 100);
    });
  }

  if (sortSelect) {
    sortSelect.addEventListener("change", applySort);
  }

  grid.addEventListener("click", function (event) {
    var button = event.target.closest(".watched-remove");
    if (!button) return;
    event.preventDefault();

    if (button.dataset.confirming !== "1") {
      button.dataset.confirming = "1";
      button.classList.add("is-confirming");
      setTimeout(function () {
        if (button.dataset.confirming === "1") {
          resetConfirmingButton(button);
        }
      }, 3000);
      return;
    }

    var card = button.closest(".watched-card");
    var tconst = button.dataset.tconst;
    if (!card || !tconst) return;

    card.style.transition = "opacity 200ms ease, transform 200ms ease";
    card.style.opacity = "0";
    card.style.transform = "scale(0.95)";

    fetch("/watched/remove/" + encodeURIComponent(tconst), {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken, Accept: "application/json" },
      credentials: "same-origin",
    }).then(function (response) {
      if (!response.ok && response.status !== 303) {
        card.style.opacity = "1";
        card.style.transform = "";
        resetConfirmingButton(button);
        showToast("Couldn't remove. Try again.");
        return;
      }

      setTimeout(function () {
        card.remove();
        syncCards();
        applyFilter();
      }, 200);
    }).catch(function (error) {
      console.error("Failed to remove watched film:", error);
      card.style.opacity = "1";
      card.style.transform = "";
      resetConfirmingButton(button);
      showToast("Couldn't remove. Try again.");
    });
  });

  window.addEventListener("nextreel:watched-cards-added", function () {
    syncCards();
    applySort();
    applyFilter();
  });
})();
