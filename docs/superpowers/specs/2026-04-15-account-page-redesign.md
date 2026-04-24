# Account Page Redesign

**Date:** 2026-04-15
**Status:** Approved
**Replaces:** 2026-04-14-account-settings-design.md (original card-based tab layout)

## Goal

Redesign the account settings pages to match the editorial design language established by the recently redesigned login, register, home, movie detail, and watched list pages. The current account pages use heavy serif headings, bordered rounded cards, and a tab navigation model that feels disconnected from the rest of the app.

## Design Decisions Summary

| Dimension | Decision |
|-----------|----------|
| Navigation model | Single scrollable page (no tabs) |
| Wayfinding | No explicit nav; rely on clear headings and spacing. Add jump links only if content grows |
| Section separation | Thin horizontal dividers (`1px solid var(--color-border)`) between sections |
| Page header | "Account" title + user email, personalized |
| Input style | Bottom-border only (auth style), accent color on focus |
| Button hierarchy | 3 tiers: primary (solid accent), secondary (outlined), danger (outlined red, fills on hover) |
| Toggle controls | iOS-style toggle switches (matching filter drawer), per-section save buttons |
| Sections | Profile, Preferences, Security, Data, Danger Zone (5 sections, all kept) |
| Session display | Stacked rows with dividers, "(Current)" badge, "Revoke" text buttons |
| Confirmations | Inline (no modals). Delete account requires typing "delete" |
| Content width | 640px max-width, centered |
| Mobile | Stack fields vertically below 480px |
| Interaction states | Hover highlights on rows/buttons, focus rings on all interactive elements, drag-over on import area, danger button fills red on hover |
| Feedback | Inline messages below save buttons (success fades after 3-4s, errors persist) |
| Validation | On blur |
| Sign out | Not on this page (already in navbar dropdown) |
| Page animation | Staggered fade-up per section (400ms, 80ms stagger) |

## Sections

### 1. Page Header

- Title: "Account" — Merriweather, weight 300, 2.25rem (matches watched page, movie page)
- Subtitle: User's email address — DM Sans, 0.9rem, muted color
- 3rem bottom margin before first section

### 2. Profile

**Fields:**
- Display Name — text input, bottom-border style
- Email — email input, bottom-border style

**Labels:** DM Sans 500, 0.85rem, uppercase, 0.08em letter-spacing, muted color.

**Actions:** "Save Profile" primary button. Inline success/error feedback below.

**Validation:** Email validated on blur (format check). Error message appears below the field in danger color.

### 3. Preferences

**Controls:**
- "Exclude watched movies" — toggle switch (on by default for existing users who have the preference set)
- "Include adult content" — toggle switch (off by default)

Each toggle is a full-width row: label + description on the left, switch on the right. Rows highlight on hover with faint background (`color-mix(in srgb, var(--color-text) 4%, transparent)`).

**Actions:** "Save Preferences" primary button with inline feedback.

### 4. Security

**Password change:**
- Two fields side-by-side: "New password" and "Confirm password" (stack on mobile)
- "Update Password" primary button with inline feedback
- On-blur validation: passwords must match, minimum length check

**Active sessions:**
- Micro-label "ACTIVE SESSIONS" above the list
- Each session: device/browser name, last active timestamp
- Current session shows "(Current)" badge in accent color, no revoke button
- Other sessions show "Revoke" text button on the right
- Rows highlight on hover
- Rows separated by thin dividers
- Single session only: show the current session with muted note "No other active sessions"

**Revoke confirmation:** Inline — clicking "Revoke" replaces the button with "Confirm / Cancel" in the same row.

### 5. Data

**Export:**
- Description text: "Download all your data including watched list, preferences, and ratings."
- "Export My Data" secondary (outlined) button
- Inline feedback on click (progress → "Download ready" with auto-download or link)

**Letterboxd import:**
- Micro-label "IMPORT FROM LETTERBOXD"
- Drop area: dashed border, "Drop your Letterboxd CSV here or **browse**" text
- Drag-over state: border turns accent, faint accent background tint
- Hover state: border turns accent
- On upload: drop area replaced by inline result summary
- Result format: "{count} movies imported successfully. {n} titles could not be matched:"
- Unmatched display: if 5 or fewer, show titles inline in a list automatically. If more than 5, show count with expandable "Show unmatched" toggle.

### 6. Danger Zone

- Description: "Permanently delete your account and all associated data. This action cannot be undone."
- "Delete Account" danger button (outlined red, fills solid red on hover)
- On click: button hides, inline confirmation panel appears:
  - Bordered container (1px solid danger color, 3px radius)
  - Prompt: "Type **delete** to confirm permanent account deletion."
  - Text input with danger-colored bottom border
  - "Delete Forever" button (solid red, disabled/40% opacity until input matches "delete")
  - "Cancel" text button to dismiss
