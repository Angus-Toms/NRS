// Wrapped in an IIFE so re-executing this script (after turbo-lite partial
// nav swaps the main content) doesn't collide with previous top-level `const`
// bindings. Each re-run gets a fresh lexical scope; old Chart.js instances
// whose canvases were detached become unreachable and get GC'd.
(function() {
// ---------- Turbo-lite partial navigation for the mode switcher -------------
// Bound FIRST so any later init error in this file doesn't leave the pills
// falling back to full-document navigation. Idempotent via window guard.
if (!window._ptdAthleteNavBound) {
    window._ptdAthleteNavBound = true;
    document.addEventListener('click', async (e) => {
        const link = e.target.closest('a[data-ptd-nav="athlete"]');
        if (!link) return;
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        e.preventDefault();
        const url = link.getAttribute('href');
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error(`status ${resp.status}`);
            const html = await resp.text();
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const newHero    = doc.querySelector('#athlete-ajax-hero');
            const newContent = doc.querySelector('#athlete-ajax-content');
            const currHero    = document.querySelector('#athlete-ajax-hero');
            const currContent = document.querySelector('#athlete-ajax-content');
            if (!newHero || !newContent || !currHero || !currContent) {
                throw new Error('ajax targets missing');
            }
            currHero.replaceWith(newHero);
            currContent.replaceWith(newContent);
            document.title = doc.title;
            history.pushState({}, '', url);
            const currScript = document.querySelector('script[src*="js/athlete.js"]');
            if (currScript) {
                const fresh = document.createElement('script');
                for (const a of currScript.attributes) fresh.setAttribute(a.name, a.value);
                currScript.remove();
                document.body.appendChild(fresh);
            }
        } catch (err) {
            console.warn('athlete partial nav failed, falling back to reload:', err);
            window.location.href = url;
        }
    });
    window.addEventListener('popstate', () => { window.location.reload(); });
}

// Shared constants used by both rankings and ratings charts
const DISC_COLORS = {
    overall:    '#E87722',  // orange
    swim:       '#38bdf8',  // sky blue
    bike:       '#a78bfa',  // violet
    run:        '#34d399',  // emerald
    transition: '#f472b6',  // pink
};
const DISC_LABELS = { overall: 'Overall', swim: 'Swim', bike: 'Bike', run: 'Run', transition: 'Transition' };

// --- Ranking charts ---
const worldRankingsData    = getJSON('world-rankings-chart-data');
const nationalRankingsData = getJSON('national-rankings-chart-data');

let activeWorldDisc = 'overall';
let activeNatDisc   = 'overall';
let mainWorldChart  = null;
let mainNatChart    = null;

// One tooltip element per ranking type, same style as the ratings tooltip
function _makeRankTooltipEl(id) {
    const existing = document.getElementById(id);
    if (existing) return existing;
    const el = document.createElement('div');
    el.id = id;
    Object.assign(el.style, {
        position: 'fixed', pointerEvents: 'none', opacity: '0',
        background: '#1a1a2e', borderRadius: '10px',
        fontFamily: "'Plus Jakarta Sans', sans-serif",
        border: '1px solid rgba(255,255,255,0.1)',
        boxShadow: '0 6px 24px rgba(0,0,0,0.4)',
        transition: 'opacity 0.12s', width: '220px', zIndex: '100',
        overflow: 'hidden',
    });
    document.body.appendChild(el);
    return el;
}

const worldRankTooltipEl = _makeRankTooltipEl('world-ranking-tooltip');
const natRankTooltipEl   = _makeRankTooltipEl('national-ranking-tooltip');

let _worldRankHovered = false;
let _natRankHovered   = false;

worldRankTooltipEl.addEventListener('mouseenter', () => { _worldRankHovered = true; });
worldRankTooltipEl.addEventListener('mouseleave', () => { _worldRankHovered = false; worldRankTooltipEl.style.opacity = '0'; worldRankTooltipEl.style.pointerEvents = 'none'; });
natRankTooltipEl.addEventListener('mouseenter',   () => { _natRankHovered = true; });
natRankTooltipEl.addEventListener('mouseleave',   () => { _natRankHovered = false; natRankTooltipEl.style.opacity = '0'; natRankTooltipEl.style.pointerEvents = 'none'; });

function _positionTooltip(tooltipEl, chart, tooltip) {
    const rect   = chart.canvas.getBoundingClientRect();
    const tipW   = tooltipEl.offsetWidth || 230;
    const caretX = rect.left + tooltip.caretX;
    const caretY = rect.top  + tooltip.caretY;
    if (caretX + tipW + 16 <= window.innerWidth) {
        // Fits to the right of the cursor - tooltip is outside the canvas, no blocking
        tooltipEl.style.left = (caretX + 16) + 'px';
        tooltipEl.style.top  = (caretY - 50) + 'px';
    } else {
        // Near right edge: go above the point so we don't sit over the canvas
        const tipH = tooltipEl.offsetHeight || 150;
        let left = Math.max(8, caretX - tipW / 2);
        if (left + tipW > window.innerWidth - 8) left = window.innerWidth - tipW - 8;
        tooltipEl.style.left = left + 'px';
        tooltipEl.style.top  = Math.max(8, caretY - tipH - 12) + 'px';
    }
    tooltipEl.style.pointerEvents = 'auto';
    tooltipEl.style.opacity = '1';
}

function _fillRankTooltip(tooltipEl, d, disc, rankLabel) {
    const color  = DISC_COLORS[disc];
    const label  = DISC_LABELS[disc];
    const date   = new Date(d.x).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    const isDNF  = d.status && DNF_STATUSES.has(d.status);

    const raceLink   = `<a href="/race/${d.race_id}" style="color:#fff;text-decoration:none;font-weight:700;font-size:13px;line-height:1.2;display:block" onmouseover="this.style.color='#E87722'" onmouseout="this.style.color='#fff'">${d.race_name}</a>`;
    const statusBadge = isDNF
        ? `<span style="display:inline-block;background:rgba(220,38,38,0.2);color:#f87171;font-size:10px;font-weight:700;padding:1px 5px;border-radius:3px;margin-left:6px">${d.status}</span>`
        : '';

    // Build time rows - transition shows T1+T2, others show discipline time
    let timeRows = '';
    if (!isDNF) {
        if (disc === 'transition') {
            const mkRow = (lbl, t, diff) => {
                if (!t) return '';
                const timeR = `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">${lbl} time</span><span style="color:#fff;font-size:13px;font-weight:600">${fmtTime(t)}</span></div>`;
                const diffR = diff == null ? '' : diff > 0
                    ? `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">${lbl} behind</span><span style="color:#f87171;font-size:12px;font-weight:500">${fmtDiff(diff)}</span></div>`
                    : `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">${lbl} behind</span><span style="color:#5eead4;font-size:12px;font-weight:500">Leader</span></div>`;
                return timeR + diffR;
            };
            timeRows = mkRow('T1', d.t1_s, d.t1_diff) + mkRow('T2', d.t2_s, d.t2_diff);
        } else if (d.time_s) {
            timeRows = `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">${label} time</span><span style="color:#fff;font-size:13px;font-weight:600">${fmtTime(d.time_s)}</span></div>`;
            timeRows += d.diff_s > 0
                ? `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">Behind leader</span><span style="color:#f87171;font-size:12px;font-weight:500">${fmtDiff(d.diff_s)}</span></div>`
                : d.diff_s != null
                    ? `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">Behind leader</span><span style="color:#5eead4;font-size:12px;font-weight:500">Leader</span></div>`
                    : '';
        }
    }

    tooltipEl.innerHTML = `
        <div style="padding:7px 12px;border-bottom:1px solid rgba(255,255,255,0.08)">
            <div style="margin-bottom:1px">${raceLink}</div>
            <div style="display:flex;justify-content:space-between;align-items:center">
                <span style="font-size:11px;color:rgba(255,255,255,0.4)">${date}</span>
                ${statusBadge}
            </div>
        </div>
        <div style="padding:10px 12px;${timeRows ? 'border-bottom:1px solid rgba(255,255,255,0.08);' : ''}">
            <div style="font-size:22px;font-weight:800;color:${color};line-height:1">#${d.y}${fmtRankChange(d.rank_chg)}</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.4);margin-top:3px">${label} ${rankLabel}</div>
        </div>
        ${timeRows ? `<div style="padding:8px 12px;display:flex;flex-direction:column;gap:4px">${timeRows}</div>` : ''}
    `;
}

function worldRankingTooltip(context) {
    const { chart, tooltip } = context;
    if (tooltip.opacity === 0) { if (!_worldRankHovered) { worldRankTooltipEl.style.opacity = '0'; worldRankTooltipEl.style.pointerEvents = 'none'; } return; }
    _fillRankTooltip(worldRankTooltipEl, tooltip.dataPoints[0].raw, activeWorldDisc, 'world ranking');
    _positionTooltip(worldRankTooltipEl, chart, tooltip);
}

function natRankingTooltip(context) {
    const { chart, tooltip } = context;
    if (tooltip.opacity === 0) { if (!_natRankHovered) { natRankTooltipEl.style.opacity = '0'; natRankTooltipEl.style.pointerEvents = 'none'; } return; }
    _fillRankTooltip(natRankTooltipEl, tooltip.dataPoints[0].raw, activeNatDisc, 'national ranking');
    _positionTooltip(natRankTooltipEl, chart, tooltip);
}

function buildMainRankingChart(disc, type) {
    const isWorld  = type === 'world';
    const data     = isWorld ? worldRankingsData[disc] : nationalRankingsData[disc];
    const canvasId = `${type}-rankings-main-canvas`;
    const hex      = DISC_COLORS[disc];

    if (isWorld) { if (mainWorldChart) mainWorldChart.destroy(); }
    else         { if (mainNatChart)   mainNatChart.destroy();   }

    const canvas        = document.getElementById(canvasId);
    const delayPerPoint = Math.min(600 / Math.max(data.length, 1), 30);
    const prevY = (ctx) => ctx.index === 0
        ? ctx.chart.scales.y.getPixelForValue(data[0]?.y ?? 1)
        : ctx.chart.getDatasetMeta(0).data[ctx.index - 1]?.getProps(['y'], true).y;

    const chart = new Chart(canvas, {
        type: 'line',
        data: {
            datasets: [{
                data,
                borderColor: hex, backgroundColor: 'transparent',
                pointBackgroundColor: hex, pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: hex, pointHoverBorderWidth: 2,
                borderWidth: 2, pointRadius: 3, pointHoverRadius: 5,
                fill: false, tension: 0.3, clip: false,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false },
            animation: {
                x: { type: 'number', easing: 'linear', duration: delayPerPoint, from: NaN,
                     delay: (ctx) => { if (ctx.type !== 'data' || ctx.xStarted) return 0; ctx.xStarted = true; return ctx.index * delayPerPoint; } },
                y: { type: 'number', easing: 'linear', duration: delayPerPoint, from: prevY,
                     delay: (ctx) => { if (ctx.type !== 'data' || ctx.yStarted) return 0; ctx.yStarted = true; return ctx.index * delayPerPoint; } },
            },
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false, external: isWorld ? worldRankingTooltip : natRankingTooltip },
            },
            scales: {
                x: { type: 'time', grid: { display: false }, ticks: { display: false } },
                y: {
                    reverse: true, min: 1, beginAtZero: false,
                    grid: { color: 'rgba(0,0,0,0.05)' },
                    ticks: { color: '#999', callback: v => '#' + v },
                }
            }
        }
    });

    if (isWorld) mainWorldChart = chart;
    else         mainNatChart   = chart;
}

