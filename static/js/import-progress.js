(function () {
  var root = document.querySelector("[data-import-id]");
  if (!root) return;

  var importId = root.dataset.importId;
  var statusEl = document.getElementById("import-status");
  var processedEl = document.getElementById("import-processed");
  var totalEl = document.getElementById("import-total");
  var matchedEl = document.getElementById("import-matched");
  var skippedEl = document.getElementById("import-skipped");
  var failedEl = document.getElementById("import-failed");
  var badgeEl = document.getElementById("import-status-badge");
  var errorWrapEl = document.getElementById("import-error");
  var errorTextEl = document.getElementById("import-error-text");
  var staleWrapEl = document.getElementById("import-stale-warning");
  var successWrapEl = document.getElementById("import-success");
  var progressBarEl = document.getElementById("import-progress-bar");
  var progressFillEl = document.getElementById("import-progress-fill");
  var pollHintEl = document.getElementById("import-poll-hint");
  var pollHintTextEl = document.getElementById("import-poll-hint-text");

  var pollInterval = 2000;
  var staleThresholdMs = 12000;
  var startedAt = Date.now();
  var lastProgress = 0;
  var lastProgressAt = Date.now();
  var lastPollAt = Date.now();
  var polling = true;
  var hintTickHandle = null;

  function setStatusBadge(status) {
    if (!badgeEl) return;
    badgeEl.dataset.state = status || "pending";
  }

  function showError(message) {
    if (!errorWrapEl) return;
    errorWrapEl.hidden = false;
    if (errorTextEl) {
      errorTextEl.textContent = message || "The import failed for an unknown reason.";
    }
  }

  function showStale() {
    if (staleWrapEl) staleWrapEl.hidden = false;
  }

  function hideStale() {
    if (staleWrapEl) staleWrapEl.hidden = true;
  }

  function showSuccess() {
    if (successWrapEl) successWrapEl.hidden = false;
  }

  function updateProgressBar(processed, total, terminal) {
    if (!progressBarEl || !progressFillEl) return;
    if (terminal) {
      progressBarEl.hidden = true;
      return;
    }
    progressBarEl.hidden = false;
    if (!total || total <= 0) {
      progressBarEl.classList.add("import-progress-bar--indeterminate");
      progressFillEl.style.width = "";
      progressBarEl.removeAttribute("aria-valuenow");
      return;
    }
    progressBarEl.classList.remove("import-progress-bar--indeterminate");
    var pct = Math.max(0, Math.min(100, Math.round((processed / total) * 100)));
    progressFillEl.style.width = pct + "%";
    progressBarEl.setAttribute("aria-valuenow", String(pct));
  }

  function formatAgo(seconds) {
    if (seconds < 1) return "just now";
    if (seconds < 2) return "1s ago";
    return Math.floor(seconds) + "s ago";
  }

  function refreshPollHint() {
    if (!pollHintEl || pollHintEl.hidden || !pollHintTextEl) return;
    var ago = (Date.now() - lastPollAt) / 1000;
    pollHintTextEl.textContent = "Checking for updates · last checked " + formatAgo(ago);
  }

  function startHintTick() {
    if (hintTickHandle) return;
    hintTickHandle = setInterval(refreshPollHint, 1000);
  }

  function stopHintTick() {
    if (hintTickHandle) {
      clearInterval(hintTickHandle);
      hintTickHandle = null;
    }
  }

  function hidePollHint() {
    if (pollHintEl) pollHintEl.hidden = true;
    stopHintTick();
  }

  function applyData(d) {
    if (statusEl) statusEl.textContent = d.status;
    if (processedEl) processedEl.textContent = d.processed;
    if (totalEl) totalEl.textContent = d.total_rows || "?";
    if (matchedEl) matchedEl.textContent = d.matched;
    if (skippedEl) skippedEl.textContent = d.skipped;
    if (failedEl) failedEl.textContent = d.failed;

    var terminal = d.status === "completed" || d.status === "failed";
    setStatusBadge(d.status);
    updateProgressBar(d.processed || 0, d.total_rows || 0, terminal);

    if (typeof d.processed === "number" && d.processed !== lastProgress) {
      lastProgress = d.processed;
      lastProgressAt = Date.now();
      hideStale();
    }

    if (d.status === "completed") {
      hideStale();
      hidePollHint();
      showSuccess();
      polling = false;
      return;
    }

    if (d.status === "failed") {
      hideStale();
      hidePollHint();
      showError(d.error_message);
      polling = false;
      return;
    }

    var idleMs = Date.now() - lastProgressAt;
    if (idleMs > staleThresholdMs && (d.status === "pending" || d.status === "running")) {
      showStale();
    }
  }

  function tick() {
    if (!polling) return;
    fetch("/account/import/" + importId + "/status", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) {
          polling = false;
          hidePollHint();
          return null;
        }
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        lastPollAt = Date.now();
        refreshPollHint();
        applyData(d);
        if (polling) setTimeout(tick, pollInterval);
      })
      .catch(function () {
        if (polling) setTimeout(tick, pollInterval);
      });
  }

  // Prime initial state from server-rendered values (covers the case where
  // status is already "failed" or "completed" on first load).
  var initialStatus = statusEl ? statusEl.textContent.trim() : "";
  setStatusBadge(initialStatus);
  if (initialStatus === "completed") {
    showSuccess();
    hidePollHint();
    polling = false;
  } else if (initialStatus === "failed") {
    var serverError = root.dataset.errorMessage || "";
    showError(serverError);
    hidePollHint();
    polling = false;
  }

  if (polling) {
    startHintTick();
    tick();
  }
})();
