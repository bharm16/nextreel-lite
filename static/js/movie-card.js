(function () {
  var el = document.getElementById("genre-info");
  if (!el) return;

  var raw = (el.textContent || "").trim();
  var parts = raw ? raw.split(",").map(function (part) {
    return part.trim();
  }).filter(Boolean) : [];

  el.innerHTML = "";
  el.className = "meta";
  el.style.marginBottom = "0";

  parts.forEach(function (genre, index) {
    var span = document.createElement("span");
    span.textContent = genre;
    el.appendChild(span);

    if (index < parts.length - 1) {
      var dot = document.createElement("span");
      dot.className = "dot";
      dot.innerHTML = "&middot;";
      el.appendChild(dot);
    }
  });
})();

(function () {
  var voteElements = document.querySelectorAll("#vote-number");
  voteElements.forEach(function (voteEl) {
    var num = parseInt(voteEl.textContent.replace(/[^0-9]/g, ""), 10);
    var out = num.toString();

    if (isNaN(num)) return;
    if (num >= 1000000) {
      out = (num / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
    } else if (num >= 1000) {
      out = (num / 1000).toFixed(1).replace(/\.0$/, "") + "K";
    }

    voteEl.textContent = out;
  });
})();

(function () {
  var button = document.getElementById("play-button");
  if (!button) return;

  button.addEventListener("click", function () {
    var url = button.getAttribute("data-video-url");
    if (url) {
      window.open(url, "_blank");
    } else {
      alert("Trailer not available.");
    }
  });
})();

(function () {
  var form = document.querySelector("[data-watched-toggle-form]");
  if (!form) return;

  var button = form.querySelector("[data-watched-toggle-button]");
  var csrfInput = form.querySelector('input[name="csrf_token"]');
  var status = document.getElementById("movie-status");
  var addUrl = form.dataset.addUrl;
  var removeUrl = form.dataset.removeUrl;

  var watchedMarkup =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>' +
    " Watched";
  var unwatchedMarkup =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>' +
    '<circle cx="12" cy="12" r="3"/></svg> ' +
    '<span class="nav-btn-watched__prefix">Mark as </span>Watched';

  function setWatchedState(isWatched) {
    form.dataset.watchedState = isWatched ? "watched" : "unwatched";
    form.action = isWatched ? removeUrl : addUrl;
    button.innerHTML = isWatched ? watchedMarkup : unwatchedMarkup;
    button.setAttribute("aria-pressed", isWatched ? "true" : "false");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!button || button.disabled) return;

    var isWatched = form.dataset.watchedState === "watched";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    if (status) {
      status.textContent = isWatched ? "Removing from watched..." : "Saving to watched...";
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
          throw new Error("watched toggle failed");
        }
        return payload;
      });
    }).then(function (payload) {
      setWatchedState(Boolean(payload.is_watched));
      if (status) {
        status.textContent = payload.is_watched ? "Marked as watched." : "Removed from watched.";
      }
    }).catch(function (error) {
      console.error("Failed to update watched state:", error);
      if (status) {
        status.textContent = "Could not update watched status.";
      }
    }).finally(function () {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
  });
})();

(function () {
  var status = document.getElementById("movie-status");

  function guardDisabled(form) {
    form.addEventListener("submit", function (event) {
      var button = form.querySelector("button");
      if (button && button.disabled) {
        event.preventDefault();
        return false;
      }

      if (button) {
        button.setAttribute("aria-busy", "true");
      }
      if (status) {
        status.textContent = form.id && form.id.indexOf("prev") !== -1
          ? "Loading previous movie..."
          : "Loading next movie...";
      }
      return true;
    });
  }

  ["#prev-form", "#prev-form-bottom", "#next-form", "#next-form-bottom"].forEach(function (selector) {
    var form = document.querySelector(selector);
    if (form) {
      guardDisabled(form);
    }
  });
})();

(function () {
  // When the page renders a partial projection (background enrichment is
  // still in flight or hasn't been attempted yet), poll the state endpoint
  // and reload as soon as the row flips to a serveable state. The reload
  // path gives us a fully populated render including the hero image preload.
  var state = document.body.dataset.projectionState;
  var publicId = document.body.dataset.publicId || "";
  if (!publicId) return;
  if (state === "ready" || state === "stale") return;

  var attempts = 0;
  var maxAttempts = 12;          // ~18s of polling at most
  var intervalMs = 1500;

  function poll() {
    attempts += 1;
    fetch("/api/projection-state/" + encodeURIComponent(publicId), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (res) { return res.ok ? res.json() : {}; })
      .then(function (data) {
        if (data && (data.state === "ready" || data.state === "stale")) {
          window.location.reload();
          return;
        }
        if (attempts < maxAttempts) {
          setTimeout(poll, intervalMs);
        }
      })
      .catch(function () {
        if (attempts < maxAttempts) {
          setTimeout(poll, intervalMs * 2);
        }
      });
  }

  setTimeout(poll, 700);
})();
