# Account Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the tab-based account settings pages with a single scrollable page using the editorial design language (Merriweather 300, bottom-border inputs, staggered fade-in, no cards).

**Architecture:** The current 6 child templates (`profile.html`, `security.html`, `preferences.html`, `data.html`, `danger.html`, `import_progress.html`) and `layout.html` are replaced by a single `account.html` template. The route handler is simplified to render one template with all section data. CSS is updated to remove old account classes and add new editorial-style settings classes. Backend route changes are minimal — the GET handler changes, POST handlers stay the same but redirect to `/account` instead of `/account?tab=X`.

**Tech Stack:** Jinja2 templates, CSS (in `static/css/input.css`), vanilla JS, existing design tokens from `static/css/tokens.css`

**Spec:** `docs/superpowers/specs/2026-04-15-account-page-redesign.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `templates/account.html` | Single scrollable account page (replaces layout.html + 5 child templates) |
| Create | `static/js/account.js` | Toggle switches, inline confirmations, save feedback, import drag-drop, delete gate, fade-in |
| Modify | `static/css/tokens.css` | Add `--color-success` token |
| Modify | `static/css/input.css` | Remove old `.account-*` card/tab classes, add new `.settings-*` editorial classes |
| Modify | `nextreel/web/routes/account.py` | Simplify GET handler to render single template; update POST redirects |
| Modify | `templates/account/import_progress.html` | Keep as standalone page (import progress is a separate page flow) |
| Delete | `templates/account/layout.html` | Replaced by account.html |
| Delete | `templates/account/profile.html` | Merged into account.html |
| Delete | `templates/account/security.html` | Merged into account.html |
| Delete | `templates/account/preferences.html` | Merged into account.html |
| Delete | `templates/account/data.html` | Merged into account.html |
| Delete | `templates/account/danger.html` | Merged into account.html |
| Create | `tests/web/test_account_routes.py` | Route tests for the redesigned account page |

---

### Task 1: Add success color token

**Files:**
- Modify: `static/css/tokens.css`

- [ ] **Step 1: Add `--color-success` to light mode tokens**

In `static/css/tokens.css`, add after the `--color-accent` line (line 11) in the `:root` block:

```css
  --color-success: #16a34a;
```

- [ ] **Step 2: Add `--color-success` to dark mode (prefers-color-scheme)**

In the `@media (prefers-color-scheme: dark)` block (after line 46), add:

```css
    --color-success: #22c55e;
```

- [ ] **Step 3: Add `--color-success` to explicit `[data-theme="light"]`**

In the `[data-theme="light"]` block (after line 61), add:

```css
  --color-success: #16a34a;
```

- [ ] **Step 4: Add `--color-success` to explicit `[data-theme="dark"]`**

In the `[data-theme="dark"]` block (after line 76), add:

```css
  --color-success: #22c55e;
