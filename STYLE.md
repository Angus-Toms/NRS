# Pro Tri Data - Style Guide

## Design Philosophy

Pro Tri Data is a **data-first** stats site. Every design decision serves the goal of making numerical information easy to read, compare, and explore. The principles in order of priority:

1. **Clarity over decoration** - if an element doesn't communicate something, remove it.
2. **Numerical readability** - numbers are the product; they must be legible and scannable at a glance.
3. **Consistency** - identical data types always look identical, regardless of context.
4. **Simplicity** - one typeface, a small colour palette, minimal chrome.

The aesthetic result is clean and editorial. It should feel like a well-designed reference tool, not a dashboard or a marketing site.

---

## Typography

Single typeface throughout: **Plus Jakarta Sans** (Google Fonts). No fallback display font.

```
https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400&display=swap
```

### Weight usage

| Weight | Use |
|--------|-----|
| 400 | Body copy, secondary metadata, descriptions |
| 500 | Table cell data, athlete names in context |
| 600 | Sub-headings, nav items, labels, card links |
| 700 | Section headings, column headers, card headings |
| 800 | Hero titles, large rating numbers, site name |

### Scale

| Role | Size |
|------|------|
| Site name | `1.1rem` / 800 |
| Hero H1 | `2.25rem` / 800 |
| Section heading (`h2.section-heading`) | `1.5rem` / 700 |
| Card heading | `1.1rem` / 700 |
| Table header | `0.7rem` / 600, uppercase, `letter-spacing: 0.08em` |
| Body / table cell | `0.875rem` / 400–600 |
| Metadata / secondary | `0.8rem` / 400 |
| Label / eyebrow | `0.75rem` / 600–700, uppercase |
| Fine print | `0.7rem` |

### Numeric formatting

All numbers use `font-variant-numeric: tabular-nums` so columns stay aligned. Large display ratings (the five-column rating boxes) use weight 800. Never use a condensed or different typeface for numbers - Jakarta Sans is legible at all sizes.

---

## Colour Palette

```css
:root {
  --page-bg:   #f4f4f2;   /* warm off-white page background */
  --card-bg:   #ffffff;   /* card and table surfaces */
  --navy:      #1a1a2e;   /* header, section bands, subheading chips */
  --orange:    #e85d04;   /* primary accent - links, highlights, active states */
  --orange-dk: #c44d03;   /* orange hover */
  --text:      #111827;   /* primary text */
  --muted:     #6b7280;   /* secondary text, metadata */
  --lighter:   #9ca3af;   /* tertiary, placeholders, disabled */
  --border:    #e5e7eb;   /* card borders, table dividers */
  --rule:      #d1d5db;   /* horizontal rules between sections */
  --positive:  #059669;   /* rating increases, positive changes */
  --negative:  #dc2626;   /* rating decreases, negative changes */
  --gold:      #f5c842;   /* 1st place */
  --silver:    #c8d8e0;   /* 2nd place */
  --bronze:    #d4a870;   /* 3rd place */
  --highlight: #fef3e8;   /* table row hover, soft orange tint */
}
```

### Colour rules

- **Orange** (`--orange`) is the single interactive accent. Use it for: links on hover, active nav items, the section heading left-border, card hover borders, eyebrow labels, dates, the "fastest" split highlight, rating change arrows.
- **Navy** (`--navy`) provides structural depth. Use it for: the site header, event card title bands, and table header rows. It should appear often enough to give the page a consistent dark anchor but never so much that it competes with content.
- **Green / red** are strictly for positive/negative rating change indicators. Do not use them for any other purpose.
- **Gold / silver / bronze** are used for podium position markers (circles in race results, coloured text in history tables). Use them consistently across both contexts.
- Backgrounds use a very light warm off-white (`--page-bg`) rather than pure white. Cards sit on top of this in pure white to create subtle lift without a heavy shadow.

---

## Layout

Max content width is **1100px**, centred, with `1.5rem` horizontal padding.

Sections are separated by `<hr class="rule">` (a 1px `--rule`-coloured line) with `3rem` vertical margin. This provides a clear rhythm without adding card-like containers around every section.

Section headings use a **3px orange left border** as the primary visual anchor:

```css
h2.section-heading {
  font-weight: 700;
  font-size: 1.5rem;
  border-left: 3px solid var(--orange);
  padding-left: 0.75rem;
  margin-bottom: 1.5rem;
}
```

Do not add eyebrow labels above section headings - the heading text is sufficient. Eyebrows are reserved for navigation cards where the category label is genuinely useful.

---

## Cards

Cards (white surface, 1px border, `border-radius: 8px`) are useful for grouping related content - tables, athlete profiles, event listings - but should not become the default wrapper for everything. A page that is entirely cards reads as a grid of boxes; prefer open layout with `<hr>` dividers between sections, and reserve cards for content that genuinely benefits from a contained surface.

