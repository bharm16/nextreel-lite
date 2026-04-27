# Navbar Redesign — Design Spec

**Date:** 2026-04-27
**Scope:** Visual redesign of the global navbar (`templates/navbar_modern.html`).
**Out of scope:** Search backend changes; new search results page; mobile layout restructuring; brand wordmark or tagline changes.

## Context

The current navbar uses an editorial all-caps tracked treatment for nav items, a 34×34 outlined search icon button that opens a Cmd+K-style spotlight modal, and a "Log In" text link at 70% white. After scrolling past the home hero, the navbar transitions from transparent to a `--solid` cream state via [`static/js/navbar-scroll.js`](../../../static/js/navbar-scroll.js).

This redesign refreshes the right-side controls and search affordance without touching the brand lockup, the spotlight backend, or the mobile layout structure.

## Goals

1. Replace the search icon trigger with a visible, IMDb-style filled input field that reads as the navbar's anchor.
2. Modernize "Sign in" copy and lighten its visual treatment.
3. Remove decorative button outlines from all navbar controls (search icon mobile, hamburger).
4. Preserve current spotlight modal behavior — the new field is a more inviting trigger, not a new search system.
5. Establish a single typographic system for right-side nav items (sentence-case 13px DM Sans).

## Non-Goals

- No changes to `_search_spotlight.html` modal markup or behavior.
- No new `/search` results route.
- No mobile bar layout changes (Watched/Watchlist already live inside `#mobileMenu`).
- No changes to the avatar dropdown structure (only its "Log out" copy).
- No changes to the brand lockup, tagline, or scroll-state JS.

## Decisions

### 1. Copy

| Surface | Today | New |
|---|---|---|
| Desktop logged-out link | "Log In" | "Sign in" |
| Avatar dropdown logout button | "Log out" | "Sign out" |
| Mobile menu logged-out link | "Log In" | "Sign in" |
| Mobile menu logout button | "Log Out" | "Sign out" |

### 2. Typography for right-side nav items

A new shared treatment replaces `.navbar-link`'s all-caps tracked style for "Sign in", "Watched", and "Watchlist":

- Font: DM Sans (`--font-sans`)
- Size: 13px
- Weight: 600
- Letter-spacing: 0.06em
- Case: sentence-case (no `text-transform: uppercase`)
- Color (transparent state): `#ffffff`
- Color (solid state): `var(--color-text)` (`#1a1a1a`)
- Hover color: `var(--color-accent)` (`#c67a5c` dark / `#b0654f` light)
- Transition: `color var(--duration-normal) var(--easing-default)`
- Focus ring: existing 2px accent outline at 2px offset

The existing `.navbar-link` rule may stay in place if it has other consumers; if it does not, replace it. New rule name: `.navbar-link` (revised) or `.navbar-link--editorial` (parallel, deprecate old). Pick one in the implementation plan based on whether `.navbar-link` is referenced anywhere outside `navbar_modern.html`.

### 3. Search field

Replaces the `<button id="searchSpotlightTrigger">` icon button. Click behavior is unchanged: opens `#searchSpotlight` modal with input pre-focused.

**Markup:** A single button or div styled as an input, with a leading magnifying glass icon and placeholder-style text. It is **not** a real `<input>` — it is a styled trigger that opens the spotlight modal containing the real input. This keeps the spotlight backend the source of truth and avoids dual input state.

**Visual specification:**

- Width: 380px (logged-out), 340px (logged-in) — handled with a CSS attribute selector or `data-authenticated` flag on `.navbar`
- Height: 38px
- Border-radius: 4px
- Background (transparent state): `#ffffff` (solid white, both states)
- Background (solid state): `#ffffff` with `box-shadow: inset 0 0 0 1px #ebe8e1` for separation from cream bg
- Padding: 9px 12px
- Icon: 14×14, color `#6b6860` (matches `--color-text-muted`)
- Placeholder text: "Search films, actors…" (matches existing spotlight placeholder)
- Placeholder color: `#6b6860`
- Placeholder font: 13px DM Sans
- Position: centered between brand lockup and right-side actions (`margin: 0 auto`)
- Hover: cursor changes to `pointer`; no visible background shift (a white-on-white hover state is imperceptible and the click target opens a modal, so signaling hover adds noise without affordance)
- Focus-visible: 2px accent outline at 2px offset (matches existing button focus rings)
- Click: opens `#searchSpotlight` modal with its real `<input>` pre-focused

**Mobile (< 768px):** Field is hidden via media query. Today's icon-only trigger (`#searchSpotlightTriggerMobile`) is preserved structurally but loses its 1px border (see decision 4).