function buildMiniRankingChart(disc, type) {
    const data   = type === 'world' ? worldRankingsData[disc] : nationalRankingsData[disc];
    const canvas = document.getElementById(`${type}-rankings-mini-${disc}`);
    if (!canvas) return;
    new Chart(canvas, {
        type: 'line',
        data: {
            datasets: [{
                data, borderColor: DISC_COLORS[disc], borderWidth: 1.5,
                pointRadius: 0, tension: 0.3, fill: false, backgroundColor: 'transparent', clip: false,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false }, yearBands: false },
            scales: {
                x: { type: 'time', display: false },
                y: { reverse: true, display: false, grid: { display: false }, border: { display: false } }
            },
            animation: false,
        }
    });
}

function switchRankingDisc(disc, type) {
    const isWorld = type === 'world';
    if (disc === (isWorld ? activeWorldDisc : activeNatDisc)) return;

    // Destroy existing mini charts for this type
    ['overall', 'swim', 'bike', 'run', 'transition'].forEach(d => {
        const existing = Chart.getChart(document.getElementById(`${type}-rankings-mini-${d}`));
        if (existing) existing.destroy();
    });

    // Swap active class within the correct card
    const card = document.getElementById(`${type}-rankings-main-canvas`).closest('.collapsible-card');
    card.querySelectorAll('.ratings-mini').forEach(el => el.classList.remove('active'));
    card.querySelector(`.ratings-mini[data-disc="${disc}"]`).classList.add('active');

    if (isWorld) activeWorldDisc = disc;
    else         activeNatDisc   = disc;

    const heading = document.getElementById(`${type}-rankings-section-title`);
    if (heading) heading.textContent = `${isWorld ? 'World' : 'National'} Rankings - ${DISC_LABELS[disc]}`;

    buildMainRankingChart(disc, type);
    ['overall', 'swim', 'bike', 'run', 'transition'].forEach(d => buildMiniRankingChart(d, type));
}

