# Watched Button Redesign

**Date:** 2026-04-16
**Status:** Approved

## Goal

Replace the saturated green "Mark as Watched" pill on the movie detail page with a control that fits the editorial, text-forward design vocabulary of the rest of the page. The button currently lives in a standalone `<div class="mt-3">` block wedged between the action row (Play Trailer / IMDb / Website) and the collection banner, styled as a Tailwind pill (`bg-green-600 rounded-full`). Both its placement and its visual treatment clash with the surrounding uppercase micro-links, serif title, and muted palette.

## Design Decisions Summary

| Dimension | Decision |
|-----------|----------|
| Placement | Middle slot of the existing sticky `.movie-nav-bar`, between Previous and Next |
| Visual style | Peer to Prev/Next: sentence case, `0.8rem`, weight 600, text-only, small SVG icon |
| Unwatched color | `var(--color-text-muted)` (same as Previous in its default state) |
| Watched color | New token `--color-watched` — muted sage harmonizing with `--color-accent` |
| State affordance | `data-watched-state` attribute on the form; CSS handles all styling |
| Icon | 16px SVG — eye (unwatched) → check (watched), leading the label |
| Mobile label | Shortens "Mark as Watched" → "Watched" below 640px |
| Logged-out behavior | Middle slot hidden entirely; bar renders Prev / Next as today |
| Auto-advance | Out of scope — click toggles watched, user still clicks Next to advance |
| Route/API changes | None — existing `/watched/add/{tconst}` and `/watched/remove/{tconst}` endpoints and JSON response untouched |

## Placement

The watched control moves from its standalone `<div class="mt-3">` block inside `movie_card.html` into the existing sticky bottom navigation bar (`.movie-nav-bar` at the bottom of the same template). The bar already has two forms (Previous, Next) with `justify-content: space-between`; adding a third form in the middle keeps the edge-center-edge distribution without any layout rule changes.

The current standalone block (lines 77–116 of `templates/movie_card.html`) is deleted entirely. No visual replacement is left behind in the body flow — the action is fully relocated into the sticky bar.

## Visual Treatment

### Unwatched state

