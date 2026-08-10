// --- Flag SVG helper ---
// Returns an <img> tag for the country flag, or empty string when code missing.
function flagImg(code, country, extraCls) {
    if (!code) return '';
    const cls = 'flag' + (extraCls ? ' ' + extraCls : '');
    const alt = String(country || code).replace(/"/g, '&quot;');
    return `<img src="/static/flags/${code}.svg" alt="${alt}" class="${cls}" loading="lazy">`;
}

// --- Sort icons: inject SVG chevrons into every th.sortable on load ---
function initSortIcons() {
    const SI_NEUTRAL = `<svg class="si-neutral" viewBox="0 0 10 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1,5.5 5,1.5 9,5.5"/><polyline points="1,8.5 5,12.5 9,8.5"/></svg>`;
    const SI_UP      = `<svg class="si-up"      viewBox="0 0 10 8"  fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1,6.5 5,1.5 9,6.5"/></svg>`;
    const SI_DOWN    = `<svg class="si-down"    viewBox="0 0 10 8"  fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1,1.5 5,6.5 9,1.5"/></svg>`;
    document.querySelectorAll('th.sortable').forEach(th => {
        if (th.querySelector('.sort-icon')) return; // already injected
        // Wrap all existing th content in a flex span so the icon centres properly
        const inner = document.createElement('span');
        inner.className = 'th-inner';
        while (th.firstChild) inner.appendChild(th.firstChild);
        const icon = document.createElement('span');
        icon.className = 'sort-icon';
        icon.innerHTML = SI_NEUTRAL + SI_UP + SI_DOWN;
        inner.appendChild(icon);
        th.appendChild(inner);
    });
}
document.addEventListener('DOMContentLoaded', initSortIcons);
if (document.readyState !== 'loading') { initSortIcons(); }

// --- Download modal: format picker for table download buttons ---
// Buttons carry data-download-url; the modal appends &format=csv|json.
function initDownloadButtons() {
    if (!document.querySelector('.btn-download')) return;

    const modal = document.createElement('div');
    modal.className = 'dl-modal';
    modal.hidden = true;
    modal.innerHTML = `
        <div class="dl-modal-backdrop"></div>
        <div class="dl-modal-panel" role="dialog" aria-modal="true" aria-label="Download table">
            <div class="dl-modal-title">Download table</div>
            <a class="dl-modal-option" data-format="csv">CSV<span>Opens in Excel and Sheets</span></a>
            <a class="dl-modal-option" data-format="json" target="_blank">JSON<span>Raw values with metadata</span></a>
        </div>`;
    document.body.appendChild(modal);

    function open(btn) {
        const url = btn.dataset.downloadUrl;
        const sep = url.includes('?') ? '&' : '?';
        modal.querySelectorAll('.dl-modal-option').forEach(a => {
            a.href = `${url}${sep}format=${a.dataset.format}`;
        });
        modal.hidden = false;
    }
    const close = () => { modal.hidden = true; };

    // Delegated so buttons inside AJAX-swapped partials (race page) still work
    document.addEventListener('click', e => {
        const btn = e.target.closest('.btn-download');
        if (btn) open(btn);
    });
    modal.querySelector('.dl-modal-backdrop').addEventListener('click', close);
    modal.querySelectorAll('.dl-modal-option').forEach(a => a.addEventListener('click', close));
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && !modal.hidden) close();
    });
}
document.addEventListener('DOMContentLoaded', initDownloadButtons);
if (document.readyState !== 'loading') { initDownloadButtons(); }

// --- Hint popup ---
function toggleHint(icon) {
    const popup = icon.nextElementSibling;
    const isShown = popup.classList.contains('show');
    
    // Close all other hints
    document.querySelectorAll('.hint-popup.show').forEach(p => {
        p.classList.remove('show');
    });
    
    // Toggle this hint
    if (!isShown) {
        popup.classList.add('show');
    }
}

// Close hints when clicking outside
document.addEventListener('click', function(event) {
    if (!event.target.closest('.hint-wrapper')) {
        document.querySelectorAll('.hint-popup.show').forEach(popup => {
            popup.classList.remove('show');
        });
    }
});