// Init ranking charts
if (worldRankingsData) {
    buildMainRankingChart('overall', 'world');
    ['overall', 'swim', 'bike', 'run', 'transition'].forEach(d => buildMiniRankingChart(d, 'world'));
    buildMainRankingChart('overall', 'national');
    ['overall', 'swim', 'bike', 'run', 'transition'].forEach(d => buildMiniRankingChart(d, 'national'));
}

function toggleNotableResults(button, targetId) {
    const dropdown = document.getElementById(targetId);
    if (!dropdown) return;

    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', !expanded);
    dropdown.hidden = expanded;
    button.classList.toggle('open', !expanded);
}

// Add click handlers to sortable headers.
// (Previously wrapped in DOMContentLoaded; runs immediately because athlete.js
// is at end of body and we also re-run it after partial nav when DOMContent
// is long fired.)
(function initSortHeaders() {
    const tables = document.querySelectorAll('table.sortable-table');
    if (!tables.length) return;
    tables.forEach(table => {
        const headers = table.querySelectorAll('th.sortable');
        headers.forEach((header, index) => {
            header.addEventListener('click', () => {
                const isAsc = header.classList.contains('asc');
                headers.forEach(h => h.classList.remove('asc', 'desc'));
                if (isAsc) {
                    header.classList.add('desc');
                    sortTable(table, index, false);
                } else {
                    header.classList.add('asc');
                    sortTable(table, index, true);
                }
                const tbody = table.querySelector('tbody');
                if (tbody) _restripeTable(tbody);
            });
        });
    });
})();