### Hover state

Cards that are clickable show an orange border and a slightly deeper shadow on hover. The card title colour also transitions to orange. Never use a background-colour change as the primary hover signal - the border change is enough.

```css
.card:hover {
  border-color: var(--orange);
  box-shadow: 0 4px 18px rgba(0,0,0,0.09);
}
```

### Overlay link pattern

When a card must be fully clickable but also contain nested interactive elements (links, buttons), use an invisible overlay anchor rather than wrapping the whole card in `<a>`. Interactive children sit above the overlay via `z-index`.

```html
<div class="card" style="position:relative;">
  <a href="/target" class="card-overlay" aria-label="Card title"></a>
  <div style="position:relative; z-index:1; pointer-events:none;">
    <h3>Card title</h3>
  </div>
  <a href="/other" style="position:relative; z-index:1; pointer-events:all;">Nested link</a>
</div>
```

```css
.card-overlay { position: absolute; inset: 0; z-index: 0; }
```

### Navy bands within cards

Event cards and similar multi-part cards use a dark navy header band to anchor the primary title. The band contains white text (weight 600) with metadata at ~55% opacity. This pattern can be used any time a card has a clear header/body split and needs more visual weight than a plain heading provides.

---

## Tables

Tables are the primary data display surface. They live inside `.table-card` (white, bordered, `border-radius: 8px`, `overflow: hidden`).

### Header row

Table headers use a navy background with white text. This visually anchors each table, mirrors the site header, and makes the header/body boundary immediately clear without needing a heavy border.

```css
thead tr { background: var(--navy); }
thead th {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: rgba(255,255,255,0.6);
  padding: 0.6rem 1rem;
  text-align: left;
}
```

On sortable columns, hover lightens the column slightly (`rgba(255,255,255,0.07)` overlay) and the active sort column uses full white text with an orange arrow icon.

### Body rows

Alternating rows use a very subtle `#fafafa` stripe. Row hover uses `--highlight` (the soft orange tint). Bottom border removed from the last row to avoid a double-border at the card edge.

### Column alignment

- Position numbers: left-aligned, fixed narrow width
- Athlete names: left-aligned, flex
- Times and ratings: left-aligned (default) - **do not right-align**; the tabular-nums setting handles column width consistency
- Change indicators: inline after the value, same cell

### Sortable columns

Columns with a `.col-sortable` class show a sort arrow (`↕` / `↑` / `↓`) that is **hidden by default** and appears on hover. When actively sorted the icon turns orange and remains visible.

```css
.sort-icon { opacity: 0; transition: opacity 0.15s; }
.col-sortable:hover .sort-icon { opacity: 1; color: var(--muted); }
.col-sortable[data-dir] .sort-icon { opacity: 1; color: var(--orange); }
```

---

## Rating Numbers

The five-column rating grid (current ratings, world rankings, peak) uses large display numbers. These are the biggest numbers on the page and should feel confident and dense.

```
font-weight: 800
font-size: 2rem (overall) / 1.75rem (others)
font-variant-numeric: tabular-nums
```

Orange tint is used on the primary (overall) rating value. Other disciplines use `--text`. Peak ratings link to the race where the peak was achieved in small orange italic text below the number.

---

## Rating Changes

Rating change indicators appear inline after the rating number. The format is **arrow + number only** - no `+` or `−` prefix, as the arrow already communicates direction:

```
↑45    (not ↑+45 or +45)
↓8     (not ↓−8 or −8)
```

```css
.change-pos { color: var(--positive); font-weight: 500; font-size: 0.8rem; }
.change-neg { color: var(--negative); font-weight: 500; font-size: 0.8rem; }
```

In rating history tables the change indicator is a smaller inline `<span>` after the rating value. In the leaderboard it appears as its own cell.

---

## Race Results Table

### Split cells

Each split cell (Swim, T1, Bike, T2, Run, Overall) contains two lines:

1. The time itself - `font-weight: 500`, `0.875rem`, `--text`
2. The gap to the fastest time in that split - `font-weight: 400`, `0.7rem`, `--muted`

The fastest time in each split is highlighted in orange (`font-weight: 600`), with `"fastest"` as the gap line instead of a `+0:00`.

```html
<td>
  <span class="time-cell-main">52:41</span>
  <span class="time-cell-gap">+0:03</span>
</td>

<!-- fastest in this split -->
<td>
  <span class="time-cell-main fastest">52:38</span>
  <span class="time-cell-gap">fastest</span>
</td>
```

The Overall column uses slightly bolder weight (`font-weight: 700`) and `display: block` on the gap span to push it to a second line.

