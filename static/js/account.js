/**
 * Account settings page — interactive behaviors.
 *
 * Toggle switches, inline confirmations, save feedback,
 * import drag-and-drop, delete confirmation gate.
 */
(function () {
  "use strict";

  // ── Toggle switches ───────────────────────────────────────────────
  document.querySelectorAll(".settings-toggle-row").forEach(function (row) {
    var toggle = row.querySelector(".settings-toggle");
    if (!toggle) return;
    row.addEventListener("click", function (e) {
      if (e.target.closest("a, button:not(.settings-toggle)")) return;
      var checked = toggle.getAttribute("aria-checked") === "true";
      toggle.setAttribute("aria-checked", String(!checked));
      var hidden = row.querySelector('input[type="hidden"]');
      if (hidden) hidden.value = checked ? "" : "on";
    });
    toggle.addEventListener("keydown", function (e) {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        row.click();
      }
    });
  });

  // ── Inline save feedback ─────────────────────────────────────────
  document.querySelectorAll("form[data-settings-form]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector(".settings-btn-primary");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Saving\u2026";
      }
    });
  });

  // ── Blur validation ──────────────────────────────────────────────
  document.querySelectorAll(".settings-input[data-validate]").forEach(function (input) {
    input.addEventListener("blur", function () {
      var rule = input.dataset.validate;
      var errorEl = input.parentElement.querySelector(".settings-field-error");
      var valid = true;

      if (rule === "email") {
        valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.value.trim());
      } else if (rule === "min8") {
        valid = input.value.length === 0 || input.value.length >= 8;
      } else if (rule === "match") {
        var target = document.getElementById(input.dataset.matchTarget);
        valid = !input.value || !target || input.value === target.value;
      }

      input.classList.toggle("invalid", !valid);
      if (errorEl) errorEl.style.display = valid ? "none" : "block";
    });
  });

  // ── Session revoke inline confirm ────────────────────────────────
  document.querySelectorAll("[data-revoke-btn]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      btn.style.display = "none";
      var confirm = btn.nextElementSibling;
      if (confirm) confirm.style.display = "inline-flex";
    });
  });
  document.querySelectorAll("[data-revoke-cancel]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var wrap = btn.closest(".settings-revoke-confirm");
      if (wrap) {
        wrap.style.display = "none";
        var revokeBtn = wrap.previousElementSibling;
        if (revokeBtn) revokeBtn.style.display = "";
      }
    });
  });

  // ── Delete account inline confirmation ───────────────────────────
  var deleteBtn = document.getElementById("delete-account-btn");
  var deleteConfirm = document.getElementById("delete-account-confirm");
  var deleteInput = document.getElementById("delete-confirm-input");
  var deleteSubmit = document.getElementById("delete-confirm-submit");
  var deleteCancel = document.getElementById("delete-cancel-btn");

  if (deleteBtn && deleteConfirm) {
    deleteBtn.addEventListener("click", function () {
      deleteBtn.style.display = "none";
      deleteConfirm.classList.add("visible");
      if (deleteInput) deleteInput.focus();
    });
    if (deleteCancel) {
      deleteCancel.addEventListener("click", function () {
        deleteConfirm.classList.remove("visible");
        deleteBtn.style.display = "";
        if (deleteInput) deleteInput.value = "";
        if (deleteSubmit) deleteSubmit.disabled = true;
      });
    }
    if (deleteInput && deleteSubmit) {
      deleteInput.addEventListener("input", function () {
        deleteSubmit.disabled = deleteInput.value.trim().toLowerCase() !== "delete";
      });
    }
  }

  // ── Watched clear inline confirmation ────────────────────────────
  var clearBtn = document.getElementById("clear-watched-btn");
  var clearConfirm = document.getElementById("clear-watched-confirm");
  var clearCancel = document.getElementById("clear-watched-cancel");

  if (clearBtn && clearConfirm) {
    clearBtn.addEventListener("click", function () {
      clearBtn.style.display = "none";
      clearConfirm.classList.add("visible");
    });
    if (clearCancel) {
      clearCancel.addEventListener("click", function () {
        clearConfirm.classList.remove("visible");
        clearBtn.style.display = "";
      });
    }
  }

  // ── Letterboxd import drag-and-drop ──────────────────────────────
  var importArea = document.getElementById("letterboxd-import-area");
  var importInput = document.getElementById("letterboxd-csv-input");
  var importForm = document.getElementById("letterboxd-import-form");

  if (importArea && importInput) {
    importArea.addEventListener("click", function () {
      importInput.click();
    });
    importArea.addEventListener("dragover", function (e) {
      e.preventDefault();
      importArea.classList.add("drag-over");
    });
    importArea.addEventListener("dragleave", function () {
      importArea.classList.remove("drag-over");
    });
    importArea.addEventListener("drop", function (e) {
      e.preventDefault();
      importArea.classList.remove("drag-over");
      if (e.dataTransfer.files.length > 0) {
        importInput.files = e.dataTransfer.files;
        if (importForm) importForm.submit();
      }
    });
    importInput.addEventListener("change", function () {
      if (importInput.files.length > 0 && importForm) {
        importForm.submit();
      }
    });
  }

  // ── Flash feedback from redirects ────────────────────────────────
  var params = new URLSearchParams(window.location.search);
  var msg = params.get("msg");
  var msgType = params.get("msg_type") || "success";
  if (msg) {
    var feedbackTarget = document.querySelector("[data-feedback-section='" + params.get("section") + "']");
    if (feedbackTarget) {
      var fb = feedbackTarget.querySelector(".settings-feedback");
      if (fb) {
        fb.textContent = decodeURIComponent(msg);
        fb.className = "settings-feedback visible " + msgType;
        if (msgType === "success") {
          setTimeout(function () { fb.classList.remove("visible"); }, 4000);
        }
      }
    }
    // Clean URL
    window.history.replaceState({}, "", window.location.pathname);
  }
})();