// Ratings chart ---------------------------------------------------------------
const ratingsData = getJSON('ratings-chart-data');
let activeDisc = 'overall';
let mainRatingsChart = null;

// Helpers
function fmtTime(s) {
    if (!s || s <= 0) return null;
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.round(s % 60);
    return h > 0
        ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`
        : `${m}:${String(sec).padStart(2,'0')}`;
}
function fmtDiff(s) {
    const t = fmtTime(s);
    return t ? `+${t}` : null;
}
// SVG chevron helpers - matches STYLE.md inline SVG convention
function _chevronSvg(up, size, col) {
    const pts = up ? '18 15 12 9 6 15' : '6 9 12 15 18 9';
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${col}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-bottom:1px"><polyline points="${pts}"></polyline></svg>`;
}
// Chevron + number, no +/- prefix (STYLE.md convention)
function fmtChangeArrow(n) {
    if (n == null) return '';
    const col = n >= 0 ? '#5eead4' : '#f87171';  // teal-300 / red-400 - legible on dark navy
    return `<span style="color:${col};font-size:11px;font-weight:600;white-space:nowrap">${_chevronSvg(n >= 0, 11, col)}${Math.abs(n)}</span>`;
}
// World rank change: positive = improved (moved up). Lower rank number = better.
function fmtRankChange(n) {
    if (n == null || n === 0) return '';
    const col = n > 0 ? '#5eead4' : '#f87171';
    return `<span style="color:${col};font-size:10px;font-weight:600;margin-left:3px;white-space:nowrap">${_chevronSvg(n > 0, 10, col)}${Math.abs(n)}</span>`;
}
const DNF_STATUSES = new Set(['DNF', 'LAP', 'NC', 'DNS', 'DQ']);

// Shared HTML tooltip element - pointerEvents: auto for sticky hover
const ratingsTooltipEl = (() => {
    const existing = document.getElementById('ratings-chart-tooltip');
    if (existing) return existing;
    const el = document.createElement('div');
    el.id = 'ratings-chart-tooltip';
    Object.assign(el.style, {
        position: 'fixed', pointerEvents: 'none', opacity: '0',
        background: '#1a1a2e', borderRadius: '10px',
        fontFamily: "'Plus Jakarta Sans', sans-serif",
        border: '1px solid rgba(255,255,255,0.1)',
        boxShadow: '0 6px 24px rgba(0,0,0,0.4)',
        transition: 'opacity 0.12s', width: '240px', zIndex: '100',
        overflow: 'hidden',
    });
    document.body.appendChild(el);
    return el;
})();