### Position indicators

In race results, positions 1–3 use filled colour circles (gold/silver/bronze) with dark text. Position 4+ uses plain muted text with no circle.

In race history tables, positions use coloured text only - no circles:

```css
.pos-first  { font-weight: 700; color: var(--gold);   }
.pos-second { font-weight: 600; color: var(--silver); }
.pos-third  { font-weight: 600; color: var(--bronze); }
.pos-other  { font-weight: 400; color: var(--muted);  }
```

### DNS / DNF / DQ

Non-finishes are displayed as plain italic text in `--lighter`. No background colour, no pill - the lighter italic treatment is sufficient to distinguish them from a numeric position without adding visual noise.

```css
.pos-dns,
.pos-dnf,
.pos-dq {
  font-weight: 400;
  font-style: italic;
  color: var(--lighter);
  font-size: 0.8rem;
}
```

---

## Podium Displays (Event Cards)

Each race within an event card shows a three-entry podium. Columns are separated using the **gap-as-divider** trick: `gap: 1px` on the flex container with `background: var(--border)`, so the gap colour shows through as a 1px divider line without affecting column widths.

```css
.event-podiums {
  display: flex;
  gap: 1px;
  background: var(--border); /* gap bleeds through as divider */
}
.podium-col {
  flex: 1;
  background: var(--card-bg);
}
```

Athlete names in podiums truncate with `text-overflow: ellipsis` - never wrap.

---

## Navigation Cards

The top-level nav cards (Athletes, Races, Compare, Leaderboard) use a **3px top border accent** that appears on hover:

```css
.nav-card {
  border-top: 3px solid transparent;
}
.nav-card:hover {
  border-top-color: var(--orange);
}
```

Each card has an eyebrow (category label), heading, short description, and a "View →" style link. The eyebrow is orange uppercase - it functions as a category tag, not decorative chrome.

---

## Responsive Design

Breakpoints are minimal. The layout is designed mobile-first where practical.

| Breakpoint | Change |
|------------|--------|
| `< 900px` | Event card body stacks vertically; info panel loses fixed width and right border, gains bottom border |
| `< 700px` | Nav cards grid collapses from 4 → 2 columns; hero stacks vertically |
| `< 500px` | Hero stats hide or collapse; rating grid may scroll horizontally |

Tables do not reflow on mobile - they scroll horizontally inside their container. This is preferable to collapsing columns, as column alignment is essential to reading split data.

---

## Shared Components (base.css)

The following patterns are defined once in `base.css` and used across leaderboard, athlete search, and any future filter UIs. Do not redefine them in page-specific CSS files.

### Segmented radio control (`.radio-chips`)

The standard filter control for mutually-exclusive options (discipline, sort order, gender). Uses real `<input type="radio">` elements visually hidden, with `<label>` as the hit target.

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

Active state: white background, orange text, subtle shadow. Inactive: grey bg, muted text. This is the only approved style for segmented filter controls - never use button-based chips.

### Action buttons

| Class | Use | Style |
|-------|-----|-------|
| `.btn-search` | Primary form action (submit, apply) | Orange fill, white text |
| `.btn-reset` | Secondary / cancel / clear | Text-only, muted, orange on hover |
| `.btn-age-preset` | Small preset shortcuts (Junior, U23) | Bordered, muted, orange on hover/active |

### Athlete ratings block (`.athlete-ratings`)

Used in leaderboard cards and search results. Discipline labels sit above their values in a grey pill. The active sort discipline uses `.rating-highlight` (orange for top order, green for hot/trending order via `.athlete-ratings-hot`).

```html
<div class="athlete-ratings">         <!-- add .athlete-ratings-hot for trending view -->
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

### Athlete metadata row (`.athlete-meta`)

Dot-separated metadata below athlete names. Use `.meta-val` for numeric values (races, wins) that should be slightly larger/darker than the surrounding label text.

```html
<div class="athlete-meta">
    <span class="meta-item">🇦🇺 Australia</span>
    <span class="meta-item">b. 1990</span>
    <span class="meta-item"><span class="meta-val">42</span> races</span>
    <span class="meta-item"><span class="meta-val">5</span> wins</span>
