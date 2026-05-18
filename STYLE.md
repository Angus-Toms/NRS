# Pro Tri Data - Style Guide

This guide documents the **actual** UI as implemented. Where the previous guide diverged from the code, this version follows the code and flags the divergence in the [Discrepancies & TODO](#discrepancies--todo) section at the end.

The guide is the source of truth for visual decisions. The code in `static/css/base.css` (~2100 lines) is the source of truth for selectors and tokens.

---

## 1. Design Philosophy

Pro Tri Data is a **data-first** stats site. Every design decision serves making numerical information easy to read, compare, and explore.

1. **Clarity over decoration** - if an element doesn't communicate, remove it.
2. **Numerical readability** - numbers are the product. They must scan at a glance.
3. **Consistency** - identical data types always look identical.
4. **Simplicity** - one typeface, small palette, minimal chrome.

The aesthetic is clean and editorial - a well-designed reference tool, not a dashboard.

---

## 2. Design Tokens (`:root` in `base.css`)

Always reference variables, never hardcoded values. The full token set:

```css
:root {
    /* Colour - interactive */
    --primary-color: #e85d04;   /* orange accent: links, highlights, active states */
    --primary-hover: #c44d03;   /* orange hover */

    /* Colour - structural */
    --navy:          #1a1a2e;   /* header, hero band, table thead, accent surfaces */
    --bg-color:      #f4f4f2;   /* warm off-white page background */
    --white:         #ffffff;   /* card surfaces */

    /* Colour - text */
    --text-color:    #111827;   /* primary text */
    --text-light:    #6b7280;   /* secondary text, metadata */
    --text-lighter:  #9ca3af;   /* tertiary, placeholders, disabled */
    --border-color:  #e5e7eb;   /* card borders, table dividers, hr */

    /* Colour - status */
    --success-color: #059669;   /* rating increases ONLY */
    --error-color:   #dc2626;   /* rating decreases ONLY */
    --warning-color: #d97706;   /* used sparingly for warning chips */
    --highlight:     #fef3e8;   /* table row hover, soft orange tint */

    /* Colour - podium (used in two contexts: avatar rings + position text) */
    --gold:    #f5c842;  --gold-text:   #5a4000;
    --silver:  #c8d8e0;  --silver-text: #3a4a50;
    --bronze:  #d4a870;  --bronze-text: #5a3000;

    /* Spacing scale - use these, do not invent new values */
    --spacing-xs:  0.25rem;   /*  4px */
    --spacing-sm:  0.5rem;    /*  8px */
    --spacing-md:  1rem;      /* 16px */
    --spacing-lg:  1.5rem;    /* 24px */
    --spacing-xl:  2rem;      /* 32px */
    --spacing-xxl: 3rem;      /* 48px - section dividers */

    /* Typography scale */
    --font-family:    'Plus Jakarta Sans', sans-serif;
    --font-size-sm:   0.875rem;
    --font-size-base: 16px;
    --font-size-lg:   1.125rem;
    --font-size-xl:   1.5rem;
    --font-size-xxl:  2rem;

    /* Radius - 8px is the default; 4px for tight chrome; 12px for hero/feature surfaces */
    --border-radius:    8px;
    --border-radius-sm: 4px;
    --border-radius-lg: 12px;

    /* Shadows */
    --box-shadow:       0 1px 3px  rgba(0,0,0,0.07);   /* resting cards */
    --box-shadow-hover: 0 4px 16px rgba(0,0,0,0.09);   /* hover lift */
    --box-shadow-lg:    0 8px 24px rgba(0,0,0,0.12);   /* heroes, modals */

    /* Transitions */
    --transition-fast: 0.15s ease;   /* colour, opacity */
    --transition-base: 0.2s  ease;   /* default */
    --transition-slow: 0.4s  ease;   /* layout, large surfaces */
}
```

### Colour usage rules

- **Orange (`--primary-color`)** is the only interactive accent. Use for: link hover, active nav, section heading left-border, card hover border, eyebrow labels, dates, "fastest" split, rating change arrows, active filter chip text.
- **Navy** provides structural depth: site header, page hero, event card title bands, `<thead>` rows. Appear often enough to anchor the page; never enough to compete with content.
- **Green / red** are reserved for rating change indicators only. Do not reuse for generic positive/negative states elsewhere.
- **Gold / silver / bronze** are reserved for podium positions (avatar rings + position text). Both contexts must use the same colour.

---

## 3. Typography

Single typeface throughout: **Plus Jakarta Sans** (self-hosted from `static/fonts/plus-jakarta-sans/`, declared at top of `base.css`). No fallback display font.

### Weight usage

| Weight | Use |
|--------|-----|
| 400 | Body copy, secondary metadata, descriptions |
| 500 | Table cell data, athlete names in lists, nav links |
| 600 | Sub-headings, labels, eyebrow text, card link titles |
| 700 | Section headings, table sort-active headers, card headings, hero H1 |
| 800 | Site logo, large rating numbers, footer site-name |

### Scale (actual values from the code)

| Role | Class / Selector | Size / Weight |
|------|------------------|---------------|
| Site logo | `.logo-text` | `1.05rem` / 800, letter-spacing `0.06em` |
| Page hero H1 | `.page-hero h1` | `1.75rem` / 700, letter-spacing `-0.02em` |
| Section heading | `.page-section-head` | `1.5rem` / 700, letter-spacing `-0.02em` |
| Section sub | `.page-section-sub` | `0.85rem` / 400, `--text-lighter` |
| Card heading | `.card-title` | `1rem` / 600, **uppercase**, letter-spacing `0.5px` |
| Stat number | `.stat-number` | `1.5rem` / 700, `--primary-color` |
| Table header | `thead th` | `0.7rem` / 600, **uppercase**, letter-spacing `0.08em` |
| Body / table cell | default | `0.875rem` / 400-500 |
| Metadata | `.meta-value` | `1rem` / 500 |
| Meta / eyebrow label | `.meta-label`, `.stat-label` | `0.7rem` / 500-600, **uppercase**, letter-spacing `0.5px` |
| Footer | `.footer-container` | `0.73rem` |
| Fine print | various | `0.7rem` |

### Numeric formatting

All tabular numbers use `font-variant-numeric: tabular-nums` so columns align. Large display ratings use weight 800. Never substitute a different typeface for numbers - Jakarta Sans is legible at every size.

### Casing rules

- **UPPERCASE** (with letter-spacing): table headers, eyebrow/meta labels (`.meta-label`, `.stat-label`, `.filter-chip-label`), `.card-title`, badge text (`.multi-stage-badge`, classification pills), filter section labels.
- **Sentence case**: section headings, card titles that aren't `.card-title`, body copy, athlete names.
- **lowercase**: never apply `text-transform: lowercase`. Source case wins.

---

## 4. Layout

- Page max width: `max-width: 1100px; width: 90%` on `.page-container`. Centred.
- Page background: `--bg-color` (warm off-white). Cards sit on `--white` for subtle lift.
- Section vertical rhythm: `<hr class="rule">` (1px `--border-color`, margin `3rem 0 0`) between major sections. Avoid wrapping every section in a card.

### Page hero (full-bleed navy band)

Used on athlete, race, country, leaderboard, etc. as a top anchor below the site header.

```html
<header class="page-hero">
  <div class="page-hero-inner">
    <h1>Leaderboard</h1>
    <p class="page-hero-subtitle">Top elite triathletes by current rating</p>
  </div>
</header>
```

```css
.page-hero { background: var(--navy); border-bottom: 3px solid var(--primary-color); }
.page-hero-inner { width: 90%; max-width: 1100px; margin: 0 auto; padding: 1.25rem 0;
                   display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; }
.page-hero h1 { color: #fff; font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; white-space: nowrap; }
.page-hero-subtitle { color: rgba(255,255,255,0.5); font-size: 0.875rem; text-align: right; }
```

### Section heading (in-page)

The standard introducer for a content section. **4px** orange left border (not 3px) is the primary visual anchor. Used as a flex row so a "see more" link can sit at the right (`justify-content: space-between`).

```html
<div class="page-section-head">
  <span>Recent Races <span class="page-section-sub">last 30 days</span></span>
  <a href="/races" class="link">Browse all</a>
</div>
```

```css
.page-section-head {
  display: flex; align-items: center; justify-content: space-between;
  border-left: 4px solid var(--primary-color);
  padding-left: 0.75rem;
  margin-bottom: 1rem;
  font-size: 1.5rem; font-weight: 700; color: var(--text-color);
  letter-spacing: -0.02em;
}
```

Do not stack an eyebrow above a section heading - the heading text alone is sufficient. Eyebrows are reserved for nav cards and the athlete hero where the category label is genuinely useful.

---

## 5. Header

Sticky, full-bleed navy bar with logo on the left and link nav on the right.

```html
<header class="site-header">
  <div class="header-container">
    <div class="logo">
      <a href="/">
        <img src="..." alt="Pro Tri Data Logo" class="logo-img">
        <span class="logo-text">Pro Tri Data</span>
      </a>
    </div>
    <nav class="main-nav">
      <a href="/"          class="nav-link">Home</a>
      <a href="/athletes"  class="nav-link active">Athletes</a>
      <a href="/races"     class="nav-link">Races</a>
      <a href="/countries" class="nav-link">Countries</a>
      <a href="/upcoming"  class="nav-link">Upcoming</a>
      <a href="/about"     class="nav-link">About</a>
    </nav>
  </div>
</header>
```

Key facts:
- `.site-header` is `position: sticky; top: 0; z-index: 1000`, full-bleed navy with `border-bottom: 1px solid rgba(255,255,255,0.08)`.
- Header height is **52px** fixed (`.header-container`).
- Logo image: 28px tall. Logo text: `1.05rem` / 800, letter-spacing `0.06em`. (Source HTML uses "Pro Tri Data" mixed case; CSS does not transform.)
- Nav links: `0.8rem` / 500, letter-spacing `0.08em`, default colour `rgba(255,255,255,0.55)`. Hover lifts to `rgba(255,255,255,0.85)`. Active state: full white + 2px orange `border-bottom`.
- The `active` class on `.nav-link` is set in templates from `active_page`.

---

## 6. Footer

Full-bleed navy band, three-column grid (site name | attribution | copy) collapsing to a single column under 600px.

```html
<footer class="site-footer">
  <div class="footer-container">
    <p class="footer-site-name">Pro Tri Data</p>
    <p class="footer-attribution">Results and data provided by
      <a class="footer-ext-link" href="..." target="_blank">World Triathlon</a> and the
      <a class="footer-ext-link" href="..." target="_blank">PTO</a>.
      PTD is in no way affiliated with World Triathlon or PTO.
    </p>
    <p class="footer-copy">&copy; 2025 Pro Tri Data</p>
  </div>
</footer>
```

- Background `--navy`, default text colour `rgba(255,255,255,0.5)`.
- `.footer-site-name`: `0.85rem` / 800, white, letter-spacing `0.06em`.
- `.footer-ext-link`: default `rgba(255,255,255,0.6)`, hover `#fff`. No underline.
- `.footer-copy`: `rgba(255,255,255,0.25)` (deliberately faint).
- Body uses `min-height: 100vh; display: flex; flex-direction: column` so the footer sticks to the bottom even on short pages.

---

## 7. Cards

Cards (`--white` surface, `1px solid var(--border-color)`, `border-radius: 8px`) group related content. **Do not** make them the default wrapper - prefer open layout with `<hr class="rule">` between sections, and reserve cards for content that genuinely benefits from a contained surface.

### Resting and hover

```css
.card { background: var(--white); border: 1px solid var(--border-color); border-radius: var(--border-radius);
        box-shadow: var(--box-shadow); transition: border-color var(--transition-fast), box-shadow var(--transition-fast); }
.card:hover { border-color: var(--primary-color); box-shadow: var(--box-shadow-hover); }
```

The card's title transitions to `--primary-color` on hover. Never use a background-colour change as the hover signal; never use `transform: translateY(...)` - the border + shadow is sufficient.

### Overlay link pattern (whole card clickable + nested links)

When a card must be fully clickable but contains nested interactive elements, use an invisible overlay anchor rather than wrapping the card in `<a>`.

```html
<div class="card" style="position:relative;">
  <a href="/athlete/123" class="card-overlay" aria-label="Athlete name"></a>
  <div style="position:relative; z-index:1; pointer-events:none;">
    <h3>Athlete name</h3>
  </div>
  <a href="/athlete/123/results" style="position:relative; z-index:1; pointer-events:all;">All results</a>
</div>
```
```css
.card-overlay { position: absolute; inset: 0; z-index: 0; }
```

### Navy header band within a card

Event cards split into a dark navy header band (white title, weight 600, metadata at ~55% opacity) and a white body. Use this any time a card needs more visual weight than a plain heading.

---

## 8. Tables

Tables are the primary data display surface. They live inside `.table-card` (white, bordered, `border-radius: 8px`, `overflow: hidden` to clip header corners).

### Header row

Navy background with white text, mirrors the site header.

```css
thead tr { background: var(--navy); }
thead th {
  font-size: 0.7rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: rgba(255,255,255,0.6);
  padding: 0.6rem 1rem;
  text-align: left;
}
```

### Body rows

- Alternating rows: subtle `#fafafa` stripe.
- Row hover: `--highlight` (soft orange tint).
- Last row's `border-bottom` is removed to avoid doubling at the card edge.
- Position-tinted rows: `.position-gold`, `.position-silver`, `.position-bronze` apply a very subtle background to highlight podium rows in history tables.

### Column alignment

- Position numbers: left, narrow fixed width.
- Athlete names: left, flex.
- Times and ratings: **left-aligned** (default). Do not right-align - `tabular-nums` already handles column alignment.
- Change indicators: inline after the value, same cell.

### Sortable columns

`th.col-sortable` shows a sort arrow that is hidden by default and appears on hover. When actively sorted the icon turns orange and stays visible.

```css
.sort-icon { opacity: 0; transition: opacity var(--transition-fast); }
.col-sortable:hover .sort-icon { opacity: 1; color: var(--text-light); }
.col-sortable[data-dir] .sort-icon { opacity: 1; color: var(--primary-color); }
```

State icons: `.si-up`, `.si-down`, `.si-neutral` (inline SVG). On hover the column header background lightens with `rgba(255,255,255,0.07)` overlay.

### Mobile

Tables do not reflow on mobile - they scroll horizontally inside `.table-card` via `overflow-x: auto`. Column alignment is essential to reading split data and must not be lost.

---

## 9. Position Indicators

Positions 1-3 use a **filled colour circle** (`.pos-circle` with `.gold` / `.silver` / `.bronze` modifier) and dark text. Position 4+ uses `.pos-other` (plain muted text, no circle). Templates pick the modifier conditionally:

```html
{% if pos == 1 %}<span class="pos-circle gold">1</span>
{% elif pos == 2 %}<span class="pos-circle silver">2</span>
{% elif pos == 3 %}<span class="pos-circle bronze">3</span>
{% else %}<span class="pos-other">{{ pos }}</span>{% endif %}
```

```css
.pos-circle { display:inline-flex; align-items:center; justify-content:center;
              width: 22px; height: 22px; border-radius: 50%;
              font-weight: 700; font-size: 0.75rem; }
.pos-circle.gold   { background: var(--gold);   color: var(--gold-text);   }
.pos-circle.silver { background: var(--silver); color: var(--silver-text); }
.pos-circle.bronze { background: var(--bronze); color: var(--bronze-text); }

.pos-other { font-weight: 400; color: var(--text-lighter); }
```

The same `.pos-circle` + colour modifier is used for both current-race results and history tables. There are no separate text-only colour classes for positions 1-3 (an earlier `.pos-1st` / `.pos-first` family was removed during the cleanup).

### DNS / DNF / DQ / LAP / NC

Templates render any non-finishing status with a single `.pos-dnf` class regardless of which status string it is. Plain italic text in `--text-lighter`, no background, no pill.

```css
.pos-dnf {
  font-weight: 400; font-style: italic; color: var(--text-lighter); font-size: 0.8rem;
}
```

### Rank badges (leaderboard)

A larger numeric position with the same gold/silver/bronze text colours, used in `.rank-badge` on the leaderboard for the top 3.

---

## 10. Rating Numbers and Changes

### Rating block (used in leaderboard cards, search results)

```html
<div class="athlete-ratings">       <!-- add .athlete-ratings-hot for trending view -->
  <div class="rating-item">
    <span class="rating-label">Overall</span>
    <span class="rating-value rating-highlight">1284</span>
  </div>
  <div class="rating-item">
    <span class="rating-label">Swim</span>
    <span class="rating-value">1150</span>
  </div>
  <!-- Bike, Run -->
</div>
```

- `.rating-label`: small grey pill above the value.
- `.rating-highlight`: orange for the active sort discipline (or `.athlete-ratings-hot` parent → green for trending).

### Rating change indicators

Format is **arrow + number only** with no `+` / `-` prefix; the arrow already conveys direction.

```
↑45    (not ↑+45 or +45)
↓8     (not ↓-8 or -8)
```

The class is set by `app/routers/router_utils.py` on each rating change record (`css_class` = `rating-increase` / `rating-decrease` / `rating-neutral`) and applied alongside `.rating-change`:

```html
<span class="rating-change {{ change.css_class }}">{{ change.formatted_str|safe }}</span>
```

```css
.rating-increase { color: var(--success-color); font-weight: 500; }
.rating-decrease { color: var(--error-color);   font-weight: 500; }
.rating-neutral  { color: var(--text-lighter);  font-weight: 500; }
```

In leaderboard tables the change appears in its own cell; in rating history it sits inline as a `<span>` after the rating.

---

## 11. Stat Strips (`.race-stat-cards`)

Compact horizontal group of stat cells, used to show a number across disciplines or categories. Lives in:

| Page | Sections |
|------|----------|
| Athlete | Current Ratings, World Rankings, Peak Ratings, Best Single Performances |
| Race | Race Standards, Best Performances, Course Conditions |
| Event | Course Conditions |

It's the single most-repeated stat-display pattern on the site, so it has its own conventions.

### Surface and dividers

**No card chrome.** Cells sit on the page background separated by 1px divider lines, not on white surfaces with their own borders. Five borders + radii + shadows for "five small numbers" is too much visual weight; flat cells with thin separators read as one coherent strip.

The divider lines come from the **gap-as-divider** trick: the parent has `gap: 1px` and `background: var(--border-color)`, the cells have `background: var(--bg-color)`. The parent's background bleeds through the gaps, producing 1px lines between columns (and between rows when the strip wraps).

```css
.race-stat-cards {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 1px;
    background: var(--border-color);
    margin-bottom: 2rem;
}

.race-stat-card {
    background: var(--bg-color);
    padding: 0.875rem 0.625rem;
    text-align: center;
    min-width: 0;       /* lets cells shrink below their content width */
}
```

Use `.race-stat-cards--4` for 4-cell groups (e.g. Course Conditions: Overall, Swim, Bike, Run).

### Cell anatomy

Three optional rows inside each cell:

```html
<div class="race-stat-card">
  <div class="race-stat-disc">Overall</div>             <!-- label, uppercase -->
  <div class="race-stat-num">1284</div>                 <!-- big number -->
  <div class="race-stat-sub">                           <!-- optional context -->
    <a href="..." class="small-link">Hamburg WTCS 2024</a>
  </div>
</div>
```

| Element | Style |
|---------|-------|
| `.race-stat-disc` | `0.65rem` / 700, **uppercase**, letter-spacing `0.06em`, `--text-lighter` |
| `.race-stat-num` | `1.35rem` / 800, `--text-color` (or success/error tint via `.rating-increase` / `.rating-decrease`) |
| `.race-stat-sub` | `0.72rem`, `--text-light`, **2-line clamp with ellipsis** |

### Responsive layout

The 5-cell layout doesn't divide cleanly on narrow screens (5 is prime). At ≤768px the strip becomes a 2-column grid where the **first cell (Overall) spans both columns**, and the remaining 4 cells fall into a 2x2 below:

```
[      Overall      ]
[ Swim    | Bike    ]
[ Run     | Trans   ]
```

Overall stays prominent (it's the headline number on every section), and the four disciplines split symmetrically.

```css
@media (max-width: 768px) {
    .race-stat-cards {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .race-stat-cards:not(.race-stat-cards--4) > .race-stat-card:first-child {
        grid-column: 1 / -1;
    }
}
```

The 4-cell variant becomes a plain 2x2 (no spanning) at the same breakpoint. ≤480 keeps the same layout; 2 columns stays readable for single numbers.

### Text overflow

`.race-stat-sub` allows up to **2 lines** before truncating with ellipsis. This is more useful than the previous 1-line ellipsis: race names like "World Championships 2023" are visible, just wrap onto a second line. Use `-webkit-line-clamp` for cross-browser support:

```css
.race-stat-sub {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    overflow-wrap: anywhere;
    line-height: 1.3;
}
```

`.race-stat-disc` and `.race-stat-num` are short by design and stay on one line.

### When to use a strip vs cards

- **Strip (this pattern):** 3-6 small stats of the same kind (numbers per discipline, counts, percentages). Compact, low-chrome.
- **Cards (`.card` etc.):** Larger compositions with multiple elements per cell (athlete card with photo + name + rating block + meta). Card chrome is justified by richer contents.

If a "card" only contains a label + a number + maybe a sub-line, use a strip instead.

---

## 12. Race Results - Split Cells

Each split cell (Swim, T1, Bike, T2, Run, Overall) contains two stacked lines:

1. The time itself - `font-weight: 500`, `0.875rem`, `--text-color`.
2. The gap to the fastest split - `font-weight: 400`, `0.7rem`, `--text-light`.

The fastest time in each split is highlighted orange (`font-weight: 600`); its gap line reads `"fastest"` instead of `+0:00`.

```html
<td>
  <span class="time-cell-main">52:41</span>
  <span class="time-cell-gap">+0:03</span>
</td>

<td>  <!-- fastest in this split -->
  <span class="time-cell-main fastest">52:38</span>
  <span class="time-cell-gap">fastest</span>
</td>
```

Overall column uses bolder weight (`font-weight: 700`) and `display: block` on the gap span to push it to a second line.

---

## 13. Podium Displays (event cards)

Three-entry podium, columns separated by the **gap-as-divider** trick: the flex container has a 1px gap with `background: var(--border-color)` so the gap colour bleeds through as a divider.

```css
.event-podiums { display: flex; gap: 1px; background: var(--border-color); }
.podium-col    { flex: 1; background: var(--white); }
```

Athlete names truncate with `text-overflow: ellipsis` - never wrap.

Podium avatars use **gold/silver/bronze `box-shadow` rings** on positions 1/2/3. This is the one exception to the otherwise neutral profile-photo treatment - the colour directly encodes finishing position.

---

## 14. Navigation Cards (home page)

Top-level nav cards (Athletes, Races, Compare, Leaderboard) use a **3px top border accent** that appears on hover.

```css
.nav-card { border-top: 3px solid transparent; }
.nav-card:hover { border-top-color: var(--primary-color); }
```

Each card has: eyebrow (category label, orange uppercase), heading, short description, and a destination link. The eyebrow functions as a category tag, not chrome.

---

## 15. Profile Photos

There is one utility class for all circular athlete photos: **`.profile-img`**, defined in `base.css` (around line 1115). It sets `border-radius: 50%`, `object-fit: cover`, `display: block`, and a soft drop shadow. Apply it to any new avatar image.

```html
<!-- new code -->
<img src="..." alt="Alex Yee" class="profile-img" style="width:48px;height:48px;">
```

```css
.profile-img {
  border-radius: 50%;
  object-fit: cover;
  display: block;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
}
```

The legacy class names (`.athlete-profile-img`, `.result-avatar`, `.as-result-avatar`, `.as-champ-img`, `.champ-img`, `.event-race-entry-img`, `.event-podium-img`, `.sel-athlete-img`, `.podium-row-img`, `.pred-result-avatar`, `.lb-img`, `.athlete-hero-img`) are aliased into the same rule, so existing templates already pick up the shared treatment without modification. New components should use `.profile-img` directly rather than inventing a new alias.

**Sizing** is set per-context in page CSS, never on `.profile-img` itself. Common sizes: 26px (dense lists), 40-48px (default lists), 64-72px (cards), 110px (athlete hero).

### `.profile-img--on-dark` modifier

Apply when the photo sits on a navy or otherwise dark surface. It adds a 2px translucent white ring (so the photo edge stays visible against the dark background) and a heavier drop shadow for lift.

```html
<!-- on a navy hero band -->
<img src="..." alt="Alex Yee" class="profile-img profile-img--on-dark" style="width:88px;height:88px;">
```

```css
.profile-img--on-dark {
  border: 2px solid rgba(255, 255, 255, 0.2);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
}
```

The athlete page hero (`.athlete-hero-img`) is the existing on-dark instance and is aliased into both rules. Any new avatar that sits on `--navy` (header avatars, navy event-card bands, dark mode contexts) **must** use this modifier. A circular photo on a dark surface with a soft shadow alone has no visible edge.

Decision principle: **shadow is enough on light surfaces; on dark surfaces add the ring.** Never use coloured borders on profile photos for any other purpose (active state, hover, etc.).

### Podium avatars (one exception)

Race and search podium positions use **gold/silver/bronze `box-shadow` rings** on positions 1/2/3 (replacing the soft default shadow, not adding to it). This is the one exception to the otherwise neutral treatment, because the colour directly encodes finishing position.

### Active vs retired athletes

Retired athletes: reduced opacity + slight desaturation applied via an `.inactive` modifier. **Never** use coloured borders for active/retired, since those colours belong to rating changes.

```css
.athlete-hero-img.inactive { opacity: 0.5; filter: grayscale(50%); }
/* List-context retirees use opacity 0.55, grayscale 40% on the per-class selector */
```

A future cleanup could fold this into `.profile-img.inactive` so the modifier works regardless of class.

---

## 16. Forms, Inputs, Filters

### Text inputs / selects

Used in filter rows on athlete search, race search, leaderboard, etc.

```css
.filter-group select,
.filter-group input[type="number"] {
  padding: 0.45rem 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: var(--border-radius-sm);
  background: var(--white);
  font: inherit;
  color: var(--text-color);
  min-width: 140px;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.filter-group select:focus,
.filter-group input:focus {
  outline: none;
  border-color: var(--primary-color);
  box-shadow: 0 0 0 3px rgba(232,93,4,0.15);
}
```

### Search box (`.as-input`, `.as-search-box`)

Larger search inputs (athlete and race search) use the same focus pattern: orange border + 3px translucent orange focus ring. Placeholder text is `--text-lighter`.

### Segmented radio control (`.radio-chips`)

The standard filter control for mutually-exclusive options (discipline, sort, gender). Real `<input type="radio">` visually hidden, `<label>` is the hit target.

```html
<div class="filter-chip-group">
  <span class="filter-chip-label">Sort by</span>
  <div class="radio-chips">
    <input type="radio" id="disc-overall" name="disc" value="overall" checked>
    <label for="disc-overall">Overall</label>
    <input type="radio" id="disc-swim" name="disc" value="swim">
    <label for="disc-swim">Swim</label>
  </div>
</div>
```

States: inactive label = grey bg + muted text. Active (`:checked + label`) = white bg, orange text, subtle shadow. **Never** build segmented controls out of `<button>`s.

### Toggle switch (`.filter-toggle`)

A custom checkbox styled as a sliding pill (track + thumb). Use for boolean filters (e.g. "active only").

### Action buttons

| Class | Use | Style |
|-------|-----|-------|
| `.btn-search` | Primary form action (submit, apply) | Orange fill, white text, `border-radius: var(--border-radius-sm)` |
| `.btn-reset` | Secondary / cancel / clear | Text-only, `--text-light`, `--primary-color` on hover |
| `.btn-age-preset` | Small preset shortcuts (Junior, U23) | `1px` bordered, `--text-light`, orange border + text on hover/active |

There is no neutral "outlined dark" button; if you need one, it should be a new variant, not `.btn-reset` repurposed.

### Filter chip label

Section labels above filter rows (`.filter-chip-label`): `0.7rem` / 700, **uppercase**, letter-spacing `0.08em`, colour `--text-lighter`. Used to title each filter group.

---

## 17. Tags and Pills

Three distinct families - do not mix them.

### Generic pill (`.ptd-tag` and variants)

Small rounded tag for race programmes, age groups, count indicators.

```css
border-radius: 999px;
padding: 2px 10px;
font-size: 0.75rem;
font-weight: 600;
text-transform: uppercase;
letter-spacing: 0.04em;
```

Variants:
- `.ptd-tag--sc` - short course (default neutral)
- `.ptd-tag--lc` - long course (slightly different tint)
- `.ptd-tag--ag` - age group

### Classification pill (`.std-pill`)

Five-step difficulty / standard classification scale on race cards. Uses `.std-pill--1` through `.std-pill--5` for tinting.

### Condition pill (`.cond-pill`)

Five-step weather/condition pill (e.g. wetsuit, no wetsuit, hot). Uses `.cond-pill--1` through `.cond-pill--5`.

### Multi-stage badge (`.multi-stage-badge`)

Wraps an inline SVG and a label, used on race cards that are part of a multi-day or stage event. Lives at `base.css:2105`. Always inline-flex so the SVG aligns with the text baseline.

```html
<span class="multi-stage-badge">
  <svg width="12" height="12" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="2.5">
    <polyline points="..." />
  </svg>
  Stage 2 of 3
</span>
```

---

## 18. Hints / Tooltips

A label with a `(?)` icon that reveals an info popup on hover. Used for explaining rating columns, predicted ratings, etc.

```html
<span class="hint-wrapper">
  Predicted
  <span class="hint-icon" tabindex="0">?</span>
  <span class="hint-popup">
    Predicted rating uses the median of the last five performances...
  </span>
</span>
```

```css
.hint-wrapper { position: relative; }
.hint-icon { display: inline-flex; align-items: center; justify-content: center;
             width: 14px; height: 14px; border-radius: 50%;
             background: var(--border-color); color: var(--text-light);
             font-size: 0.65rem; font-weight: 700; cursor: help; }
.hint-popup { position: absolute; visibility: hidden; opacity: 0;
              transition: opacity var(--transition-fast);
              background: var(--navy); color: #fff;
              padding: 0.5rem 0.75rem; border-radius: var(--border-radius-sm);
              font-size: 0.75rem; line-height: 1.4; max-width: 240px;
              z-index: 10; box-shadow: var(--box-shadow-lg); }
.hint-wrapper:hover .hint-popup,
.hint-icon:focus + .hint-popup { visibility: visible; opacity: 1; }
.hint-popup::after { /* triangle pointer */ }
```

A right-anchored variant `.hint-right` flips popup origin so it does not clip at the page edge. Always pair with `tabindex="0"` so keyboard users can trigger it.

---

## 19. Charts (Chart.js)

Chart.js is loaded globally in `base.html` (no per-page imports needed). Wrappers actually used in templates:

| Class | Use |
|-------|-----|
| `.chart-container-compact` | Standard inline chart wrapper (race results, athlete history) |
| `.charts-grid`             | 2-column grid of compact charts; collapses to 1 column at `≤ 768px` |
| `.mini-chart`              | Small chart inside a grid cell |
| `.mini-chart.full-width`   | Stretches across the row |
| `.rating-mini`             | Mini rating sparkline; `data-label` attribute is rendered as a `::before` label |

There is no general-purpose `.chart-container` or `.chart-section` wrapper anymore (both were dead and removed during the cleanup); reuse `.chart-container-compact` for new chart wrappers, or define a page-specific class if it needs different sizing.

Chart colour conventions: line series use `--primary-color`; comparison / secondary series use `--navy`. Grid lines `--border-color` at low opacity. No coloured backgrounds on the chart area.

---

## 20. Loading and Empty States

| Class | Use |
|-------|-----|
| `.as-loading` | Shown during async athlete-search; centred italic text, `--text-light` |
| `.as-no-results` | Empty results for athlete search |
| `.country-leaderboard-wrap--loading` | Country page loading shimmer |
| `.country-empty` | "No results" for country leaderboard |
| `.pane-empty`, `.series-empty` | Empty state inside a tabbed card or series block |

Pattern for new empty states: a centered block with `padding: 2rem`, a one-line heading at `1rem` / 600 in `--text-color`, and a sub-line at `0.8rem` / 400 in `--text-light`. No icons.

---

## 21. Links and CTAs

### No directional arrows

Never append `→` (or `&rarr;`) to link text. RHS placement already communicates direction; an arrow is redundant.

```html
<a href="/athletes">Browse all</a>          <!-- correct -->
<a href="/athletes">Browse all →</a>        <!-- wrong -->
```

For inter-page detail navigation (back / forward), see [Chevrons](#22-icons-and-chevrons).

### RHS placement signals navigation

Within any container (card footer, section header, list row) navigational links go on the **right**. In `.page-section-head` use `justify-content: space-between` with the heading on the left and "see more" on the right.

### Light vs dark surfaces

| Surface | Default colour | Hover colour |
|---------|---------------|--------------|
| Light (`--white` / `--bg-color`) | `--primary-color` | `--primary-hover` |
| Dark (`--navy`) | `rgba(255,255,255,0.45)` (or `0.6` for footer ext links) | `--primary-color` (or `#fff` for footer) |

On a navy clickable card the title is white and transitions to orange on hover (whole card is the link).

---

## 22. Common Patterns

### Eyebrow label

Small orange all-caps label used in nav cards and the athlete hero. Sits above the heading as a category tag.

```html
<p class="eyebrow">Athletes</p>
<h3>Ratings, results, and leaderboards</h3>
```

### Section divider

`<hr class="rule">` between major sections. Margin `3rem 0 0`. Do not wrap sections in containers purely for visual separation.

### Athlete metadata row (`.athlete-meta`)

Dot-separated metadata below athlete names. `.meta-val` for numeric values (slightly larger/darker than the surrounding label text). Dots are injected via `::before`; do not add them in markup.

```html
<div class="athlete-meta">
  <span class="meta-item"><span class="flag">🇦🇺</span> Australia</span>
  <span class="meta-item">b. 1990</span>
  <span class="meta-item"><span class="meta-val">42</span> races</span>
  <span class="meta-item"><span class="meta-val">5</span> wins</span>
</div>
```

### Hero stats (right-side stat cluster)

Used on page heroes (athlete, race, country) to show 2-4 key numbers aligned to the right.

```css
.hero-stat-num { font-size: 1.4rem; font-weight: 800; line-height: 1; }
.hero-stat-lbl { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.08em;
                 color: rgba(255,255,255,0.55); }
.hero-stat-sep { width: 1px; height: 28px; background: rgba(255,255,255,0.15); }
```

---

## 23. Icons and Chevrons

Always use **inline SVG** for UI chrome (date, location, search, sort, chevron). Feather-style: `stroke="currentColor"`, `fill="none"`, `viewBox="0 0 24 24"`.

Sizes:
- `12px` for metadata icons.
- `16px` for interactive controls.
- `10px` (with `stroke-width="2.5"`) for inline chevrons in text links.

```html
<svg class="meta-icon" width="12" height="12" viewBox="0 0 24 24"
     fill="none" stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
  <circle cx="12" cy="10" r="3"/>
</svg>
```

### Chevron navigation

| Direction | Use case | `polyline points` |
|-----------|----------|--------------------|
| Left `‹` | Back to parent ("Back to event") | `15 18 9 12 15 6` |
| Right `›` | Forward to detail ("Full Results") | `9 18 15 12 9 6` |

Always pair the chevron with text. Never use a chevron alone.

```html
<a href="/event/123" class="back-link">
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
    <polyline points="15 18 9 12 15 6"/>
  </svg>
  Back to event
</a>
```

### Emoji policy

**Emoji are not used in the UI.** This is a hard rule, not a guideline. Emoji rendering varies wildly across platforms and OS versions, produces inconsistent sizing, and cannot be reliably styled with CSS. Every UI affordance (date, location, time, search, sort, info, link, status, success, warning, etc.) must be an inline SVG.

**The only permitted exception is country flag emoji** (🇦🇺, 🇬🇧, 🇺🇸) used to identify country in athlete metadata, podiums, and search results. This exception exists because:
- Country flags are a finite, well-defined set with broad cross-platform support.
- Building 250+ SVG flags would bloat the bundle significantly.
- Flags carry semantic identity (country) that abstract iconography cannot.

There are no other exceptions. Do not introduce emoji for "fun" decorative bullets, status indicators, section markers, or because an SVG feels heavyweight - if it lives in the rendered UI, it is an SVG (or a flag).

---

## 24. Responsive Design

Mobile-first in spirit; built desktop-first in practice. The current code uses ten distinct breakpoints. The target system is **three** canonical breakpoints, applied consistently. New components should be written against the target; existing components should migrate to it as they are touched.

### Canonical breakpoints

| Token name | Max width | Class of device | Primary intent |
|------------|----------:|-----------------|----------------|
| `--bp-tablet` | `1024px` | Tablets, narrow laptops | Tighten gutters, drop optional secondary content |
| `--bp-mobile` | `768px`  | Tablets portrait, large phones landscape | Stack two-column rows, full-width cards, hide non-essential hero stats |
| `--bp-phone`  | `480px`  | Phones portrait | Collapse meta grids to 1 column, hide labels in favour of icons where unambiguous, single-column footer |

These three are the **only** breakpoints permitted in new CSS. If you find yourself reaching for `600px` or `900px`, the design needs work, not a new breakpoint. The existing legacy breakpoints (`600`, `700`, `720`, `760`, `800`, `900`) should migrate to the nearest canonical one as files are touched.

The breakpoints are **max-width** queries: each `@media (max-width: Xpx)` block applies *down to* the next break. Mobile-first authoring (`min-width` queries) is acceptable for new files but the existing codebase uses `max-width`, so match that style when editing existing CSS to avoid mixing conventions in the same file.

### Token shifts at each breakpoint

The base scale is sized for desktop. At narrower widths the scale steps down to maintain rhythm without sacrificing legibility. **Body text never goes below `0.875rem` (14px)** on any device; data inside tables never below `0.8rem`.

| Token | Desktop (default) | `≤ 1024px` | `≤ 768px` | `≤ 480px` |
|-------|-------------------|-----------|-----------|-----------|
| Page horizontal gutter (`.page-container` padding) | `2rem` | `1.5rem` | `1rem`  | `0.75rem` |
| Card padding | `1.25rem` | `1.25rem` | `1rem`  | `0.875rem` |
| Section vertical rhythm (`<hr class="rule">` margin) | `3rem 0 0` | `2.5rem 0 0` | `2rem 0 0` | `1.5rem 0 0` |
| Card-to-card gap in grids | `1.25rem` | `1rem` | `0.75rem` | `0.625rem` |
| Form field height (`.filter-group input/select`) | `36px` | `36px` | `40px` | `44px` (touch target) |
| Page hero H1 (`.page-hero h1`) | `1.75rem` | `1.5rem` | `1.25rem` | `1.125rem` |
| Page hero padding | `1.25rem 0` | `1rem 0` | `0.875rem 0` | `0.75rem 0` |
| Section heading (`.page-section-head`) | `1.5rem` | `1.35rem` | `1.2rem` | `1.1rem` |
| Card heading (`h3` inside cards) | `1.1rem` | `1.05rem` | `1rem`   | `0.95rem` |
| Stat number (`.stat-number`, `.hero-stat-num`) | `1.5rem / 1.4rem` | `1.4rem` | `1.25rem` | `1.15rem` |
| Big rating number (athlete page) | `2rem` | `1.75rem` | `1.5rem` | `1.35rem` |
| Body / table cell | `0.875rem` | `0.875rem` | `0.875rem` | `0.85rem` |
| Meta / label | `0.7rem` | `0.7rem` | `0.7rem` | `0.7rem` |

Padding inside table cells follows the same step-down: desktop `0.6rem 1rem` → tablet `0.5rem 0.75rem` → mobile `0.45rem 0.625rem` → phone `0.4rem 0.5rem`.

### Touch targets

At `≤ 768px` every interactive control (buttons, nav links, filter chips, sortable headers, expandable row triggers) must have a tap target of at least **44×44px**. This often means adding vertical padding rather than enlarging text.

```css
@media (max-width: 768px) {
  .nav-link, .btn-search, .btn-reset, .btn-age-preset, .radio-chips label {
    min-height: 44px;
    display: inline-flex;
    align-items: center;
  }
}
```

### Component-by-component rules

**Header.** At `≤ 768px` shrink the logo to 24px, drop nav link letter-spacing to `0.05em`, allow the nav to scroll horizontally with `overflow-x: auto; scrollbar-width: none` rather than wrapping. Keep the header height at 52px so `position: sticky` offsets remain predictable. At `≤ 480px` hide the logo wordmark (keep the icon) so all six nav links fit; if they still overflow, they scroll.

**Page hero.** At `≤ 768px` the hero stacks: title on top (left-aligned), subtitle / hero stats wrap onto a second row (also left-aligned, smaller). `white-space: nowrap` on the H1 must be removed at this breakpoint to prevent overflow on long athlete names.

```css
@media (max-width: 768px) {
  .page-hero-inner { flex-direction: column; align-items: flex-start; gap: 0.5rem; }
  .page-hero h1 { white-space: normal; font-size: 1.25rem; }
  .page-hero-subtitle { text-align: left; }
}
```

**Hero stats.** At `≤ 768px` show the two most important stats only and hide the rest with `display: none` (use a `.hero-stat--secondary` modifier on the omittable stats). Do not shrink three stats to fit, omit the third.

**Section heading.** Border-left thins from 4px to 3px at `≤ 480px` to claw back horizontal space. The "see more" link on the right must remain on the same row; if it can't fit, drop the section sub-text instead.

**Cards.** Always full-width at `≤ 768px` (one card per row). Multi-column card grids collapse: 4-col → 2-col at `≤ 1024px`, 2-col → 1-col at `≤ 768px`. Card hover states are still styled but on touch devices `:hover` fires on tap; this is acceptable since the hover state is purely cosmetic (border colour) and never reveals new content.

**Forms / filters.** At `≤ 768px` filter rows stack vertically with each `.filter-group` becoming full-width. Text inputs and selects expand to `width: 100%`. Filter chip groups remain horizontal but allow horizontal scroll inside `.radio-chips` (`overflow-x: auto`). At `≤ 480px` the `.filter-chip-label` moves above the chips on its own line rather than left of them.

**Footer.** Already collapses to single column at `600px` in code; migrate to `≤ 768px` instead, keeping the same single-column layout.

**Profile photos.** Athlete page hero photo shrinks: `110px → 88px` at `≤ 768px`, `72px` at `≤ 480px`. List avatars (`.athlete-profile-img`) shrink: `48px → 40px` at `≤ 480px`.

**Charts.** Chart containers stay 100% width and let Chart.js redraw. Aspect ratio shifts from 16:9 (desktop) to 4:3 (mobile) so the chart remains tall enough to read. Legend moves below the chart. At `≤ 480px`, axis tick labels rotate 30° to fit narrow horizontal axes (configured per chart in JS).

**Pills and tags.** No size change; they're already small. At `≤ 480px` reduce horizontal padding from `10px` to `8px` to fit two pills on a row.

**Section dividers.** `<hr class="rule">` vertical margin steps down with the section rhythm token (see table above).

### What does **not** change responsively

- Colour palette, accent colour usage, dark/light surface conventions.
- Casing rules (uppercase headers stay uppercase; sentence case stays sentence case).
- Border radius, shadow tokens (always full-strength regardless of viewport).
- Icon stroke widths.
- The two-line split-cell pattern in race results (the cell shrinks but keeps both lines).
- The orange + arrow rating change indicator format.

### Hierarchy preservation under shrinkage

When stepping down sizes, **maintain at least a 1.25× ratio** between adjacent levels of hierarchy. If section headings drop to `1.1rem` on phone, body must drop no lower than `0.875rem` (ratio = 1.26). If you ever find that a heading and the body it introduces are within 10% of each other, the heading is too small.

---

## 25. Responsive Tables (mobile pattern)

Tables are the hardest responsive problem on the site. Horizontal scroll is the existing fallback but is awkward to use on phones, especially for the race results table (9 columns). The target pattern, applied at `≤ 768px`, is:

1. Show only the **identity columns** and the **headline number** on the collapsed row.
2. Make each row tappable to reveal a full-width detail row immediately below it, containing the omitted columns laid out as a stacked grid.
3. Indicate the toggle with a chevron in the leading cell, rotating on expand.

This keeps the canonical column alignment for the columns that remain, gives every other column space to breathe in the detail row, and avoids forcing horizontal scroll on the most common page on the site.

### Explicit column widths are mandatory

Every data table must declare explicit column widths. Auto-sized table layouts produce inconsistent column widths between rows, defeat the `tabular-nums` alignment, and cause layout jumps when async data lands.

The standard pattern uses `table-layout: fixed` and per-class widths:

```css
.results-table { table-layout: fixed; width: 100%; }
.results-table .position-col { width: 52px; }
.results-table .athlete-col  { width: 190px; }
.results-table .yob-col      { width: 90px; }
.results-table .time-col     { width: calc((100% - 332px) / 6); }
```

The `calc((100% - fixed) / n)` pattern lets fixed columns reserve their space and splits the remainder evenly across the data columns. Use it any time a table has a mix of fixed-width identity columns and a variable number of data columns.

### Recommended column widths (desktop baseline)

These are the canonical widths for the site's main tables. Use them when adding similar tables; deviate only with a reason.

| Column class | Desktop width | Notes |
|--------------|--------------:|-------|
| `.position-col`           | `52px` | Pos number, narrow |
| `.athlete-col`            | `190px` (results) / `220px` (leaderboard) | Includes avatar + name |
| `.yob-col`                | `90px`  | Year of birth |
| `.country-col`            | `110px` | Flag + name; flag-only at narrow widths |
| `.time-col`               | `calc((100% - sumOfFixed) / nDataCols)` | Even split across split times |
| `.rating-col`             | `calc((100% - sumOfFixed) / nDataCols)` | Same pattern as `.time-col` |
| `.change-col`             | `64px`  | Rating change arrow + number |
| `.expand-toggle-col`      | `36px`  | Mobile only; chevron cell on collapsed rows |

At `≤ 768px` the widths shift to:

| Column class | Mobile width |
|--------------|-------------:|
| `.expand-toggle-col` | `32px` |
| `.position-col`      | `36px` |
| `.athlete-col`       | `auto` (takes remaining space) |
| Headline number column (`.time-col` on Overall, or `.rating-col` on Overall) | `78px` |
| All other `.time-col` / `.rating-col` / `.yob-col` / `.country-col` | hidden via `display: none` on collapsed view |

### Markup pattern

The expandable row is a pair of `<tr>`s: a clickable summary row (`.row-summary`) and an immediately following detail row (`.row-detail`) that is hidden by default and revealed when the summary has `aria-expanded="true"`.

```html
<table class="sortable-table results-table responsive-expand">
  <colgroup>
    <col class="expand-toggle-col">
    <col class="position-col">
    <col class="athlete-col">
    <col class="yob-col">
    <col class="time-col">
    <col class="time-col">
    <col class="time-col">
    <col class="time-col">
    <col class="time-col">
    <col class="time-col headline-col">
  </colgroup>

  <thead>
    <tr>
      <th class="expand-toggle-col" aria-hidden="true"></th>
      <th class="position-col">Pos</th>
      <th class="athlete-col">Athlete</th>
      <th class="yob-col">YOB</th>
      <th class="time-col">Swim</th>
      <th class="time-col">T1</th>
      <th class="time-col">Bike</th>
      <th class="time-col">T2</th>
      <th class="time-col">Run</th>
      <th class="time-col headline-col">Overall</th>
    </tr>
  </thead>

  <tbody>
    <tr class="row-summary" aria-expanded="false" aria-controls="row-detail-12345">
      <td class="expand-toggle-col">
        <svg class="row-chevron" width="12" height="12" viewBox="0 0 24 24"
             fill="none" stroke="currentColor" stroke-width="2.5"
             aria-hidden="true">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </td>
      <td class="position-col"><span class="pos-circle pos-first">1</span></td>
      <td class="athlete-col">
        <img class="athlete-profile-img" src="..." alt="Alex Yee">
        <span class="athlete-name">Alex Yee</span>
      </td>
      <td class="yob-col">1998</td>
      <td class="time-col"><span class="time-cell-main">17:42</span><span class="time-cell-gap">+0:03</span></td>
      <td class="time-col"><span class="time-cell-main">0:31</span><span class="time-cell-gap">+0:01</span></td>
      <td class="time-col"><span class="time-cell-main">52:38</span><span class="time-cell-gap fastest">fastest</span></td>
      <td class="time-col"><span class="time-cell-main">0:24</span><span class="time-cell-gap">+0:00</span></td>
      <td class="time-col"><span class="time-cell-main">29:51</span><span class="time-cell-gap">+0:08</span></td>
      <td class="time-col headline-col"><span class="time-cell-main">1:41:06</span></td>
    </tr>

    <tr class="row-detail" id="row-detail-12345" hidden>
      <td colspan="10">
        <dl class="row-detail-grid">
          <div><dt>YOB</dt>     <dd>1998</dd></div>
          <div><dt>Swim</dt>    <dd>17:42 <span class="gap">+0:03</span></dd></div>
          <div><dt>T1</dt>      <dd>0:31 <span class="gap">+0:01</span></dd></div>
          <div><dt>Bike</dt>    <dd>52:38 <span class="gap fastest">fastest</span></dd></div>
          <div><dt>T2</dt>      <dd>0:24 <span class="gap">+0:00</span></dd></div>
          <div><dt>Run</dt>     <dd>29:51 <span class="gap">+0:08</span></dd></div>
        </dl>
      </td>
    </tr>
  </tbody>
</table>
```

### CSS

```css
/* Default desktop: details row never shown, toggle column hidden */
.responsive-expand .expand-toggle-col,
.responsive-expand .row-detail { display: none; }
.responsive-expand .row-summary { cursor: default; }

@media (max-width: 768px) {
  /* Reveal toggle column, hide non-essential columns */
  .responsive-expand .expand-toggle-col { display: table-cell; }
  .responsive-expand .yob-col,
  .responsive-expand .time-col:not(.headline-col),
  .responsive-expand .rating-col:not(.headline-col),
  .responsive-expand .country-col { display: none; }

  /* Summary row is now interactive */
  .responsive-expand .row-summary { cursor: pointer; }
  .responsive-expand .row-summary:hover { background: var(--highlight); }

  /* Chevron rotates when expanded */
  .responsive-expand .row-chevron {
    transition: transform var(--transition-fast);
    color: var(--text-light);
  }
  .responsive-expand .row-summary[aria-expanded="true"] .row-chevron {
    transform: rotate(90deg);
    color: var(--primary-color);
  }

  /* Detail row */
  .responsive-expand .row-summary[aria-expanded="true"] + .row-detail {
    display: table-row;
  }
  .responsive-expand .row-detail > td {
    background: #fafafa;
    padding: 0.75rem 1rem 1rem;
    border-top: none;
  }
  .row-detail-grid {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0.625rem 1rem;
  }
  .row-detail-grid > div { display: flex; justify-content: space-between; align-items: baseline; }
  .row-detail-grid dt {
    font-size: 0.7rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--text-lighter);
  }
  .row-detail-grid dd {
    margin: 0;
    font-size: 0.875rem; font-weight: 500;
    color: var(--text-color);
    font-variant-numeric: tabular-nums;
  }
  .row-detail-grid .gap { font-size: 0.7rem; color: var(--text-light); margin-left: 0.25rem; }
  .row-detail-grid .gap.fastest { color: var(--primary-color); font-weight: 600; }
}

@media (max-width: 480px) {
  /* Three columns is too dense; one row per metric */
  .row-detail-grid { grid-template-columns: 1fr; gap: 0.5rem; }
}
```

### JavaScript hook

The expand pattern needs a one-line vanilla JS handler. Place it in `base.js` (or wherever the table init lives) so it works for any table tagged `.responsive-expand`:

```js
document.addEventListener('click', (e) => {
  const summary = e.target.closest('.responsive-expand .row-summary');
  if (!summary) return;
  const expanded = summary.getAttribute('aria-expanded') === 'true';
  summary.setAttribute('aria-expanded', String(!expanded));
});

// Keyboard support
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const summary = e.target.closest('.responsive-expand .row-summary');
  if (!summary) return;
  e.preventDefault();
  summary.click();
});
```

Add `tabindex="0"` and `role="button"` to `.row-summary` so keyboard users can expand rows.

### Tables that should NOT use the expand pattern

Use horizontal scroll (the legacy fallback) for tables where every column carries comparable weight and there is no clear "headline" value to put on the summary row. Examples:

- The corrections table on race pages (every column is metadata; no obvious primary).
- Compact comparison tables that only have 3-4 columns to start with (just shrink padding instead).

For these, keep `overflow-x: auto` on the wrapping `.table-card` and ensure column widths are still explicit.

### Choosing the headline column

| Table | Identity columns (always shown) | Headline column |
|-------|---------------------------------|-----------------|
| Race results | Pos, Athlete | Overall time |
| Race ratings | Pos, Athlete | Overall rating |
| Leaderboard | Rank, Athlete | Active sort discipline rating |
| Athlete race history | Date, Race | Position |
| Athlete rating history | Date, Race | New rating + change |
| Country leaderboard | Rank, Athlete | Overall rating |

The headline column is the value the user is most likely scanning for in that context. Everything else lives in the expand drawer.

---

## 26. Accessibility Notes

- All interactive elements must have a visible focus state. Inputs use the orange ring (`box-shadow: 0 0 0 3px rgba(232,93,4,0.15)`); buttons inherit the default focus outline (do not remove without adding a replacement).
- The `.hint-icon` uses `tabindex="0"` and reveals its popup on `:focus`.
- Every clickable card with an overlay anchor must set `aria-label` on the overlay.
- Profile images must have `alt` text matching the athlete's name.
- Icons that are decorative (paired with adjacent text) should have `aria-hidden="true"`.
- The "ALL CAPS" treatment is applied via CSS `text-transform`, not by writing uppercase in the HTML, so screen readers receive the original casing.

---

## 27. Copy and Writing Style

These rules apply to user-facing copy in templates, button labels, empty-state messages, error text, and tooltips. They also apply to documentation and commit messages by default.

### Em-dashes are not used

**Never use the em-dash (`—`, U+2014) or en-dash (`–`, U+2013) in user-facing copy or in source files.** Use a regular hyphen (`-`) for inline asides, ranges (`2024-2025`), or compound modifiers, or rewrite the sentence with a comma, colon, parenthesis, or full stop.

```
correct:   The leaderboard updates nightly - usually around 02:00 UTC.
correct:   The leaderboard updates nightly. Usually around 02:00 UTC.
wrong:     The leaderboard updates nightly — usually around 02:00 UTC.
wrong:     The leaderboard updates nightly – usually around 02:00 UTC.
```

This rule also applies to anything Claude or another tool generates into the codebase (templates, documentation, comments, CSS comments, SQL comments). It is also restated at the top of `CLAUDE.md`. The only legitimate place an em-dash may appear is inside content quoted verbatim from an external source (e.g. a race name as published).

### Emoji are not used

See §23. Emoji are not permitted in templates, copy, or documentation. The single exception is country flag emoji used to mark a country.

### Casing in copy

- Section headings and card headings: **sentence case** (`Recent races`, not `Recent Races`).
- Eyebrow / label / table-header text: written in source as sentence case; uppercased visually via CSS `text-transform: uppercase` so screen readers receive normal casing.
- Button labels: sentence case (`Browse all`, `Apply filters`).
- Athlete names and event names: preserve the source case verbatim.

### Numbers and units

- Use thousands separators in display numbers ≥ 10,000 (`12,438` not `12438`).
- Use lowercase units (`km`, `mi`, `m`, `s`, `kg`). No space between number and a 1-letter unit (`5km`); space before multi-letter units in body copy (`5 hours`).
- Times use `hh:mm:ss` or `mm:ss` with leading zero on minutes (`02:14`, `1:23:45`).
- Percentages use `%` with no space (`72%`).

### Punctuation in copy

- No trailing punctuation in headings, button labels, or filter labels.
- Single space between sentences.
- Use straight quotes (`"` `'`), not curly quotes.
- Lists use sentence case items with no terminal punctuation unless multi-sentence.

---

## 28. Discrepancies & TODO

Items where the previous STYLE.md, the current code, or the markup disagree. These should be reconciled in a follow-up pass.

### Documentation now matches code (fixed in this revision)

- CSS variable names. The previous guide used `--orange / --text / --muted / --lighter / --border / --positive / --negative`; the code uses `--primary-color / --text-color / --text-light / --text-lighter / --border-color / --success-color / --error-color`. This guide now uses the code names.
- Section heading left-border thickness: code is **4px**, previous guide said 3px.
- Page hero H1: code is **1.75rem / 700**, previous guide said `2.25rem / 800`.
- Card hover shadow blur: code is **16px** (`--box-shadow-hover`), previous guide said 18px.
- Profile photo: previous guide claimed a "2px navy border" rule. **No such rule exists in the codebase.** The global treatment is shadow-only; the 2px border on the athlete hero is translucent white, scoped to that one component on the navy band.
- Footer was undocumented. Now in §6.
- Spacing / radius / shadow / transition tokens were undocumented. Now in §2.
- Forms, hints, charts, multi-stage badges, classification pills, condition pills, loading/empty states were all undocumented. Now in §17-20.
- Sticky header z-index is **1000** in code, not 100 as previously documented.

### Resolved in this pass

- ~~**Breakpoint sprawl.**~~ Done. All `@media (max-width: ...)` queries across the CSS are now `1024`, `768`, or `480`. The legacy values (`600`, `640`, `700`, `720`, `760`, `800`, `900`) all migrated to `768`. Where this brought two queries together in the same file (`index.css`, `athlete_search.css`), the blocks were merged.
- ~~**Profile-image consistency.**~~ Done. `.profile-img` and `.profile-img--on-dark` are now the canonical utility classes in `base.css`. All legacy avatar class names are aliased into the same rule. `.athlete-hero-img` was simplified to keep only its sizing/layout; visual treatment (ring + heavier shadow) flows from `--on-dark`. New components must use `.profile-img` directly. On-navy avatars get the translucent ring via `.profile-img--on-dark`.
- ~~**Dead CSS sweep.**~~ Done. Verified-unused class selectors were removed across all CSS files. Total deletion: ~600 lines. Notable removals:
  - **Stats bar / meta-grid families** (`.stats-bar`, `.stat-item`, `.stat-number`, `.stat-label`, `.meta-grid`, `.meta-label`, `.meta-value`): entire components removed; STYLE.md updated to drop those sub-sections. The hero-stats family on navy heroes survives as the only stat-cluster pattern.
  - **Five-column rating grid** (`.ratings-overview`, `.ratings-grid`, `.rating-box`, `.rating-discipline`, `.rating-rank`, `.rating-race`, `.ranking-world`, `.ranking-national`): never made it into templates. STYLE.md §10 no longer documents it.
  - **Inline position colour text classes** (`.pos-1st` / `.pos-first` / `.pos-2nd` / `.pos-second` / `.pos-3rd` / `.pos-third`): superseded by `.pos-circle.gold/.silver/.bronze`. STYLE.md §9 rewritten to show the actual template pattern.
  - **Position row tints** (`.position-cell`, `.position-gold`, `.position-silver`, `.position-bronze`).
  - **Medal badge duplicates** (`.lb-pos--1/2/3`, `.event-podium-pos--1/2/3`, `.as-lb-pos--1/2/3`): three identical sets of gold/silver/bronze background rules, all dead.
  - **Chart wrappers** (`.chart-section`, `.chart-container`): dead; templates use `.chart-container-compact`. STYLE.md §19 updated.
  - **Duplicated rating-change rules** (`.change-pos`, `.change-neg`): kept only `.rating-increase` / `.rating-decrease` / `.rating-neutral` (which Python emits via `css_class`).
  - **About-page hero family** (`.about-hero`, `.about-hero-copy`, `.about-eyebrow`, `.about-lede`, `.about-hero-panel`, `.about-bullets`): about page now uses the shared `.page-hero`.
  - **About-page blog teaser** (`.about-blog-card`, `.about-blog-eyebrow`, `.about-blog-title`, `.about-blog-teaser`, `.about-blog-link`, `.about-preview-blurb`, `.about-blurb-link`): replaced by shared `.blog-card`.
  - **`.data-table` selector**: every rule paired it with bare `table`. Templates never use the class, so it was redundant. Removed as a selector list cleanup.
  - **H2H comparison leftovers** (`.h2h-summary`, `.athlete-headers`, `.athlete-header`, `.vs-divider`, `.comparison-row`, `.comparison-label`, `.comparison-value`, `.athlete-tags`).
  - **Misc orphans**: `.section-title`, `.card-title`, `.back-link`, `.results-section-title-row`, `.other-edition*`, `.leaderboard-country-chip`, `.event-entry-count`, `.athlete-hero-flag-link`, `.sub-race-count`, `.time-cell`, `.time-behind`, `.age-table`, `.result-country/divider/yob`.

### Still in code (follow-ups)

1. **Stats bar vs hero stats consolidation** is now obsolete (stats-bar deleted); `.hero-stats` / `.hero-stat-num` / `.hero-stat-lbl` is the single remaining pattern.
2. **`.page-hero h1` `white-space: nowrap`** overflows on narrow screens for long page titles (athlete names with multiple given names). §24 specifies the override; needs implementing.
3. **Mixed border-radius** values in older code: a few selectors use hardcoded `8px` / `4px` / `12px` instead of the radius variables. Greppable and worth a sweep.
4. **No `.responsive-expand` table pattern in code yet.** §25 defines it as the target for narrow screens; current race results, leaderboard, etc. fall back to horizontal scroll. Implement on race results first as it's the most-trafficked wide table.
5. **Tables without explicit column widths.** Several smaller tables (rating history, comparison breakdowns, country leaderboard's secondary tables) currently rely on auto layout. §25 mandates explicit widths; sweep needed.
6. **Touch target sizes.** Filter chips, sortable headers, and nav links are below the 44px touch-target minimum at `≤ 768px` (see §24). Add the `min-height: 44px` rule when the responsive pass lands.
7. **Per-class avatar `border-radius` / `object-fit` declarations.** Now redundant since the `.profile-img` consolidated rule sets them, but still present in ~25 page CSS rules. Harmless but worth removing on the next pass through each file.
8. **Mobile token shifts.** §24 specifies a step-down for spacing, padding, and font sizes at each canonical breakpoint. Most of these reductions are not yet implemented in the existing `@media` blocks. This is the bulk of the remaining mobile pass.
9. **Inactive modifier class.** `.athlete-hero-img.inactive` is the only `.inactive` rule. Worth promoting to `.profile-img.inactive` so the modifier works on any avatar.
10. **Hint widget** (§18) is fully styled in CSS but no template currently renders it. The user is reintroducing the widget shortly; CSS and JS handler retained against that.

### Things deliberately NOT in this guide

- Page-specific layouts (event, athlete, country, series). Page CSS files are dense; pulling that detail into the guide would duplicate the source. Treat the page CSS files as the styling spec for that page; this guide covers only patterns that are or should be shared.
- JavaScript behaviour (sort, filter, search debouncing) - that belongs in a JS architecture doc, not the style guide.