// Track whether cursor is over the tooltip so we don't hide it prematurely
let _tooltipHovered = false;
ratingsTooltipEl.addEventListener('mouseenter', () => { _tooltipHovered = true; });
ratingsTooltipEl.addEventListener('mouseleave', () => {
    _tooltipHovered = false;
    ratingsTooltipEl.style.opacity = '0';
    ratingsTooltipEl.style.pointerEvents = 'none';
});

function ratingsExternalTooltip(context) {
    const { chart, tooltip } = context;
    if (tooltip.opacity === 0) {
        if (!_tooltipHovered) { ratingsTooltipEl.style.opacity = '0'; ratingsTooltipEl.style.pointerEvents = 'none'; }
        return;
    }

    const d      = tooltip.dataPoints[0].raw;
    const disc   = activeDisc;
    const color  = DISC_COLORS[disc];
    const label  = disc.charAt(0).toUpperCase() + disc.slice(1);
    const date   = new Date(d.x).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    const isDNF  = d.status && DNF_STATUSES.has(d.status);
    const noTime = disc === 'transition';

    // Rating change: skip for DNF/LAP/NC
    const changeHtml = isDNF ? '' : fmtChangeArrow(d.change);

    // Status badge for non-finished results
    const statusBadge = isDNF
        ? `<span style="display:inline-block;background:rgba(220,38,38,0.2);color:#f87171;font-size:10px;font-weight:700;padding:1px 5px;border-radius:3px;margin-left:6px">${d.status}</span>`
        : '';

    // Time/diff rows: transition shows T1+T2, others show discipline time
    let timeRows = '';
    if (!isDNF) {
        if (noTime) {
            // transition disc - show T1 and T2 individually
            const mkRow = (lbl, t, diff) => {
                if (!t) return '';
                const timeR = `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">${lbl} time</span><span style="color:#fff;font-size:13px;font-weight:600">${fmtTime(t)}</span></div>`;
                const diffR = diff == null ? '' : diff > 0
                    ? `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">${lbl} behind</span><span style="color:#f87171;font-size:12px;font-weight:500">${fmtDiff(diff)}</span></div>`
                    : `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">${lbl} behind</span><span style="color:#5eead4;font-size:12px;font-weight:500">Leader</span></div>`;
                return timeR + diffR;
            };
            timeRows = mkRow('T1', d.t1_s, d.t1_diff) + mkRow('T2', d.t2_s, d.t2_diff);
        } else if (d.time_s) {
            timeRows = `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">${label} time</span><span style="color:#fff;font-size:13px;font-weight:600">${fmtTime(d.time_s)}</span></div>`;
            timeRows += d.diff_s > 0
                ? `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">Behind leader</span><span style="color:#f87171;font-size:12px;font-weight:500">${fmtDiff(d.diff_s)}</span></div>`
                : d.diff_s != null
                    ? `<div style="display:flex;justify-content:space-between;align-items:baseline"><span style="color:rgba(255,255,255,0.5);font-size:11px">Behind leader</span><span style="color:#5eead4;font-size:12px;font-weight:500">Leader</span></div>`
                    : '';
        }
    }

    // World rank section
    const rankHtml = d.world_rank ? `
        <div style="text-align:right">
            <div style="font-size:18px;font-weight:700;color:#fff;line-height:1">#${d.world_rank}${fmtRankChange(d.world_rank_chg)}</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.4);margin-top:2px">World rank</div>
        </div>` : '';

    // Race name as link
    const raceLink = `<a href="/race/${d.race_id}" style="color:#fff;text-decoration:none;font-weight:700;font-size:13px;line-height:1.2;display:block" onmouseover="this.style.color='#E87722'" onmouseout="this.style.color='#fff'">${d.race_name}</a>`;

    ratingsTooltipEl.innerHTML = `
        <div style="padding:7px 12px;border-bottom:1px solid rgba(255,255,255,0.08)">
            <div style="margin-bottom:1px">${raceLink}</div>
            <div style="display:flex;justify-content:space-between;align-items:center">
                <span style="font-size:11px;color:rgba(255,255,255,0.4)">${date}</span>
                ${statusBadge}
            </div>
        </div>
        <div style="padding:10px 12px;${timeRows ? 'border-bottom:1px solid rgba(255,255,255,0.08);' : ''}display:flex;justify-content:space-between;align-items:flex-end">
            <div>
                <div style="font-size:22px;font-weight:800;color:${color};line-height:1">${d.y} ${changeHtml}</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.4);margin-top:3px">${label} rating</div>
            </div>
            ${rankHtml}
        </div>
        ${timeRows ? `<div style="padding:8px 12px;display:flex;flex-direction:column;gap:4px">${timeRows}</div>` : ''}
    `;

    _positionTooltip(ratingsTooltipEl, chart, tooltip);
}

