(function () {
  var form = document.querySelector("[data-watchlist-toggle-form]");
  if (!form) return;

  var button = form.querySelector("[data-watchlist-toggle-button]");
  var csrfInput = form.querySelector('input[name="csrf_token"]');
  var status = document.getElementById("movie-status");
  var addUrl = form.dataset.addUrl;
  var removeUrl = form.dataset.removeUrl;

  var savedMarkup =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg> Saved';
  var unsavedMarkup =
    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg> ' +
    '<span><span class="nav-btn-watchlist__prefix">Add to </span>Watchlist</span>';

  function setWatchlistState(isInWatchlist) {
    form.dataset.watchlistState = isInWatchlist ? "saved" : "unsaved";
    form.action = isInWatchlist ? removeUrl : addUrl;
    button.innerHTML = isInWatchlist ? savedMarkup : unsavedMarkup;
    button.setAttribute("aria-pressed", isInWatchlist ? "true" : "false");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!button || button.disabled) return;

    var isInWatchlist = form.dataset.watchlistState === "saved";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    if (status) {
      status.textContent = isInWatchlist
        ? "Removing from watchlist..."
        : "Saving to watchlist...";
    }

    fetch(form.action, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-CSRFToken": csrfInput ? csrfInput.value : "",
      },
      credentials: "same-origin",
    }).then(function (response) {
      return response.json().catch(function () {
        return null;
      }).then(function (payload) {
        if (!response.ok || !payload || !payload.ok) {
          throw new Error("watchlist toggle failed");
        }
        return payload;
      });
    }).then(function (payload) {
      setWatchlistState(Boolean(payload.is_in_watchlist));
      if (status) {
        status.textContent = payload.is_in_watchlist
          ? "Added to watchlist."
          : "Removed from watchlist.";
      }
    }).catch(function (error) {
      console.error("Failed to update watchlist state:", error);
      if (status) {
        status.textContent = "Could not update watchlist status.";
      }
    }).finally(function () {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
  });
})();