</div>
```

Dots are injected via `::before` CSS - do not add them manually in markup.

---

## Common Patterns

### Eyebrow label
Small orange all-caps label used in nav cards and the athlete hero. Sits above the heading as a category tag.
```html
<p class="eyebrow">Athletes</p>
<h3>Ratings, results, and leaderboards</h3>
```

### Pill / tag
Small rounded tag used for race programmes, count indicators, toggle buttons:
```css
border-radius: 999px;
padding: 2px 10px;
font-size: 0.75rem;
```

### Toggle (gender / filter)
Pill-shaped buttons in a flex row. Active state: orange fill, white text. Inactive: `--border` background, `--muted` text.

### Section divider
`<hr class="rule">` between major sections. Margin `3rem 0 0`. Do not wrap sections in containers purely for visual separation - the rule is sufficient.

### Sticky header
The site header is `position: sticky; top: 0; z-index: 100`. It uses `--navy` background with `border-bottom: 1px solid rgba(255,255,255,0.08)`. Active nav item has an orange underline border.

---

## Links and CTAs

### No directional arrows

Never append `→` (or `&rarr;`) to link text. The RHS placement of the link already communicates direction; an arrow is redundant visual noise.

```html
<!-- correct -->
<a href="/athletes">Browse all</a>

<!-- wrong -->
<a href="/athletes">Browse all →</a>
```

### RHS placement signals navigation

Within any container (card footer, section header, list row), navigational links belong on the **right-hand side**. This provides a consistent spatial cue that the link moves you forward/deeper, without needing an arrow.

In a section heading, use `justify-content: space-between` with the title on the left and the "see more" link on the right. In a card footer, use `justify-content: flex-end` or `text-align: right`. In a vertical flex card, the link should be `align-self: flex-end` or `display: block; text-align: right`.

### Light vs dark surfaces

| Surface | Default colour | Hover colour |
|---------|---------------|--------------|
| Light (white / `--page-bg`) | `--orange` | `--orange-dk` |
| Dark (`--navy` background) | `rgba(255,255,255,0.45)` | `--orange` |

On a navy card the primary title text is white; it transitions to orange on hover (the whole card is the link). Use the same pattern for any clickable navy surface.

```css
/* clickable navy card */
.card { background: var(--navy); border: 1px solid rgba(255,255,255,0.08); }
.card:hover { border-color: var(--orange); box-shadow: var(--box-shadow-hover); }
.card .title { color: #fff; transition: color var(--transition-fast); }
.card:hover .title { color: var(--orange); }
```

Never use `transform: translateY(...)` as a hover effect on cards - the border-colour change and shadow are sufficient.

---

## Icons

Always use SVG icons for UI chrome (date, location, search, etc.). **Never use emoji as icons.** Emoji rendering varies wildly across platforms and OS versions, produces inconsistent sizing, and cannot be reliably styled with CSS.

Use Feather-style inline SVGs (`stroke="currentColor"`, `fill="none"`) so icons inherit colour from their parent and can be tinted via `color`. Standard size is `12px` for metadata and `16px` for interactive controls.

```html
<!-- correct -->
<svg class="meta-icon" xmlns="http://www.w3.org/2000/svg" width="12" height="12"
     viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
  <circle cx="12" cy="10" r="3"></circle>
</svg>

<!-- wrong -->
<span>📍</span>
```

### Chevron navigation

Use inline SVG chevrons (`polyline points`) for all inter-page navigation links. Left-pointing for "back" links, right-pointing for "forward" / detail links. Always pair the chevron with a text label - never use the chevron alone.

| Direction | Use case | Points value |
|-----------|----------|--------------|
| Left `‹` | Back to parent (e.g. "Back to event") | `"15 18 9 12 15 6"` |
| Right `›` | Forward to detail (e.g. "Full Results", "View race") | `"9 18 15 12 9 6"` |

Standard size: `width="10" height="10" stroke-width="2.5"` for inline text links, `width="12" height="12"` for card footer links.

```html
<!-- back link -->
<a href="/event/{{ event_id }}" class="back-link">
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polyline points="15 18 9 12 15 6"></polyline>
    </svg>
    Back to event
</a>

<!-- forward link -->
<a href="/race/{{ race.race_id }}" class="results-link">
    Full Results
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polyline points="9 18 15 12 9 6"></polyline>
    </svg>
</a>
```

---

## Profile Photos

All circular profile images use a **2px navy border** and a subtle shadow. The navy ring ties the photo to the site's structural colour without introducing a third accent.

```css
.profile-img {
    border-radius: 50%;
    border: 2px solid var(--navy);
    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    object-fit: cover;
}
```

### Active vs retired athletes

Do not use coloured borders (green/red) to signal active/retired status - those colours are reserved exclusively for rating change indicators. Instead, retired athletes receive reduced opacity and a slight desaturation:

```css
.profile-img.inactive {
    opacity: 0.55;
    filter: grayscale(40%);
}
```

Active athletes use the default styling with no modifier class. The `.active` class should not exist.

### Podium avatars

Race and search podium displays use gold/silver/bronze `box-shadow` rings on position 1/2/3 photos. This is the one exception to the navy-border rule - the colour directly encodes finishing position and is contextually unambiguous.