function buildMainChart(disc) {
    const canvas = document.getElementById('ratings-main-canvas');
    if (mainRatingsChart) mainRatingsChart.destroy();

    const hex  = DISC_COLORS[disc];
    const data = ratingsData[disc];
    const delayPerPoint = Math.min(600 / Math.max(data.length, 1), 30);
    const prevY = (ctx) => ctx.index === 0
        ? ctx.chart.scales.y.getPixelForValue(data[0]?.y ?? 0)
        : ctx.chart.getDatasetMeta(0).data[ctx.index - 1]?.getProps(['y'], true).y;

    mainRatingsChart = new Chart(canvas, {
        type: 'line',
        data: {
            datasets: [{
                data,
                borderColor: hex,
                backgroundColor: 'transparent',
                pointBackgroundColor: hex,
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: hex,
                pointHoverBorderWidth: 2,
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 5,
                fill: false,
                tension: 0.3,
                clip: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false },
            animation: {
                x: { type: 'number', easing: 'linear', duration: delayPerPoint, from: NaN,
                     delay: (ctx) => { if (ctx.type !== 'data' || ctx.xStarted) return 0; ctx.xStarted = true; return ctx.index * delayPerPoint; } },
                y: { type: 'number', easing: 'linear', duration: delayPerPoint, from: prevY,
                     delay: (ctx) => { if (ctx.type !== 'data' || ctx.yStarted) return 0; ctx.yStarted = true; return ctx.index * delayPerPoint; } },
            },
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false, external: ratingsExternalTooltip },
            },
            scales: {
                x: { type: 'time', grid: { display: false }, ticks: { display: false } },
                y: { beginAtZero: false, grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { color: '#999' } }
            }
        }
    });
}

function buildMiniChart(disc) {
    const canvas = document.getElementById(`ratings-mini-${disc}`);
    new Chart(canvas, {
        type: 'line',
        data: {
            datasets: [{
                data: ratingsData[disc],
                borderColor: DISC_COLORS[disc],
                borderWidth: 1.5,
                pointRadius: 0,
                tension: 0.3,
                fill: false,
                backgroundColor: 'transparent',
                clip: false,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false },
                yearBands: false,  // disable global year-band plugin on sparklines
            },
            scales: {
                x: { type: 'time', display: false },
                y: { display: false, beginAtZero: false, grid: { display: false }, border: { display: false } }
            },
            animation: false,
        }
    });
}

const ratingsHeading = document.getElementById('ratings-section-title');

function updateRatingTitle(disc) {
    if (ratingsHeading) ratingsHeading.textContent = `Rating History - ${DISC_LABELS[disc]}`;
}

function switchRatingDisc(disc) {
    if (disc === activeDisc) return;

    ['overall', 'swim', 'bike', 'run', 'transition'].forEach(d => {
        const existing = Chart.getChart(document.getElementById(`ratings-mini-${d}`));
        if (existing) existing.destroy();
    });

    document.querySelectorAll('.ratings-mini').forEach(el => el.classList.remove('active'));
    document.querySelector(`.ratings-mini[data-disc="${disc}"]`).classList.add('active');

    activeDisc = disc;
    updateRatingTitle(disc);
    buildMainChart(disc);
    ['overall', 'swim', 'bike', 'run', 'transition'].forEach(buildMiniChart);
}

// Init
if (ratingsData) {
    updateRatingTitle('overall');
    buildMainChart('overall');
    ['overall', 'swim', 'bike', 'run', 'transition'].forEach(buildMiniChart);
}

// % Behind Leader chart -------------------------------------------------------
const pctBehindData = getJSON('pct-behind-chart-data');
let activePctDisc = 'overall';
let mainPctChart  = null;