// --- Utils ---
function formatTime(seconds) {
    const absSeconds = Math.abs(seconds);
    const hours = Math.floor(absSeconds / 3600);
    const minutes = Math.floor((absSeconds % 3600) / 60);
    const secs = Math.floor(absSeconds % 60);
    
    const sign = seconds < 0 ? '-' : '';
    
    if (hours > 0) {
        return `${sign}${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    } 
    
    return `${sign}${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

function toggleCollapse(cardOrToggle) {
    // Accept either the card element itself (race page tables) or the toggle button inside it
    const card = cardOrToggle.classList.contains('collapsible-card')
        ? cardOrToggle
        : cardOrToggle.closest('.collapsible-card');
    const content = card.querySelector('.collapsible-content');
    content.classList.toggle('collapsed');
    // Update the toggle button text wherever it is
    const btn = card.querySelector('.collapse-toggle') ?? cardOrToggle;
    btn.textContent = content.classList.contains('collapsed') ? '+' : '−';
}

function parseTime(timeStr) {
    // Convert time string (HH:MM:SS or MM:SS) to seconds for sorting
    if (!timeStr || timeStr === 'DNF' || timeStr === 'DQ') return Infinity;
    const parts = timeStr.split(':').map(Number);
    if (parts.length === 3) {
        return parts[0] * 3600 + parts[1] * 60 + parts[2];
    } else if (parts.length === 2) {
        return parts[0] * 60 + parts[1];
    }
    return parseFloat(timeStr) || 0;
}

function sortTable(table, column, asc = true) {
    const tbody = table.querySelector('tbody');
    const allRows = Array.from(tbody.querySelectorAll('tr'));
    // Sub-race rows use a colspan and have fewer cells - exclude from sort and reinsert after
    const subRows = allRows.filter(r => r.classList.contains('sub-race-row'));
    const rows    = allRows.filter(r => !r.classList.contains('sub-race-row'));

    // Skip if no data
    if (rows.length === 1 && rows[0].querySelector('.no-data')) return;
    
    const sortType = table.querySelectorAll('th')[column].getAttribute('data-sort');
    
    rows.sort((a, b) => {
        const aCell = a.cells[column];
        const bCell = b.cells[column];
        
        let aVal = aCell.getAttribute('data-value') || aCell.textContent.trim();
        let bVal = bCell.getAttribute('data-value') || bCell.textContent.trim();
        
        // Handle different data types
        if (sortType === 'time') {
            aVal = parseTime(aVal);
            bVal = parseTime(bVal);
        } else if ( sortType === 'rating') {
            aVal = parseFloat(aVal);
            bVal = parseFloat(bVal);
        } else if (sortType === 'position') {
            const specialValues = { 'NC': 9995, 'LAP': 9996, 'DNF': 9997, 'DQ': 9998, 'DNS': 9999 };
            aVal = specialValues[aVal] !== undefined ? specialValues[aVal] : parseInt(aVal) || Infinity;
            bVal = specialValues[bVal] !== undefined ? specialValues[bVal] : parseInt(bVal) || Infinity;
        } else if (sortType === 'race-id' || sortType === 'date' || sortType === 'string') {
            // String comparison for dates and IDs
            aVal = aVal.toString();
            bVal = bVal.toString();
        } else {
            // Try numeric, fallback to string
            const aNum = parseFloat(aVal);
            const bNum = parseFloat(bVal);
            if (!isNaN(aNum) && !isNaN(bNum)) {
                aVal = aNum;
                bVal = bNum;
            }
        }
        
        if (aVal < bVal) return asc ? -1 : 1;
        if (aVal > bVal) return asc ? 1 : -1;
        return 0;
    });
    
    // Reappend sorted rows, then re-attach each sub-race row after its parent.
    // Parent rows are identified by the toggle button's data-parent matching the sub-row's data-parent.
    rows.forEach(row => tbody.appendChild(row));
    subRows.forEach(sub => {
        const parentId = sub.dataset.parent;
        const parentRow = parentId && Array.from(tbody.querySelectorAll('tr')).find(
            r => r.querySelector(`.sub-race-toggle[data-parent="${parentId}"]`)
        );
        if (parentRow) parentRow.after(sub);
        else tbody.appendChild(sub);
    });
}

function getJSON(id) {
    const el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Vertical offset (px) of year labels above the x-axis on zebra-banded charts.
// Kept just off the axis line so the labels don't collide with it.
const YEAR_LABEL_OFFSET = 10;

// Minimum horizontal px a year label needs before we start skipping labels
// (a 4-digit year at 11px is ~28px; leave room so neighbours don't touch).
const YEAR_LABEL_MIN_SPACING = 46;

// How many years to advance between drawn labels so they don't overlap on
// long careers or narrow screens. Snaps to a "round" stride (1/2/5/10...) so
// the labelled years stay human-friendly (e.g. every 5th year).
function yearLabelStep(numYears, axisWidthPx) {
    const maxLabels = Math.max(1, Math.floor(axisWidthPx / YEAR_LABEL_MIN_SPACING));
    if (numYears <= maxLabels) return 1;
    const raw = numYears / maxLabels;
    for (const s of [1, 2, 5, 10, 20, 25, 50, 100]) if (s >= raw) return s;
    return 100;
}

// Chart.js plugin: alternating year bands + centered year labels.
// Only fires on time-axis charts; no-ops on linear/category axes.
const yearBandsPlugin = {
    id: 'yearBands',
    _bands(chart) {
        const xScale = chart.scales.x;
        if (!xScale || xScale.type !== 'time') return null;
        const { chartArea: { top, bottom, left, right } } = chart;
        const startYear = new Date(xScale.min).getFullYear();
        const endYear   = new Date(xScale.max).getFullYear();
        const bands = [];
        for (let year = startYear; year <= endYear; year++) {
            const xStart = Math.max(left,  xScale.getPixelForValue(new Date(year,     0, 1)));
            const xEnd   = Math.min(right, xScale.getPixelForValue(new Date(year + 1, 0, 1)));
            if (xEnd > xStart) bands.push({ year, xStart, xEnd });
        }
        return { bands, top, bottom, left, right };
    },
    // Draw shaded bands before data
    beforeDraw(chart) {
        const info = this._bands(chart);
        if (!info) return;
        const { bands, top, bottom } = info;
        const ctx = chart.ctx;
        ctx.save();
        for (const { year, xStart, xEnd } of bands) {
            if (year % 2 === 0) {
                ctx.fillStyle = 'rgba(0, 0, 0, 0.04)';
                ctx.fillRect(xStart, top, xEnd - xStart, bottom - top);
            }
        }
        ctx.restore();
    },
    // Draw year labels after data, clipped inside chart area
    afterDraw(chart) {
        const info = this._bands(chart);
        if (!info) return;
        const { bands, top, bottom, left, right } = info;
        const ctx = chart.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.rect(left, top, right - left, bottom - top);
        ctx.clip();
        ctx.font = '11px sans-serif';
        ctx.fillStyle = 'rgba(130, 130, 130, 0.9)';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        const step = yearLabelStep(bands.length, right - left);
        for (const { year, xStart, xEnd } of bands) {
            if (year % step !== 0) continue;  // round strides: 2000, 2005, ...
            ctx.fillText(String(year), (xStart + xEnd) / 2, bottom - YEAR_LABEL_OFFSET);
        }
        ctx.restore();
    }
};
Chart.register(yearBandsPlugin);

// Generate up to ~maxCount "nice" round tick values spanning [min, max].
// Steps snap to 1 / 2 / 2.5 / 5 x 10^n so axis labels read as round numbers
// (e.g. #20, #40, #60) instead of chart.js's data-fitted values (#19, #50, #81).
function niceTickValues(min, max, maxCount = 6) {
    if (!isFinite(min) || !isFinite(max) || max <= min) return null;
    const rawStep = (max - min) / maxCount;
    const base = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const f = rawStep / base;
    const nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    const step = nf * base;
    const out = [];
    for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) {
        out.push(Math.round(v * 1e6) / 1e6);
    }
    return out;
}

// chart.js afterBuildTicks handler: replace auto ticks with nice round values.
// opts.includeMin forces the axis min (e.g. rank #1) to appear as a tick.
function applyNiceTicks(scale, opts = {}) {
    const vals = niceTickValues(scale.min, scale.max, opts.maxCount || 6);
    if (!vals) return;
    if (opts.includeMin && vals[0] !== scale.min) vals.unshift(scale.min);
    scale.ticks = vals.map(value => ({ value }));
}
// --- Section help tours ---
// Guided explainers opened from the "i" icon next to section titles
// (help_icon macro in partials/_icons.html). The page blurs and each step
// spotlights one part of the section with a short explanation. Steps whose
// selector matches nothing on the current page are skipped, so conditional
// columns (rankings, 1y change, course conditions...) just drop out.
// `column: true` expands a th target's spotlight to the whole table column.
const _pill = c => `<span class="std-pill std-pill--${c}">${c[0].toUpperCase() + c.slice(1)}</span>`;
const _ratingBasics = 'Every athlete carries an ELO-style rating per discipline, updated after each race. Higher is better. There is no fixed minimum or maximum - ratings only mean something relative to other athletes in the same gender, course and category.';
const _tierText = `Tiers grade the field strength against all races of the same gender and course: ${_pill('beginner')} bottom 30%, ${_pill('novice')} next 30%, ${_pill('intermediate')} 60th-85th percentile, ${_pill('advanced')} 85th-95th, and ${_pill('expert')} the top 5%.`;

const HELP_TOURS = {
    'athlete-ratings': [
        { sel: '.ratings-table th.rt-rating', column: true, title: 'Rating',
          text: _ratingBasics },
        { sel: '.ratings-table th.rt-rank', column: true, title: 'Rankings',
          text: 'Where this rating places the athlete among all rated athletes - world and national rank. Click through to see the full leaderboard.' },
        { sel: '.ratings-table th.rt-peak', column: true, title: 'Peak rating',
          text: 'The highest rating the athlete has ever held in each discipline, and the race where they hit it.' },
        { sel: '.ratings-table th.rt-best', column: true, title: 'Best race',
          text: 'The single race that earned the biggest rating gain - the athlete\'s standout performance in each discipline.' },
        { sel: '.ratings-table th.rt-change', column: true, title: '1y change',
          text: 'Rating change over the last 12 months, with a sparkline of the trajectory. Green means improving, red means declining.' },
    ],
    'athlete-upcoming': [
        { sel: '.upcoming-races-table', title: 'Upcoming races',
          text: 'Races this athlete is entered in, with the finishing position and splits the model expects from their current rating. These are estimates made before the race - they don\'t know the course or the conditions on the day, so actual results will differ.' },
    ],
    'athlete-results': [
        { sel: '.table-section.results-table', title: 'Race results',
          text: 'Every recorded pro result with full splits. The fastest split of the race is highlighted; every other time shows the gap behind it.' },
        { sel: '.table-section.results-table .std-pill', title: 'Race tiers',
          text: _tierText },
        { sel: '.table-section.results-table th.sortable', title: 'Sorting',
          text: 'Click any column header to sort the table - by date, position, or any split.' },
    ],
    'athlete-rating-history': [
        { sel: '.table-section.rating-table', title: 'Rating history',
          text: 'The athlete\'s rating after each race. The small green or red number is what the result gained or lost: beating expectation earns points, underperforming costs them.' },
    ],
    'race-standards': [
        { sel: '.ratings-table th.rt-rating', column: true, title: 'Race standard',
          text: 'A weighted average of the pre-race ratings of everyone who finished each leg - a measure of how strong the field actually was. Stronger athletes count for more, and only athletes who raced the leg are included. Higher means a deeper, stronger field.' },
        { sel: '.ratings-table th.rt-tier', column: true, title: 'Tier',
          text: _tierText },
        { sel: '.ratings-table th.rt-rank', column: true, title: 'Race rank',
          text: 'Where this race\'s field ranks all-time among races of the same gender and course. Click through for the full race leaderboard.' },
        { sel: '.ratings-table th.rt-best', column: true, title: 'Best performance',
          text: 'The athlete who gained the most rating points in each discipline - the day\'s standout performance.' },
        { sel: '.ratings-table th.rt-cond', column: true, title: 'Course conditions',
          text: 'How fast the course raced compared with typical times over this distance. Flags slow swims, fast bikes and so on - from currents, terrain, heat or measurement.' },
    ],
    'race-results': [
        { sel: '#results-view .race-table-section', title: 'Results',
          text: 'The official finishing order with full splits. The fastest split of the race is highlighted; every other time shows the gap behind it.' },
        { sel: '.race-view-toggle', title: 'Predictions vs Results',
          text: 'Results are what actually happened. Predictions show what the model expected before the race, based purely on pre-race ratings. Toggle between them to see who over- or under-performed.' },
        { sel: '#add-prediction-btn', title: 'Add an athlete',
          text: 'Search for any athlete who wasn\'t in this race and the model predicts where they would have finished, calibrated to this race\'s course and conditions.' },
    ],
    'race-rating-changes': [
        { sel: '.race-table-section .rating-table', title: 'Ratings & changes',
          text: _ratingBasics + ' The small green or red number next to each rating is what this race changed it by: beating expectation earns points, underperforming costs them.' },
    ],
    'upcoming-standards': [
        { sel: '.ratings-table th.rt-rating', column: true, title: 'Race standard',
          text: 'A weighted average of the start list\'s current ratings, per discipline - how strong the assembled field is. Higher means a deeper, stronger field.' },
        { sel: '.ratings-table .std-pill', title: 'Tier',
          text: _tierText },
    ],
    'upcoming-predictions': [
        { sel: '#predictions-view .race-table-section, .race-table-section', title: 'Predicted results',
          text: 'The model\'s expected splits and finishing order, from each athlete\'s current rating. These are pre-race estimates - they don\'t know the course or the weather, and actual results will differ.' },
    ],
};

function openHelpTour(key) {
    const steps = (HELP_TOURS[key] || [])
        .map(s => ({ ...s, el: document.querySelector(s.sel) }))
        .filter(s => s.el);
    if (!steps.length) return;

    const tour = document.createElement('div');
    tour.className = 'help-tour';
    tour.innerHTML = `
        <div class="help-blur" data-side="t"></div>
        <div class="help-blur" data-side="b"></div>
        <div class="help-blur" data-side="l"></div>
        <div class="help-blur" data-side="r"></div>
        <div class="help-corner"></div>
        <div class="help-corner"></div>
        <div class="help-corner"></div>
        <div class="help-corner"></div>
        <div class="help-spot"></div>
        <div class="help-card" role="dialog" aria-modal="true">
            <button type="button" class="help-card-close" aria-label="Close">&times;</button>
            <div class="help-card-title"></div>
            <div class="help-card-text"></div>
            <div class="help-card-footer">
                <span class="help-card-count"></span>
                <span class="help-card-btns">
                    <button type="button" class="help-btn" data-nav="-1">Back</button>
                    <button type="button" class="help-btn help-btn-primary" data-nav="1">Next</button>
                </span>
            </div>
        </div>`;
    document.body.appendChild(tour);
    document.body.classList.add('help-tour-open');

    const panels = {};
    tour.querySelectorAll('.help-blur').forEach(p => panels[p.dataset.side] = p);
    const corners = tour.querySelectorAll('.help-corner');
    const spot = tour.querySelector('.help-spot');
    const card = tour.querySelector('.help-card');
    let idx = 0;

    // Padded spotlight rect for the current step. th targets with column:true
    // expand to the full table column: no vertical padding, and horizontal
    // padding is clipped to the table so the window never spills onto the page
    // background beside an edge column.
    function targetRect(step) {
        const r = step.el.getBoundingClientRect();
        if (step.column && step.el.tagName === 'TH') {
            const t = step.el.closest('table').getBoundingClientRect();
            return {
                left: Math.max(r.left - 6, t.left), right: Math.min(r.right + 6, t.right),
                top: t.top, bottom: t.bottom,
            };
        }
        return { left: r.left - 8, right: r.right + 8, top: r.top - 8, bottom: r.bottom + 8 };
    }

    function position() {
        const step = steps[idx];
        const r = targetRect(step);
        const left = Math.max(0, r.left), right = Math.min(innerWidth, r.right);
        const top = Math.max(0, r.top);
        // A target taller than the screen (a whole results table) is clipped so
        // the window keeps a normal height with the card below it, rather than
        // stretching over the entire viewport.
        const ch = card.offsetHeight;
        const bottom = Math.min(r.bottom, Math.max(top + 80, innerHeight - ch - 28));
        const w = right - left, h = bottom - top;
        // Match the window's corner rounding to the component it frames.
        const rad = Math.min(step.column ? 8 : (parseFloat(getComputedStyle(step.el).borderRadius) + 8 || 10), w / 2, h / 2);
        // Four blurred panels leave a rectangular hole. (A single full-screen
        // panel with a clip-path hole hits a Chromium stale-backdrop bug.)
        panels.t.style.cssText = `top:0;left:0;right:0;height:${Math.max(0, top)}px`;
        panels.b.style.cssText = `top:${bottom}px;left:0;right:0;bottom:0`;
        panels.l.style.cssText = `top:${top}px;left:0;width:${left}px;height:${h}px`;
        panels.r.style.cssText = `top:${top}px;left:${right}px;right:0;height:${h}px`;
        // Small corner pieces round the hole: each is a rad x rad square
        // clipped to the area outside the corner arc, rotated into place.
        const cornerClip = `path("M0 0H${rad}A${rad} ${rad} 0 0 0 0 ${rad}Z")`;
        [[left, top], [right - rad, top], [right - rad, bottom - rad], [left, bottom - rad]].forEach(([x, y], i) => {
            corners[i].style.cssText =
                `top:${y}px;left:${x}px;width:${rad}px;height:${rad}px;` +
                `clip-path:${cornerClip};transform:rotate(${i * 90}deg)`;
        });
        spot.style.cssText = `top:${top}px;left:${left}px;width:${w}px;height:${h}px;border-radius:${rad}px`;
        // Card below the spotlight if it fits, otherwise above; clamped to viewport.
        const cw = Math.min(card.offsetWidth, innerWidth - 24);
        let cardTop = bottom + 12;
        if (cardTop + ch > innerHeight - 12) cardTop = Math.max(12, top - ch - 12);
        const cardLeft = Math.min(Math.max(12, left + (right - left) / 2 - cw / 2), innerWidth - cw - 12);
        card.style.top = `${cardTop}px`;
        card.style.left = `${cardLeft}px`;
    }

    function show(i) {
        idx = i;
        const step = steps[idx];
        card.querySelector('.help-card-title').textContent = step.title;
        card.querySelector('.help-card-text').innerHTML = step.text;
        card.querySelector('.help-card-count').textContent = steps.length > 1 ? `${idx + 1} of ${steps.length}` : '';
        card.querySelector('[data-nav="-1"]').style.visibility = idx > 0 ? 'visible' : 'hidden';
        card.querySelector('[data-nav="1"]').textContent = idx < steps.length - 1 ? 'Next' : 'Done';
        // Tall targets (whole tables) scroll to their top - centering them would
        // land mid-table with the section title out of view. The extra nudge
        // clears the sticky header.
        const tall = step.el.getBoundingClientRect().height > innerHeight * 0.7;
        step.el.scrollIntoView({ block: tall ? 'start' : 'center', behavior: 'instant' });
        if (tall) scrollBy(0, -70);
        position();
    }

    function close() {
        tour.remove();
        document.body.classList.remove('help-tour-open');
        removeEventListener('resize', position);
        removeEventListener('scroll', position, true);
        removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') close(); }

    addEventListener('resize', position);
    addEventListener('scroll', position, true);
    addEventListener('keydown', onKey);
    tour.addEventListener('click', e => {
        const nav = e.target.closest('[data-nav]');
        if (nav) {
            const next = idx + Number(nav.dataset.nav);
            if (next >= steps.length) close(); else show(next);
            return;
        }
        if (e.target.closest('.help-card-close') || e.target.closest('.help-blur')) close();
    });

    show(0);
}

// Delegated so icons inside AJAX-swapped partials (race page) still work.
document.addEventListener('click', e => {
    const btn = e.target.closest('.help-icon[data-help]');
    if (btn) openHelpTour(btn.dataset.help);
});
