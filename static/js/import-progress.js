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

  var pollInterval = 2000;
  var staleThresholdMs = 30000;
  var startedAt = Date.now();
  var lastProgress = 0;
  var lastProgressAt = Date.now();
  var polling = true;

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

  function updateProgressBar(processed, total) {
    if (!progressBarEl || !progressFillEl) return;
    if (!total || total <= 0) {
      progressBarEl.hidden = true;
      return;
    }
    progressBarEl.hidden = false;
    var pct = Math.max(0, Math.min(100, Math.round((processed / total) * 100)));
    progressFillEl.style.width = pct + "%";
    progressBarEl.setAttribute("aria-valuenow", String(pct));
  }

  function applyData(d) {
    if (statusEl) statusEl.textContent = d.status;
    if (processedEl) processedEl.textContent = d.processed;
    if (totalEl) totalEl.textContent = d.total_rows || "?";
    if (matchedEl) matchedEl.textContent = d.matched;
    if (skippedEl) skippedEl.textContent = d.skipped;
    if (failedEl) failedEl.textContent = d.failed;

    setStatusBadge(d.status);
    updateProgressBar(d.processed || 0, d.total_rows || 0);

    if (typeof d.processed === "number" && d.processed !== lastProgress) {
      lastProgress = d.processed;
      lastProgressAt = Date.now();
      hideStale();
    }

    if (d.status === "completed") {
      hideStale();
      showSuccess();
      polling = false;
      return;
    }

    if (d.status === "failed") {
      hideStale();
      showError(d.error_message);
      polling = false;
      return;
    }

    var idleMs = Date.now() - lastProgressAt;
    if (idleMs > staleThresholdMs && d.status === "pending") {
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
          return null;
        }
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
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
    polling = false;
  } else if (initialStatus === "failed") {
    var serverError = root.dataset.errorMessage || "";
    showError(serverError);
    polling = false;
  }

  if (polling) tick();
})();