const pctTooltipEl = (() => {
    const existing = document.getElementById('pct-behind-tooltip');
    if (existing) return existing;
    const el = document.createElement('div');
    el.id = 'pct-behind-tooltip';
    Object.assign(el.style, {
        position: 'fixed', pointerEvents: 'none', opacity: '0',
        background: '#1a1a2e', borderRadius: '10px',
        fontFamily: "'Plus Jakarta Sans', sans-serif",
        border: '1px solid rgba(255,255,255,0.1)',
        boxShadow: '0 6px 24px rgba(0,0,0,0.4)',
        transition: 'opacity 0.12s', width: '220px', zIndex: '100',
        overflow: 'hidden',
    });
    document.body.appendChild(el);
    return el;
})();

let _pctHovered = false;
pctTooltipEl.addEventListener('mouseenter', () => { _pctHovered = true; });
pctTooltipEl.addEventListener('mouseleave', () => { _pctHovered = false; pctTooltipEl.style.opacity = '0'; pctTooltipEl.style.pointerEvents = 'none'; });

function pctExternalTooltip(context) {
    const { chart, tooltip } = context;
    if (tooltip.opacity === 0) { if (!_pctHovered) { pctTooltipEl.style.opacity = '0'; pctTooltipEl.style.pointerEvents = 'none'; } return; }
    const d     = tooltip.dataPoints[0].raw;
    const color = DISC_COLORS[activePctDisc];
    const label = DISC_LABELS[activePctDisc];
    const date  = new Date(d.x).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    const raceLink = `<a href="/race/${d.race_id}" style="color:#fff;text-decoration:none;font-weight:700;font-size:13px;line-height:1.2;display:block" onmouseover="this.style.color='#E87722'" onmouseout="this.style.color='#fff'">${d.race_name}</a>`;
    const pctDisplay = d.y === 0
        ? `<span style="color:#5eead4;font-size:22px;font-weight:800;line-height:1">Leader</span>`
        : `<span style="color:${color};font-size:22px;font-weight:800;line-height:1">+${d.y.toFixed(1)}%</span>`;
    pctTooltipEl.innerHTML = `
        <div style="padding:7px 12px;border-bottom:1px solid rgba(255,255,255,0.08)">
            <div style="margin-bottom:1px">${raceLink}</div>
            <span style="font-size:11px;color:rgba(255,255,255,0.4)">${date}</span>
        </div>
        <div style="padding:10px 12px">
            ${pctDisplay}
            <div style="font-size:11px;color:rgba(255,255,255,0.4);margin-top:3px">${label} % behind leader</div>
        </div>
    `;
    _positionTooltip(pctTooltipEl, chart, tooltip);
}

function buildMainPctBehindChart(disc) {
    const canvas = document.getElementById('pct-behind-main-canvas');
    if (mainPctChart) mainPctChart.destroy();
    const hex  = DISC_COLORS[disc];
    const data = pctBehindData[disc];
    const delayPerPoint = Math.min(600 / Math.max(data.length, 1), 30);
    const prevY = (ctx) => ctx.index === 0
        ? ctx.chart.scales.y.getPixelForValue(data[0]?.y ?? 0)
        : ctx.chart.getDatasetMeta(0).data[ctx.index - 1]?.getProps(['y'], true).y;
    mainPctChart = new Chart(canvas, {
        type: 'line',
        data: {
            datasets: [{
                data,
                borderColor: hex, backgroundColor: 'transparent',
                pointBackgroundColor: hex,
                pointHoverBackgroundColor: '#fff', pointHoverBorderColor: hex, pointHoverBorderWidth: 2,
                borderWidth: 2, pointRadius: 3, pointHoverRadius: 5,
                fill: false, tension: 0.3, clip: false,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false },
            animation: {
                x: { type: 'number', easing: 'linear', duration: delayPerPoint, from: NaN,
                     delay: (ctx) => { if (ctx.type !== 'data' || ctx.xStarted) return 0; ctx.xStarted = true; return ctx.index * delayPerPoint; } },
                y: { type: 'number', easing: 'linear', duration: delayPerPoint, from: prevY,
                     delay: (ctx) => { if (ctx.type !== 'data' || ctx.yStarted) return 0; ctx.yStarted = true; return ctx.index * delayPerPoint; } },
            },
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false, external: pctExternalTooltip },
            },
            scales: {
                x: { type: 'time', grid: { display: false }, ticks: { display: false } },
                y: {
                    min: 0,
                    grid: { color: 'rgba(0,0,0,0.05)' },
                    ticks: { color: '#999', callback: v => v.toFixed(1) + '%' },
                }
            }
        }
    });
}