- Cancel returns to the initial state with the danger button visible

### Edge Cases

**OAuth users (Google/Apple sign-in):**
- Security section shows "Signed in with Google" (or Apple) with provider icon instead of password change fields
- Optionally offer "Set a password" link to enable dual auth (future enhancement, not in initial scope)

**Single session:**
- Show current session row with badge
- Muted note below: "No other active sessions"

**Import with no unmatched:**
- Result shows only: "{count} movies imported successfully." — no unmatched section

## Visual Foundation

### Typography

| Level | Family | Weight | Size | Style |
|-------|--------|--------|------|-------|
| Page title | Merriweather | 300 | 2.25rem | Normal case |
| Section heading | Merriweather | 300 | 1.5rem | Normal case |
| Field label | DM Sans | 500 | 0.85rem | Uppercase, 0.08em tracking |
| Body/description | DM Sans | 400 | 0.9rem | Normal case |
| Toggle label | DM Sans | 400 | 0.95rem | Normal case |
| Toggle description | DM Sans | 400 | 0.8rem | Normal case, muted |
| Button text | DM Sans | 600 | 0.8rem | Uppercase, 0.08em tracking |
| Badge text | DM Sans | 600 | 0.7rem | Uppercase, 0.06em tracking |

### Colors

Uses existing design tokens from `tokens.css` plus one new token for success feedback.

- Accent: `var(--color-accent)` — primary buttons, focus borders, toggle active, badges
- Danger: `#dc2626` light / `#ef4444` dark — danger buttons, delete confirmation
- Success (new): `#16a34a` light / `#22c55e` dark — add as `--color-success` in `tokens.css`
- Text, muted, border, bg, surface: existing tokens

### Spacing

- Page padding: 3rem top, 2rem horizontal
- Section padding-bottom: 2.5rem
- Section margin-bottom: 2.5rem
- Section heading margin-bottom: 1.75rem
- Field group margin-bottom: 1.5rem
- Label margin-bottom: 0.5rem
- Section actions margin-top: 2rem
- Page header to first section: 3rem

### Buttons

| Tier | Background | Border | Text | Hover | Focus |
|------|-----------|--------|------|-------|-------|
| Primary | `var(--color-accent)` | none | white | Darkened accent | 2px accent outline, 2px offset |
| Secondary | transparent | `1px solid var(--color-border)` | muted | Border darkens, text brightens | 2px accent outline, 2px offset |
| Danger | transparent | `1px solid danger` | danger | Fills solid danger, text white | 2px danger outline, 2px offset |
| Text | none | none | accent | opacity 0.75 | 2px accent outline, 2px offset |

All buttons: 3px border-radius, `scale(0.98)` on active, 200ms transition.

### Interaction States

- **Focus rings:** 2px solid accent, 2px offset on all interactive elements (inputs, buttons, toggles). Visible on `:focus-visible` only (keyboard nav, not mouse clicks).
- **Hover on rows:** Faint background highlight (`color-mix(in srgb, var(--color-text) 4%, transparent)`) on toggle rows and session rows. 150ms transition.
- **Drag-over on import:** Border turns accent, faint accent background tint. 200ms transition.
- **Danger button hover:** Fills solid red, text turns white. 200ms transition.
- **Input focus:** Bottom border changes to 2px accent color.

### Animation

- Staggered fade-up on page load: `opacity: 0 → 1`, `translateY(12px → 0)`, 400ms ease, 80ms stagger per section (header + 5 sections + danger = 7 elements, total stagger ~560ms)
- Save feedback fade: 300ms ease in, auto-fade after 3-4s for success
- Toggle slide: 200ms ease
- All transitions use `ease` timing function

### Mobile (below 480px)

- Password fields: single column (grid-template-columns: 1fr)
- Toggle rows: wrap — switch drops below label/description
- Session rows: maintain horizontal layout (revoke button is small enough)
- Page horizontal padding: reduces to 1.25rem
- Section headings: same size (1.5rem is still readable)

## Accessibility

- Focus rings on all interactive elements via `:focus-visible`
- Toggle switches need `role="switch"`, `aria-checked`, keyboard activation (Space/Enter)
- Session revoke buttons need `aria-label="Revoke session on {device}"`
- Delete confirmation input needs `aria-label="Type delete to confirm"`
- Color is never the sole indicator — all states have text or shape backup
- Minimum tap target: 44x44px for mobile touch targets (buttons, toggles)
- Success/error messages use `role="status"` or `aria-live="polite"` for screen reader announcement

## Out of Scope

- Avatar/profile photo upload — not in current implementation, add later if needed
- Password strength meter — basic length validation only for now
- Two-factor authentication — future enhancement
- Account recovery options — future enhancement
- "Set a password" for OAuth users — noted as future enhancement
- Notification preferences — no notification system exists yet