### 4. Border removal

The 1px decorative border on `.navbar-icon-btn` is removed in **all** uses:

- Mobile search icon trigger (`#searchSpotlightTriggerMobile`)
- Mobile hamburger button (`#menuBtn`)

Hover state replaces the border-color ramp with a color shift (icon stroke color: muted → full-strength). Focus-visible state retains the existing 2px accent outline at 2px offset for accessibility.

### 5. Logged-in nav items

"Watched" and "Watchlist" links adopt the new typography from decision 2 (sentence-case 13px DM Sans, no caps). They sit to the right of the search field, before the avatar dropdown. The avatar dropdown trigger is unchanged structurally; only the "Log out" copy inside it changes to "Sign out".

### 6. Mobile dropdown

`#mobileMenu` contents adopt the new typography sized up to 15px for touch readability. Order and structure unchanged:

- Logged out: `Sign in`
- Logged in: `Watched` · `Watchlist` · `Account` · `Sign out`

The "Sign out" item is rendered in `--color-accent` to signal a destructive action.

## Layout summary

### Desktop, transparent (over hero), logged-out

```
[Nextreel · Cinema Discovery]   [🔍 Search films, actors…       ]   Sign in
```

### Desktop, transparent (over hero), logged-in

```
[Nextreel · Cinema Discovery]   [🔍 Search films, actors… ]   Watched   Watchlist   (B)
```

### Desktop, solid (after scroll), logged-out

```
[Nextreel · Cinema Discovery]   [🔍 Search films, actors…       ]   Sign in
                  (dark text on cream bg, search field has inset hairline)
```

### Mobile (any auth state)

```
[Nextreel · Cinema Discovery]                              [🔍] [☰]
```

Hamburger opens `#mobileMenu` with auth-aware contents.

## Files affected

- [`templates/navbar_modern.html`](../../../templates/navbar_modern.html) — replace search icon button with styled trigger; update copy strings; update logged-in nav link classes.
- [`static/css/input.css`](../../../static/css/input.css) — replace `.navbar-link` rules; replace `.navbar-icon-btn` border-related rules; add `.navbar-search-trigger` (or chosen class name) for the new search field; add solid-state hairline rule.
- [`static/css/output.css`](../../../static/css/output.css) — regenerated by Tailwind build.
- No changes to [`templates/_search_spotlight.html`](../../../templates/_search_spotlight.html).
- No changes to [`static/js/navbar.js`](../../../static/js/navbar.js), [`static/js/navbar-scroll.js`](../../../static/js/navbar-scroll.js), or [`static/js/search-spotlight.js`](../../../static/js/search-spotlight.js) beyond rebinding the spotlight trigger event from the old button id to the new one (verify only one binding exists; trigger id may be renamed for clarity).

## Accessibility

- Search trigger: `<button>` element with `aria-label="Open search"`, `aria-haspopup="dialog"`, `aria-controls="searchSpotlight"` — same attributes as today's icon button.
- Sentence-case nav items remain `<a>` elements with no ARIA changes.
- Focus-visible outlines preserved on all interactive elements.
- Touch targets remain ≥44px on mobile (today's `.navbar-icon-btn--mobile` size is unchanged).
- Color contrast: white-on-dark hero exceeds 4.5:1; dark-on-cream solid exceeds 4.5:1.

## Risks and tradeoffs

- **Risk:** White search field on cream solid bg has weak separation. **Mitigation:** Inset 1px hairline at `#ebe8e1` (decision 3).
- **Risk:** Sentence-case "Sign in" / "Watched" / "Watchlist" breaks the editorial all-caps signature. **Tradeoff accepted:** Creates a deliberate hierarchy between the brand lockup (caps preserved) and the action layer (sentence-case).
- **Risk:** Removing icon button borders reduces affordance. **Mitigation:** Hover color shift compensates; touch targets unchanged.
- **Risk:** Search trigger is a button styled as an input, which may surprise users who try to type before clicking. **Tradeoff accepted:** Keeping the spotlight modal as the single source of typing state is simpler than dual-input synchronization. The spotlight pre-focuses its real input on open, so the perceived flow is "click, then type" — same as today.

## Visual references

Mockups for each state are persisted in `.superpowers/brainstorm/73238-*/content/`:

- `baseline.html` — current navbar (before)
- `signin-borderless.html` — sign-in treatment options
- `search-bar-v2.html` — search field options (IMDb / Letterboxd inspired)
- `collateral.html` — typography parity, mobile pattern, copy parity decisions
- `final-design-v2.html` — final consolidated mockup