function buildMiniPctBehindChart(disc) {
    const canvas = document.getElementById(`pct-behind-mini-${disc}`);
    if (!canvas) return;
    new Chart(canvas, {
        type: 'line',
        data: {
            datasets: [{
                data: pctBehindData[disc],
                borderColor: DISC_COLORS[disc], borderWidth: 1.5,
                pointRadius: 0, tension: 0.3, fill: false, backgroundColor: 'transparent', clip: false,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false }, yearBands: false },
            scales: {
                x: { type: 'time', display: false },
                y: { min: 0, display: false, grid: { display: false }, border: { display: false } },
            },
            animation: false,
        }
    });
}

function switchPctBehindDisc(disc) {
    if (disc === activePctDisc) return;
    ['overall', 'swim', 'bike', 'run'].forEach(d => {
        const existing = Chart.getChart(document.getElementById(`pct-behind-mini-${d}`));
        if (existing) existing.destroy();
    });
    const card = document.getElementById('pct-behind-main-canvas').closest('.collapsible-card');
    card.querySelectorAll('.ratings-mini').forEach(el => el.classList.remove('active'));
    card.querySelector(`.ratings-mini[data-disc="${disc}"]`).classList.add('active');
    activePctDisc = disc;
    const heading = document.getElementById('pct-behind-section-title');
    if (heading) heading.textContent = `% Behind Leader - ${DISC_LABELS[disc]}`;
    buildMainPctBehindChart(disc);
    ['overall', 'swim', 'bike', 'run'].forEach(buildMiniPctBehindChart);
}

// Align race pills: when the pill has wrapped to a new line, left-align it.
// Uses rendered positions so it handles both single-line-link-overflow and multi-line links.
function alignRacePills() {
    document.querySelectorAll('.race-name-wrap').forEach(wrap => {
        const link = wrap.querySelector('.link');
        const pill = wrap.querySelector('.std-pill');
        if (!link || !pill) return;

        pill.classList.remove('pill-block');

        const linkRects = link.getClientRects();
        const lastLinkTop = linkRects[linkRects.length - 1].top;
        const pillTop = pill.getBoundingClientRect().top;

        // Multi-line link, or pill sits below the link's last baseline
        if (linkRects.length > 1 || pillTop > lastLinkTop + 4) {
            pill.classList.add('pill-block');
        }
    });
}
alignRacePills();
let _pillResizeTimer;
if (!window._ptdAthleteResizeBound) {
    window._ptdAthleteResizeBound = true;
    window.addEventListener('resize', () => {
        clearTimeout(_pillResizeTimer);
        _pillResizeTimer = setTimeout(alignRacePills, 100);
    });
}

// Init
if (pctBehindData) {
    buildMainPctBehindChart('overall');
    ['overall', 'swim', 'bike', 'run'].forEach(buildMiniPctBehindChart);
}

// Re-stripe visible (non-sub-race) rows so alternation is correct
// regardless of how many hidden sub-race rows exist in the DOM.
function _restripeTable(tbody) {
    let stripe = 0;
    for (const row of tbody.rows) {
        if (row.classList.contains('sub-race-row')) continue;
        row.classList.toggle('stripe-even', stripe % 2 === 1);
        stripe++;
    }
}

// Sub-race expand/collapse toggles
document.querySelectorAll('.sub-race-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
        const parentId = btn.dataset.parent;
        const expanded = btn.classList.toggle('expanded');
        document.querySelectorAll(`.sub-race-row[data-parent="${parentId}"]`)
                .forEach(row => row.classList.toggle('visible', expanded));
    });
});

// Initial stripe pass - also called after sort (sortTable fires on th click)
document.querySelectorAll('.results-table tbody, .rating-table tbody').forEach(_restripeTable);

// Expose functions referenced from inline HTML onclick attributes. The IIFE
// wrap above makes these locally scoped, so we hoist them back onto window.
window.switchRatingDisc    = switchRatingDisc;
window.switchRankingDisc   = switchRankingDisc;
window.switchPctBehindDisc = switchPctBehindDisc;
window.toggleNotableResults = toggleNotableResults;

})(); // end IIFE