```

- [ ] **Step 5: Commit**

```bash
git add static/css/tokens.css
git commit -m "feat(tokens): add --color-success design token for account page feedback"
```

---

### Task 2: Replace account CSS — remove old classes, add new settings classes

**Files:**
- Modify: `static/css/input.css:1618-1793`

- [ ] **Step 1: Remove old account card/tab/modal CSS**

In `static/css/input.css`, replace lines 1618-1793 (from `.account-page` through `.modal-actions`) with the new settings styles below. Keep the `.account-avatar-*` classes (lines 1698-1747) since the navbar avatar dropdown uses them.

Replace lines 1618-1697 (`.account-page` through `.account-field-row` responsive rule) and lines 1749-1793 (`.btn-danger` through `.modal-actions`) with:

```css
  /* ── Account settings page (editorial redesign) ── */
  .settings-page {
    max-width: 640px;
    margin: 0 auto;
    padding: 3rem 2rem 4rem;
  }

  .settings-page-title {
    font-family: var(--font-serif, 'Merriweather', Georgia, serif);
    font-weight: 300;
    font-size: 2.25rem;
    margin: 0 0 0.25rem;
    letter-spacing: -0.01em;
  }

  .settings-user-identity {
    font-size: 0.9rem;
    color: var(--color-text-muted);
    margin: 0 0 3rem;
  }

  /* Sections */
  .settings-section {
    padding-bottom: 2.5rem;
    margin-bottom: 2.5rem;
    border-bottom: 1px solid var(--color-border);
  }
  .settings-section:last-child {
    border-bottom: none;
    margin-bottom: 0;
  }

  .settings-section-heading {
    font-family: var(--font-serif, 'Merriweather', Georgia, serif);
    font-weight: 300;
    font-size: 1.5rem;
    margin: 0 0 1.75rem;
  }

  .settings-description {
    font-size: 0.9rem;
    color: var(--color-text-muted);
    margin: 0 0 1rem;
    line-height: 1.6;
  }

  /* Field groups */
  .settings-field { margin-bottom: 1.5rem; }

  .settings-label {
    font-weight: 500;
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-text-muted);
    margin-bottom: 0.5rem;
    display: block;
  }

  .settings-input {
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--color-border);
    color: var(--color-text);
    font-family: var(--font-sans);
    font-size: 1rem;
    padding: 0.5rem 0;
    width: 100%;
    outline: none;
    transition: border-color var(--duration-normal) var(--easing-default);
  }
  .settings-input:focus {
    border-bottom: 2px solid var(--color-accent);
  }
  .settings-input:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
    border-radius: 2px;
  }
  .settings-input::placeholder {
    color: var(--color-text-muted);
    opacity: 0.6;
  }
  .settings-input.invalid {
    border-bottom-color: #dc2626;
  }

  .settings-field-error {
    font-size: 0.8rem;
    color: #dc2626;
    margin-top: 0.35rem;
  }
  [data-theme="dark"] .settings-field-error,
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) .settings-field-error { color: #ef4444; }
  }

  .settings-field-readonly {
    font-size: 0.95rem;
    color: var(--color-text);
    padding: 0.5rem 0;
  }

  .settings-field-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.5rem;
  }

  /* Toggle rows */
  .settings-toggle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 0.5rem;
    margin: 0 -0.5rem;
    border-radius: var(--radius-sharp);
    transition: background-color var(--duration-fast) var(--easing-default);
    cursor: pointer;
  }
  .settings-toggle-row:hover {
    background: color-mix(in srgb, var(--color-text) 4%, transparent);
  }

  .settings-toggle-label { font-size: 0.95rem; }
  .settings-toggle-desc {
    font-size: 0.8rem;
    color: var(--color-text-muted);
    margin-top: 0.15rem;
  }

  .settings-toggle {
    width: 44px;
    height: 24px;
    background: var(--color-border);
    border-radius: 12px;
    border: none;
    position: relative;
    flex-shrink: 0;
    margin-left: 1.5rem;
    cursor: pointer;
    transition: background var(--duration-normal) var(--easing-default);
    padding: 0;
  }
  .settings-toggle[aria-checked="true"] { background: var(--color-accent); }
  .settings-toggle::after {
    content: '';
    position: absolute;
    top: 2px; left: 2px;
    width: 20px; height: 20px;
    background: white;
    border-radius: 50%;
    transition: transform var(--duration-normal) var(--easing-default);
    box-shadow: 0 1px 3px rgba(0,0,0,0.15);
  }
  .settings-toggle[aria-checked="true"]::after { transform: translateX(20px); }
  .settings-toggle:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }

  /* Session rows */
  .settings-session-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 0.5rem;
    margin: 0 -0.5rem;
    border-bottom: 1px solid var(--color-border);
    border-radius: var(--radius-sharp);
    transition: background-color var(--duration-fast) var(--easing-default);
  }
  .settings-session-row:hover {
    background: color-mix(in srgb, var(--color-text) 4%, transparent);
  }
  .settings-session-row:last-child { border-bottom: none; }

  .settings-session-device { font-size: 0.95rem; }
  .settings-session-badge {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-accent);
    font-weight: 600;
    margin-left: 0.5rem;
  }
  .settings-session-time {
    font-size: 0.8rem;
    color: var(--color-text-muted);
    margin-top: 0.15rem;
  }
  .settings-session-note {
    font-size: 0.85rem;
    color: var(--color-text-muted);
    padding: 0.5rem 0;
  }

  /* Buttons */
  .settings-btn-primary {
    background: var(--color-accent);
    color: white;
    border: none;
    font-family: var(--font-sans);
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 0.7rem 1.75rem;
    border-radius: var(--radius-sharp);
    cursor: pointer;
    transition: background var(--duration-normal) var(--easing-default);
  }
  .settings-btn-primary:hover { filter: brightness(0.9); }
  .settings-btn-primary:active { transform: scale(0.98); }
  .settings-btn-primary:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }

  .settings-btn-secondary {
    background: transparent;
    color: var(--color-text-muted);
    border: 1px solid var(--color-border);
    font-family: var(--font-sans);
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 0.65rem 1.5rem;
    border-radius: var(--radius-sharp);
    cursor: pointer;
    transition: all var(--duration-normal) var(--easing-default);
  }
  .settings-btn-secondary:hover {
    border-color: var(--color-text-muted);
    color: var(--color-text);
  }
  .settings-btn-secondary:active { transform: scale(0.98); }
  .settings-btn-secondary:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }

  .settings-btn-danger {
    background: transparent;
    color: #dc2626;
    border: 1px solid #dc2626;
    font-family: var(--font-sans);
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 0.65rem 1.5rem;
    border-radius: var(--radius-sharp);
    cursor: pointer;
    transition: all var(--duration-normal) var(--easing-default);
  }
  .settings-btn-danger:hover {
    background: #dc2626;
    color: white;
  }
  .settings-btn-danger:active { transform: scale(0.98); }
  .settings-btn-danger:focus-visible {
    outline: 2px solid #dc2626;
    outline-offset: 2px;
  }
  .settings-btn-danger:disabled {
    opacity: 0.4;
    cursor: not-allowed;
    pointer-events: none;
  }

  .settings-btn-text {
    background: none;
    border: none;
    color: var(--color-accent);
    font-family: var(--font-sans);
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    cursor: pointer;
    padding: 0.25rem 0.5rem;
    border-radius: var(--radius-sharp);
    transition: opacity var(--duration-normal) var(--easing-default);
  }
  .settings-btn-text:hover { opacity: 0.75; }
  .settings-btn-text:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }

  .settings-actions { margin-top: 2rem; }

  /* Inline feedback */
  .settings-feedback {
    font-size: 0.8rem;
    margin-top: 0.75rem;
    transition: opacity 300ms var(--easing-default);
    opacity: 0;
  }
  .settings-feedback.visible { opacity: 1; }
  .settings-feedback.success { color: var(--color-success); }
  .settings-feedback.error { color: #dc2626; }

  /* Import drop area */
  .settings-import-area {
    border: 2px dashed var(--color-border);
    border-radius: var(--radius-sharp);
    padding: 2rem 1.5rem;
    text-align: center;
    color: var(--color-text-muted);
    font-size: 0.9rem;
    cursor: pointer;
    transition: border-color var(--duration-normal) var(--easing-default),
                background-color var(--duration-normal) var(--easing-default);
  }
  .settings-import-area:hover { border-color: var(--color-accent); }
  .settings-import-area.drag-over {
    border-color: var(--color-accent);
    background: color-mix(in srgb, var(--color-accent) 6%, transparent);
  }
  .settings-import-area strong {
    color: var(--color-accent);
    font-weight: 500;
  }

  /* Import result */
  .settings-import-result {
    font-size: 0.85rem;
    padding: 0.75rem 0;
    line-height: 1.6;
  }
  .settings-import-result .count { font-weight: 600; }
  .settings-import-result .unmatched-list {
    margin-top: 0.5rem;
    padding-left: 1.25rem;
    color: var(--color-text-muted);
  }

  /* Inline confirmation (delete account) */
  .settings-confirm {
    margin-top: 1rem;
    padding: 1.25rem;
    border: 1px solid #dc2626;
    border-radius: var(--radius-sharp);
    display: none;
  }
  .settings-confirm.visible { display: block; }
  .settings-confirm p {
    font-size: 0.9rem;
    margin: 0 0 0.75rem;
  }
  .settings-confirm .settings-input {
    margin-bottom: 1rem;
    border-bottom-color: #dc2626;
  }
  .settings-confirm-actions {
    display: flex;
    gap: 0.75rem;
    align-items: center;
  }
  .settings-confirm-cancel {
    background: none;
    border: none;
    color: var(--color-text-muted);
    font-size: 0.85rem;
    cursor: pointer;
    padding: 0.5rem;
  }
  .settings-confirm-cancel:hover { color: var(--color-text); }

  /* Revoke inline confirm */
  .settings-revoke-confirm {
    display: inline-flex;
    gap: 0.5rem;
    align-items: center;
  }

  /* OAuth provider note */
  .settings-oauth-note {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.9rem;
    color: var(--color-text-muted);
    padding: 0.5rem 0;
  }

  /* Fade-in animation */
  @keyframes settingsFadeUp {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .settings-fade {
    opacity: 0;
    animation: settingsFadeUp 400ms var(--easing-default) forwards;
  }
  .settings-fade:nth-child(1) { animation-delay: 0ms; }
  .settings-fade:nth-child(2) { animation-delay: 80ms; }
  .settings-fade:nth-child(3) { animation-delay: 160ms; }
  .settings-fade:nth-child(4) { animation-delay: 240ms; }
  .settings-fade:nth-child(5) { animation-delay: 320ms; }
  .settings-fade:nth-child(6) { animation-delay: 400ms; }
  .settings-fade:nth-child(7) { animation-delay: 480ms; }

  /* Mobile stacking */
  @media (max-width: 480px) {
    .settings-page { padding: 2rem 1.25rem 3rem; }
    .settings-field-row {
      grid-template-columns: 1fr;
      gap: 1rem;
    }
    .settings-toggle-row { flex-wrap: wrap; }
    .settings-toggle { margin-left: 0; margin-top: 0.5rem; }
  }

  /* ── Danger button variant (kept for import_progress.html) ── */
  .btn-danger {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    padding: 0.625rem 1.125rem;
    font-weight: 600;
    border-radius: 0.5rem;
    background: #dc2626;
    color: white;
    border: 0;
    cursor: pointer;
  }
  .btn-danger:hover { background: #b91c1c; }
  .btn-danger:disabled { opacity: 0.55; cursor: not-allowed; }

  /* ── Modal primitive (kept for import_progress.html compat) ── */
  .modal-backdrop {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.55);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 50;
    padding: 1rem;
  }
  .modal-backdrop.open { display: flex; }
  .modal-panel {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    border-radius: 0.75rem;
    padding: 1.5rem;
    width: 100%;
    max-width: 28rem;
    box-shadow: 0 30px 60px rgba(0,0,0,0.35);
  }
  .modal-panel h3 { margin: 0 0 0.5rem; font-size: 1.125rem; font-weight: 600; }
  .modal-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1.25rem; }
```

Note: Keep the `.account-avatar-*` classes (lines 1698-1747) untouched — the navbar dropdown uses them.

- [ ] **Step 2: Rebuild CSS**

```bash
npm run build-css
```

Expected: `static/css/output.css` is regenerated without errors.

- [ ] **Step 3: Commit**

```bash
git add static/css/input.css
git commit -m "feat(css): replace account card/tab styles with editorial settings styles"
```

---

### Task 3: Create the account page JavaScript module

**Files:**
- Create: `static/js/account.js`

- [ ] **Step 1: Create `static/js/account.js`**

```javascript
/**
 * Account settings page — interactive behaviors.
 *
 * Toggle switches, inline confirmations, save feedback,
 * import drag-and-drop, delete confirmation gate.
 */
(function () {
  "use strict";

  // ── Toggle switches ──────────────────────────────────────────────
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
```

- [ ] **Step 2: Commit**

```bash
git add static/js/account.js
git commit -m "feat: add account.js for toggle, confirm, validation, and drag-drop behaviors"
```

---

### Task 4: Create the single account template

**Files:**
- Create: `templates/account.html`

- [ ] **Step 1: Create `templates/account.html`**

```html
{% from "macros.html" import user_avatar with context %}
<!DOCTYPE html>
<html lang="en" class="scroll-smooth" {% if server_theme %}data-theme-server="{{ server_theme }}"{% endif %}>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Account – Nextreel</title>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Merriweather:ital,wght@0,300;0,400;0,700;1,400&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/output.css') }}?v={{ config.get('CSS_VERSION', '1') }}">
  <script src="{{ url_for('static', filename='js/theme-boot.js') }}?v={{ config.get('CSS_VERSION', '1') }}"></script>
  <style>body { font-family: var(--font-sans, 'DM Sans', system-ui, sans-serif); background: var(--color-bg); color: var(--color-text); }</style>
</head>
<body class="antialiased">
  <a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow">Skip to content</a>
  {% include 'navbar_modern.html' %}

  <main id="main" class="settings-page">
    {# ── Page Header ── #}
    <div class="settings-fade">
      <h1 class="settings-page-title">Account</h1>
      <p class="settings-user-identity">{{ user.email }}</p>
    </div>

    {# ── Profile ── #}
    <section class="settings-section settings-fade" data-feedback-section="profile">
      <h2 class="settings-section-heading">Profile</h2>
      <form method="POST" action="{{ url_for('main.account_profile_save') }}" data-settings-form>
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="settings-field">
          <label for="display_name" class="settings-label">Display Name</label>
          <input id="display_name" name="display_name" type="text" maxlength="100"
                 value="{{ user.display_name or '' }}" class="settings-input"
                 placeholder="How you'd like to be called">
        </div>
        <div class="settings-field">
          <label class="settings-label">Email</label>
          <p class="settings-field-readonly">{{ user.email }}</p>
        </div>
        <div class="settings-actions">
          <button type="submit" class="settings-btn-primary">Save Profile</button>
          <div class="settings-feedback" role="status" aria-live="polite"></div>
        </div>
      </form>
    </section>

    {# ── Preferences ── #}
    <section class="settings-section settings-fade" data-feedback-section="preferences">
      <h2 class="settings-section-heading">Preferences</h2>
      <form method="POST" action="{{ url_for('main.account_preferences_save') }}" data-settings-form>
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

        <div class="settings-toggle-row">
          <div>
            <div class="settings-toggle-label">Exclude watched movies</div>
            <div class="settings-toggle-desc">Hide movies you've already seen from recommendations</div>
          </div>
          <input type="hidden" name="exclude_watched_default" value="{{ 'on' if exclude_watched_default else '' }}">
          <button type="button" class="settings-toggle" role="switch"
                  aria-checked="{{ 'true' if exclude_watched_default else 'false' }}"
                  aria-label="Exclude watched movies"></button>
        </div>

        <div class="settings-actions">
          <button type="submit" class="settings-btn-primary">Save Preferences</button>
          <div class="settings-feedback" role="status" aria-live="polite"></div>
        </div>
      </form>
    </section>

    {# ── Security ── #}
    <section class="settings-section settings-fade" data-feedback-section="security">
      <h2 class="settings-section-heading">Security</h2>

      {% if user.auth_provider == 'email' %}
      <form method="POST" action="{{ url_for('main.account_password_change') }}" data-settings-form>
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="settings-field">
          <label for="current_password" class="settings-label">Current Password</label>
          <input id="current_password" name="current_password" type="password" required
                 autocomplete="current-password" class="settings-input">
          {% if errors and errors.current_password %}
            <p class="settings-field-error">{{ errors.current_password }}</p>
          {% endif %}
        </div>
        <div class="settings-field">
          <label class="settings-label">New Password</label>
          <div class="settings-field-row">
            <div>
              <input id="new_password" name="new_password" type="password" required
                     autocomplete="new-password" class="settings-input"
                     placeholder="New password" data-validate="min8">
              {% if errors and errors.new_password %}
                <p class="settings-field-error">{{ errors.new_password }}</p>
              {% endif %}
            </div>
            <div>
              <input id="confirm_password" name="confirm_password" type="password" required
                     autocomplete="new-password" class="settings-input"
                     placeholder="Confirm password" data-validate="match" data-match-target="new_password">
              {% if errors and errors.confirm_password %}
                <p class="settings-field-error">{{ errors.confirm_password }}</p>
              {% endif %}
            </div>
          </div>
        </div>
        <div class="settings-actions" style="margin-bottom: 2.5rem;">
          <button type="submit" class="settings-btn-primary">Update Password</button>
          <div class="settings-feedback" role="status" aria-live="polite"></div>
        </div>
      </form>
      {% else %}
      <div class="settings-oauth-note" style="margin-bottom: 2.5rem;">
        <span>Signed in with {{ user.auth_provider | capitalize }}</span>
      </div>
      {% endif %}

      <div class="settings-label" style="margin-bottom: 1rem;">Active Sessions</div>
      <div class="settings-session-row">
        <div>
          <div class="settings-session-device">
            Current session
            <span class="settings-session-badge">Current</span>
          </div>
        </div>
      </div>
      <div style="margin-top: 1rem;">
        <form method="POST" action="{{ url_for('main.account_sessions_revoke') }}" style="display:inline;">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <button type="button" class="settings-btn-text" data-revoke-btn>Sign out other devices</button>
          <span class="settings-revoke-confirm" style="display:none;">
            <button type="submit" class="settings-btn-text" style="color:#dc2626;">Confirm</button>
            <button type="button" class="settings-confirm-cancel" data-revoke-cancel>Cancel</button>
          </span>
        </form>
      </div>
    </section>

    {# ── Data ── #}
    <section class="settings-section settings-fade" data-feedback-section="data">
      <h2 class="settings-section-heading">Data</h2>

      <div class="settings-field">
        <div class="settings-label">Export</div>
        <p class="settings-description">Download all your data including watched list, preferences, and ratings.</p>
        <a href="{{ url_for('main.account_export_watched_csv') }}" class="settings-btn-secondary" style="display:inline-block;">Export My Data</a>
      </div>

      <div class="settings-field" style="margin-top: 2rem;">
        <div class="settings-label">Import from Letterboxd</div>
        <form id="letterboxd-import-form" method="POST"
              action="{{ url_for('main.account_letterboxd_upload') }}"
              enctype="multipart/form-data">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <input type="file" id="letterboxd-csv-input" name="csv" accept=".csv"
                 style="display:none;">
          <div id="letterboxd-import-area" class="settings-import-area">
            Drop your Letterboxd CSV here or <strong>browse</strong>
          </div>
        </form>
      </div>

      <div class="settings-field" style="margin-top: 2rem;">
        <div class="settings-label">Clear Watched History</div>
        <p class="settings-description">Remove every movie from your watched list. This cannot be undone.</p>
        <button type="button" id="clear-watched-btn" class="settings-btn-danger">Clear All Watched</button>
        <div id="clear-watched-confirm" class="settings-confirm">
          <p>Are you sure? This will permanently delete all watched movies from your list.</p>
          <form method="POST" action="{{ url_for('main.account_watched_clear') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <div class="settings-confirm-actions">
              <button type="submit" class="settings-btn-danger">Clear All</button>
              <button type="button" class="settings-confirm-cancel" id="clear-watched-cancel">Cancel</button>
            </div>
          </form>
        </div>
      </div>
    </section>

    {# ── Danger Zone ── #}
    <section class="settings-section settings-fade">
      <h2 class="settings-section-heading">Danger Zone</h2>
      <p class="settings-description">Permanently delete your account and all associated data. This action cannot be undone.</p>
      <button type="button" id="delete-account-btn" class="settings-btn-danger">Delete Account</button>
      <div id="delete-account-confirm" class="settings-confirm">
        <p>Type <strong>delete</strong> to confirm permanent account deletion.</p>
        <form method="POST" action="{{ url_for('main.account_delete') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <input id="delete-confirm-input" name="confirm_delete" type="text" class="settings-input"
                 placeholder="Type 'delete' to confirm" autocomplete="off"
                 aria-label="Type delete to confirm">
          <div class="settings-confirm-actions">
            <button id="delete-confirm-submit" type="submit" class="settings-btn-danger" disabled>Delete Forever</button>
            <button type="button" class="settings-confirm-cancel" id="delete-cancel-btn">Cancel</button>
          </div>
        </form>
      </div>
    </section>
  </main>

  {% include 'footer_modern.html' %}
  <script src="{{ url_for('static', filename='js/account.js') }}?v={{ config.get('CSS_VERSION', '1') }}"></script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add templates/account.html
git commit -m "feat: create single scrollable account page template"
```

---

### Task 5: Update the route handler

**Files:**
- Modify: `nextreel/web/routes/account.py:68-98`

- [ ] **Step 1: Change the `account_view` GET handler**

Replace the current `account_view` function (lines 68-98) with:

```python
@bp.route("/account")
async def account_view():
    if not _current_user_id():
        return redirect(url_for("main.login_page", next="/account"))

    db_pool = _db_pool()
    user_id = _current_user_id()
    user = await get_user_by_id(db_pool, user_id)
    if not user:
        session.clear()
        return redirect(url_for("main.login_page"))

    exclude_watched_default = await user_preferences.get_exclude_watched_default(
        db_pool, user_id
    )
    theme_preference = await user_preferences.get_theme_preference(db_pool, user_id)
    default_filters = await user_preferences.get_default_filters(db_pool, user_id)

    return await render_template(
        "account.html",
        user=user,
        server_theme=theme_preference,
        exclude_watched_default=exclude_watched_default,
        default_filters=default_filters,
        page_title="Account",
    )
```

- [ ] **Step 2: Update POST redirect targets**

Change all `+ "?tab=profile"`, `+ "?tab=security"`, `+ "?tab=preferences"`, `+ "?tab=data"` suffixes to just redirect to `/account`. Update these lines:

Line 120: change `url_for("main.account_view") + "?tab=profile"` → `url_for("main.account_view")`
Line 185: change `url_for("main.account_view") + "?tab=security"` → `url_for("main.account_view")`
Line 201: change `url_for("main.account_view") + "?tab=security"` → `url_for("main.account_view")`
Line 220: change `url_for("main.account_view") + "?tab=preferences"` → `url_for("main.account_view")`
Line 231: change `url_for("main.account_view") + "?tab=preferences"` → `url_for("main.account_view")`
Line 240: change `url_for("main.account_view") + "?tab=preferences"` → `url_for("main.account_view")`
Line 456: change `url_for("main.account_view") + "?tab=data"` → `url_for("main.account_view")`

- [ ] **Step 3: Remove `_VALID_TABS` constant**

Delete line 42: `_VALID_TABS = ("profile", "security", "preferences", "data", "danger")`

- [ ] **Step 4: Update password validation error rendering**

In the `account_password_change` function (around lines 157-169), update the error template rendering to use the new single template:

```python
    if errors:
        user = await get_user_by_id(db_pool, user_id)
        theme = await user_preferences.get_theme_preference(db_pool, user_id)
        exclude_watched = await user_preferences.get_exclude_watched_default(
            db_pool, user_id
        )
        default_filters = await user_preferences.get_default_filters(db_pool, user_id)
        return (
            await render_template(
                "account.html",
                user=user,
                server_theme=theme,
                exclude_watched_default=exclude_watched,
                default_filters=default_filters,
                errors=errors,
                page_title="Account",
            ),
            400,
        )
```

- [ ] **Step 5: Update the delete confirmation to use typed "delete" instead of email**

In the `account_delete` function (lines 462-475), change the confirmation check:

```python
@bp.route("/account/delete", methods=["POST"])
@csrf_required
@rate_limited("account_delete")
async def account_delete():
    user_id = _require_user()
    form = await request.form
    typed = (form.get("confirm_delete") or "").strip().lower()

    if typed != "delete":
        abort(400, description="Please type 'delete' to confirm account deletion.")

    db_pool = _db_pool()
    user = await get_user_by_id(db_pool, user_id)
    if not user:
        abort(400)
```

Note: The form field name changes from `confirm_email` to `confirm_delete`, and the input name in `account.html` (Task 4) already uses `id="delete-confirm-input"`. Add `name="confirm_delete"` to the input in the template.

- [ ] **Step 6: Commit**

```bash
git add nextreel/web/routes/account.py
git commit -m "refactor(routes): simplify account to single-template rendering, use typed 'delete' confirmation"
```

---

### Task 6: Delete old account templates

**Files:**
- Delete: `templates/account/layout.html`
- Delete: `templates/account/profile.html`
- Delete: `templates/account/security.html`
- Delete: `templates/account/preferences.html`
- Delete: `templates/account/data.html`
- Delete: `templates/account/danger.html`

Note: Keep `templates/account/import_progress.html` — it's a separate page that still works.

- [ ] **Step 1: Update import_progress.html to be standalone**

The import progress page currently extends `account/layout.html` which we're deleting. Update it to be a standalone page. Replace the entire file:

```html
<!DOCTYPE html>
<html lang="en" class="scroll-smooth" {% if server_theme %}data-theme-server="{{ server_theme }}"{% endif %}>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Import Progress – Nextreel</title>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Merriweather:ital,wght@0,300;0,400;0,700;1,400&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/output.css') }}?v={{ config.get('CSS_VERSION', '1') }}">
  <script src="{{ url_for('static', filename='js/theme-boot.js') }}?v={{ config.get('CSS_VERSION', '1') }}"></script>
  <style>body { font-family: var(--font-sans, 'DM Sans', system-ui, sans-serif); background: var(--color-bg); color: var(--color-text); }</style>
</head>
<body class="antialiased">
  <a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow">Skip to content</a>
  {% include 'navbar_modern.html' %}

  <main id="main" class="settings-page">
    <div class="settings-fade">
      <h1 class="settings-page-title">Importing from Letterboxd</h1>
    </div>

    <section class="settings-section settings-fade" data-import-id="{{ import_row.import_id }}">
      <p class="settings-description">
        Status: <strong id="import-status">{{ import_row.status }}</strong> —
        <span id="import-processed">{{ import_row.processed }}</span> of
        <span id="import-total">{{ import_row.total_rows or '?' }}</span> rows
      </p>
      <ul style="font-size:0.9rem; color: var(--color-text-muted); list-style: none; padding: 0;">
        <li>Matched: <span id="import-matched">{{ import_row.matched }}</span></li>
        <li>Skipped: <span id="import-skipped">{{ import_row.skipped }}</span></li>
        <li>Failed: <span id="import-failed">{{ import_row.failed }}</span></li>
      </ul>
      <div class="settings-actions">
        <a href="{{ url_for('main.account_view') }}" class="settings-btn-secondary">← Back to Account</a>
      </div>
    </section>
  </main>

  {% include 'footer_modern.html' %}

  <script>
    (function () {
      var root = document.querySelector('[data-import-id]');
      if (!root) return;
      var id = root.dataset.importId;
      var polling = true;
      function tick() {
        if (!polling) return;
        fetch('/account/import/' + id + '/status')
          .then(function (r) { if (!r.ok) { polling = false; return null; } return r.json(); })
          .then(function (d) {
            if (!d) return;
            document.getElementById('import-status').textContent = d.status;
            document.getElementById('import-processed').textContent = d.processed;
            document.getElementById('import-total').textContent = d.total_rows || '?';
            document.getElementById('import-matched').textContent = d.matched;
            document.getElementById('import-skipped').textContent = d.skipped;
            document.getElementById('import-failed').textContent = d.failed;
            if (d.status === 'completed' || d.status === 'failed') { polling = false; return; }
            setTimeout(tick, 2000);
          })
          .catch(function () { setTimeout(tick, 2000); });
      }
      tick();
    })();
  </script>
</body>
</html>
```

- [ ] **Step 2: Delete the old templates**

```bash
rm templates/account/layout.html
rm templates/account/profile.html
rm templates/account/security.html
rm templates/account/preferences.html
rm templates/account/data.html
rm templates/account/danger.html
```

- [ ] **Step 3: Commit**

```bash
git add templates/account/import_progress.html
git add -u templates/account/
git commit -m "refactor: delete old tab-based account templates, make import_progress standalone"
```

---

### Task 7: Rebuild CSS and verify

**Files:**
- None (build step)

- [ ] **Step 1: Rebuild Tailwind CSS**

```bash
npm run build-css
```

Expected: No errors. `static/css/output.css` regenerated.

- [ ] **Step 2: Commit the built CSS**

```bash
git add static/css/output.css
git commit -m "build: regenerate output.css with new settings styles"
```

---

### Task 8: Write route tests

**Files:**
- Create: `tests/web/test_account_routes.py`

- [ ] **Step 1: Create the test file**

```python
"""Tests for the redesigned single-page account routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def _mock_user():
    """Patch get_user_by_id to return a fake user."""
    user = {
        "user_id": "u1",
        "email": "test@example.com",
        "display_name": "Test User",
        "auth_provider": "email",
        "created_at": None,
    }
    with patch(
        "nextreel.web.routes.account.get_user_by_id",
        new_callable=AsyncMock,
        return_value=user,
    ) as mock:
        yield mock


@pytest.fixture
def _mock_prefs():
    """Patch preference helpers to return defaults."""
    with (
        patch(
            "nextreel.web.routes.account.user_preferences.get_exclude_watched_default",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "nextreel.web.routes.account.user_preferences.get_theme_preference",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "nextreel.web.routes.account.user_preferences.get_default_filters",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        yield


async def test_account_redirects_unauthenticated(client):
    resp = await client.get("/account")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


async def test_account_renders_single_page(client, logged_in_session, _mock_user, _mock_prefs):
    resp = await client.get("/account")
    assert resp.status_code == 200
    body = (await resp.get_data(as_text=True))
    assert "settings-page" in body
    assert "Account" in body
    # All sections present in single page
    assert "Profile" in body
    assert "Preferences" in body
    assert "Security" in body
    assert "Data" in body
    assert "Danger Zone" in body


async def test_account_no_tab_param_needed(client, logged_in_session, _mock_user, _mock_prefs):
    """Old ?tab= params should be ignored gracefully."""
    resp = await client.get("/account?tab=security")
    assert resp.status_code == 200


async def test_delete_requires_typed_delete(client, logged_in_session, _mock_user):
    resp = await client.post(
        "/account/delete",
        form={"csrf_token": "test", "confirm_delete": "wrong"},
    )
    assert resp.status_code == 400


async def test_delete_accepts_typed_delete(client, logged_in_session, _mock_user):
    with (
        patch(
            "nextreel.web.routes.account._db_pool",
        ) as mock_pool,
        patch(
            "nextreel.web.routes.account._redis_client",
            return_value=None,
        ),
    ):
        mock_pool.return_value.execute = AsyncMock(return_value=None)
        resp = await client.post(
            "/account/delete",
            form={"csrf_token": "test", "confirm_delete": "delete"},
        )
    assert resp.status_code == 302
    assert "/" in resp.headers["Location"]
```

Note: This test file depends on existing test fixtures (`client`, `logged_in_session`) that should already exist in the project's test infrastructure. If they don't, the executing agent should check `tests/conftest.py` and adapt fixture names.

- [ ] **Step 2: Run the tests**

```bash
python3 -m pytest tests/web/test_account_routes.py -v
```

Expected: Tests pass (or if `client`/`logged_in_session` fixtures aren't available, the agent should adapt the fixture setup).

- [ ] **Step 3: Commit**

```bash
git add tests/web/test_account_routes.py
git commit -m "test: add route tests for redesigned account page"
```

---

### Task 9: Run full test suite and CSS build

**Files:**
- None (verification step)

- [ ] **Step 1: Run full tests**

```bash
python3 -m pytest tests/ -v
```

Expected: All tests pass. No regressions from template changes.

- [ ] **Step 2: Verify CSS build is clean**

```bash
npm run build-css
```

Expected: No errors.

- [ ] **Step 3: Start the dev server and verify page loads**

```bash
python3 app.py
```

Navigate to `http://127.0.0.1:5000/account` (while logged in). Verify:
- Single scrollable page renders (no tabs)
- All 5 sections visible
- Staggered fade-in animation plays
- Toggle switches work
- Delete account inline confirmation appears and "delete" gate works
- Buttons use correct tier styling

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address test/build issues from account page redesign"
```

---

## Verification Checklist

After all tasks complete, verify:

- [ ] `/account` renders the single scrollable page (not the old tab layout)
- [ ] `/account?tab=anything` still works (ignored gracefully)
- [ ] Profile save redirects back to `/account`
- [ ] Password change shows inline errors on validation failure
- [ ] Toggle switches toggle and submit correct values
- [ ] Delete account requires typing "delete" (not email)
- [ ] Letterboxd import drop area works (drag-over highlight, file select)
- [ ] Import progress page (`/account/import/<id>`) still works standalone
- [ ] Export CSV link works
- [ ] Session revoke has inline confirm/cancel
- [ ] Clear watched has inline confirm/cancel
- [ ] Staggered fade-in animation plays on page load
- [ ] Page is responsive at 480px (fields stack, toggles wrap)
- [ ] Focus rings visible on keyboard navigation
- [ ] All tests pass