- Label: `Mark as Watched`
- Size: `font-size: 0.8rem`, `font-weight: 600` (matches `.nav-btn-prev`)
- Color: `var(--color-text-muted)`
- Icon: 16px SVG eye outline, leading the label with a 0.35rem gap
- Background: none
- Border: none
- Padding: `0.4rem 0` (matches Prev/Next)
- Hover: color transitions to `var(--color-text)` (matches Prev's hover)

### Watched state

- Label: `Watched`
- Size/weight/structure: identical to unwatched
- Color: `var(--color-watched)` — a new token set to a muted sage green that sits quietly against the page's `--color-accent`. Light-mode and dark-mode values both live in `tokens.css`.
- Icon: 16px SVG check (same path the current button uses)
- Hover: small opacity shift (0.85), no color change

### Disabled / loading state

- `button.disabled = true` during fetch (existing behavior, preserved)
- `opacity: 0.4`, `cursor: not-allowed` (matches `.nav-btn-prev:disabled`)
- `aria-busy="true"` applied during the request (preserved)

## Accessibility

All existing a11y behavior is preserved — the redesign is style + placement, not interaction:

- `aria-pressed="true|false"` on the button, reflecting watched state
- `aria-busy="true"` added during the request, removed on completion
- The `#movie-status` live region continues to announce "Marked as watched." / "Removed from watched." / "Could not update watched status."
- The form remains keyboard-submittable; the button remains keyboard-focusable with a visible focus ring (inherits from the existing `--color-accent` focus styles)

## Logged-out behavior

The existing `{% if current_user_id %}` Jinja guard moves with the control into the sticky bar. When absent, the bar renders with only Previous and Next, distributed edge-to-edge via the existing `justify-content: space-between` rule. No "log in to track" placeholder — the bar stays clean.

## Mobile / responsive behavior

The sticky bar already tightens to `padding: 0.5rem 1rem` below 640px (see `input.css` line 1589). At that width:

- The unwatched label shortens from `Mark as Watched` → `Watched`. Implementation: wrap the label word "Mark as " in a `<span class="nav-btn-watched__prefix">`. Inside the existing `@media (max-width: 640px)` block, set `.nav-btn-watched__prefix { display: none; }`.
- The watched-state label is already `Watched` — no change needed on mobile.
- Icon remains at 16px in all states.
- Three buttons fit comfortably at the narrowest supported widths (tested mentally against the 320px floor).

## JS refactor

The current `movie-card.js` watched IIFE hard-codes two full Tailwind utility-class strings as JS literals:

```js
var watchedClassName = "inline-flex items-center gap-1.5 rounded-full bg-green-600 ...";
var unwatchedClassName = "inline-flex items-center gap-1.5 rounded-full chip ...";
```

This couples styling to JS — every visual tweak requires editing `movie-card.js`. The redesign moves styling to CSS:

- JS toggles `form.dataset.watchedState = "watched" | "unwatched"` (already does this) and swaps the button's icon + label HTML. It no longer manages classes.
- CSS selectors `form[data-watched-state="unwatched"] .nav-btn-watched` / `form[data-watched-state="watched"] .nav-btn-watched` own all visual differences. The `data-watched-state` attribute stays on the form (matching the existing JS), CSS styles the button inside it.
- The `watchedMarkup` / `unwatchedMarkup` HTML strings in JS shrink to just the icon + text (no class attributes needed — the form-level `data-watched-state` drives styling).

Net result: future restyles happen in CSS only.

## File changes

1. `templates/movie_card.html`
   - Delete lines 77–116 (standalone `<div class="mt-3">` block with the watched form).
   - Inside `.movie-nav-bar` (near line 267), add a third form between the Previous and Next forms, gated by `{% if current_user_id %}`. The form retains `data-watched-toggle-form`, `data-watched-state`, `data-add-url`, `data-remove-url` attributes so the existing JS selector still matches.

2. `static/css/tokens.css`
   - Add `--color-watched` token in both `:root` (light mode) and `[data-theme="dark"]` blocks. Initial values: `#5a8a3c` (light mode — a muted forest), `#9bc97b` (dark mode — a muted sage). Both intentionally desaturated so the color signals "confirmed" without competing with `--color-accent`. Values can be tuned during implementation if they clash with the current accent palette, but this spec commits to starting there.

3. `static/css/input.css`
   - Add `.nav-btn-watched` rule block adjacent to the existing `.nav-btn-prev` / `.nav-btn-next` rules (around line 377). Includes base styles, hover, disabled, and the `[data-watched-state]` state variants.
   - Add the responsive label-swap rule inside the existing `@media (max-width: 640px)` block near line 1589.

4. `static/js/movie-card.js`
   - Simplify the watched IIFE (lines 59–123): remove `watchedClassName` and `unwatchedClassName` string literals. `setWatchedState` only updates `form.dataset.watchedState`, `form.action`, `button.innerHTML` (icon + label), and `aria-pressed`. No `button.className` manipulation.

## Out of scope

- Auto-advance to the next movie on mark-watched
- Any change to the watched-list page (`watched_list.html`) or `_watched_card.html` partial — these already use their own styling
- New keyboard shortcut for marking watched
- Any server-side, route, worker, or database change — the `/watched/add/{tconst}` and `/watched/remove/{tconst}` routes and their JSON contract are unchanged

## Rationale

The movie detail page's vocabulary is consistent and deliberate: serif display title, muted micro-links for external references, a text-only sticky nav bar, no pills or outlined chips anywhere in the body. The current watched pill is the single element in the layout that breaks this vocabulary, which is why it reads as "randomly placed" even though its position is technically deterministic. Moving it into the sticky bar places it in the natural flow of user action — browse, mark seen, move on — while adopting the bar's existing style makes it look like it was always meant to be there.

Decoupling the JS from Tailwind utility strings is a small but meaningful cleanup: the next visual iteration (for example, theming tweaks, a light-mode refinement, or a color-token change) becomes a pure CSS change instead of a JS edit.
