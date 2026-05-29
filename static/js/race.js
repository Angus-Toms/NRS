// Chart.js global defaults - match site typography and colour palette
Chart.defaults.font.family = "'Plus Jakarta Sans', sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.color = '#6b7280'; // --text-light

Chart.defaults.plugins.tooltip.backgroundColor = '#1a1a2e'; // --navy
Chart.defaults.plugins.tooltip.titleColor = '#ffffff';
Chart.defaults.plugins.tooltip.bodyColor = 'rgba(255,255,255,0.7)';
Chart.defaults.plugins.tooltip.padding = { x: 10, y: 8 };
Chart.defaults.plugins.tooltip.cornerRadius = 6;
Chart.defaults.plugins.tooltip.displayColors = false;
Chart.defaults.plugins.tooltip.titleFont = { family: "'Plus Jakarta Sans', sans-serif", weight: '600', size: 12 };
Chart.defaults.plugins.tooltip.bodyFont = { family: "'Plus Jakarta Sans', sans-serif", size: 11 };

function _debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// Sort table click listeners - safe to call multiple times after partial swaps
function initSortableListeners() {
    document.querySelectorAll('table.sortable-table').forEach(table => {
        const allHeaders = Array.from(table.querySelectorAll('th'));
        const sortableHeaders = table.querySelectorAll('th.sortable');
        sortableHeaders.forEach((header) => {
            header.addEventListener('click', () => {
                const colIndex = allHeaders.indexOf(header);
                const isAsc = header.classList.contains('asc');
                sortableHeaders.forEach(h => h.classList.remove('asc', 'desc'));
                header.classList.add(isAsc ? 'desc' : 'asc');
                sortTable(table, colIndex, !isAsc);
            });
        });
    });
}

// Chart initialisation - destroys existing instances before recreating
function initRaceCharts() {
    const charts = [
        // Time histograms
        { id: 'overall-times-canvas', data: 'overall-time-hist-data', label: 'Overall Time', fmt: 'time' },
        { id: 'swim-times-canvas',    data: 'swim-time-hist-data',    label: 'Swim Time',    fmt: 'time' },
        { id: 'bike-times-canvas',    data: 'bike-time-hist-data',    label: 'Bike Time',    fmt: 'time' },
        { id: 'run-times-canvas',     data: 'run-time-hist-data',     label: 'Run Time',     fmt: 'time' },
        { id: 't1-times-canvas',      data: 't1-time-hist-data',      label: 'T1 Time',      fmt: 'time' },
        { id: 't2-times-canvas',      data: 't2-time-hist-data',      label: 'T2 Time',      fmt: 'time' },
        // Rating histograms
        { id: 'overall-ratings-canvas',    data: 'overall-ratings-hist-data',    label: 'Overall Rating',    fmt: 'rating' },
        { id: 'swim-ratings-canvas',       data: 'swim-ratings-hist-data',       label: 'Swim Rating',       fmt: 'rating' },
        { id: 'bike-ratings-canvas',       data: 'bike-ratings-hist-data',       label: 'Bike Rating',       fmt: 'rating' },
        { id: 'run-ratings-canvas',        data: 'run-ratings-hist-data',        label: 'Run Rating',        fmt: 'rating' },
        { id: 'transition-ratings-canvas', data: 'transition-ratings-hist-data', label: 'Transition Rating', fmt: 'rating' },
    ];

    charts.forEach(({ id, data: dataId, label, fmt }) => {
        const canvas = document.getElementById(id);
        if (!canvas) return;
        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();
        const data = getJSON(dataId);
        if (!data || !data.datasets) return;

        new Chart(canvas, {
            type: 'bar',
            data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                parsing: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: 'index',
                        callbacks: {
                            title: ctx => ctx[0].raw.label,
                            label: ctx => `Athletes: ${ctx.raw.y}`,
                        }
                    }
                },
                scales: {
                    x: {
                        beginAtZero: false,
                        type: 'linear',
                        title: { display: true, text: label },
                        ticks: fmt === 'time'
                            ? { callback: v => isNaN(+v) ? v : formatTime(+v) }
                            : { stepSize: 100, callback: v => Math.round(v) }
                    },
                    y: {
                        beginAtZero: true,
                        type: 'linear',
                        title: { display: true, text: 'Number of Athletes' }
                    }
                }
            }
        });
    });
}

// ============================================================
//  ATHLETE PREDICTION
// ============================================================

// Column index (0-based) for each discipline in the results table
const _DISC_COL = { overall: 8, swim: 3, bike: 5, run: 7 };

function _fitWLS(xs, ys, ws) {
    const W   = ws.reduce((a, b) => a + b, 0);
    const sX  = ws.reduce((s, w, i) => s + w * xs[i], 0);
    const sY  = ws.reduce((s, w, i) => s + w * ys[i], 0);
    const sXY = ws.reduce((s, w, i) => s + w * xs[i] * ys[i], 0);
    const sXX = ws.reduce((s, w, i) => s + w * xs[i] * xs[i], 0);
    const d   = W * sXX - sX * sX;
    if (!d) return null;
    const slope = (W * sXY - sX * sY) / d;
    return { slope, intercept: (sY - slope * sX) / W };
}

function initPrediction() {
    const predData = getJSON('prediction-data');
    if (!predData || predData.length < 3) return;

    // Fit a WLS model for each discipline from the race's existing data
    const RATING_KEY = { overall: 'rating',      swim: 'swim_rating', bike: 'bike_rating', run: 'run_rating' };
    const TIME_KEY   = { overall: 'time',         swim: 'swim_time',   bike: 'bike_time',   run: 'run_time'  };
    const models = {};
    for (const disc of ['overall', 'swim', 'bike', 'run']) {
        const rk = RATING_KEY[disc], tk = TIME_KEY[disc];
        const pts = predData.filter(d => d[rk] != null && d[tk] != null && d[tk] > 0);
        if (pts.length >= 3)
            models[disc] = _fitWLS(pts.map(d => d[rk]), pts.map(d => d[tk]), pts.map(d => d.w || 1));
    }

    const existingIds   = new Set(getJSON('race-athlete-ids') || []);
    const raceYear      = getJSON('race-year');
    const gender        = document.getElementById('add-prediction-btn').dataset.gender;
    const course        = document.getElementById('add-prediction-btn').dataset.course || 'short';
    const tbody         = document.querySelector('table.results-table tbody');
    const addBtn        = document.getElementById('add-prediction-btn');
    const modal         = document.getElementById('prediction-modal');
    const backdrop      = modal.querySelector('.prediction-modal-backdrop');
    const searchInput   = document.getElementById('prediction-search-input');
    const searchResults = document.getElementById('prediction-search-results');

    const getDisc    = () => modal.querySelector('input[name="pred-disc"]:checked')?.value || 'overall';
    const openModal  = () => { modal.classList.remove('hidden'); searchInput.focus(); };
    const closeModal = () => {
        modal.classList.add('hidden');
        searchInput.value = '';
        searchResults.innerHTML = '';
        searchResults.classList.remove('active');
    };

    addBtn.addEventListener('click', openModal);
    backdrop.addEventListener('click', closeModal);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

    searchInput.addEventListener('input', _debounce(async (e) => {
        const q = e.target.value.trim();
        if (q.length < 2) { searchResults.classList.remove('active'); searchResults.innerHTML = ''; return; }
        try {
            const res      = await fetch(`/athlete-compare/search?q=${encodeURIComponent(q)}&gender=${encodeURIComponent(gender)}&course=${encodeURIComponent(course)}`);
            const data     = await res.json();
            const filtered = data.filter(a => !existingIds.has(a.athlete_id));
            _renderPredictionResults(filtered, searchResults);
            searchResults.querySelectorAll('.pred-result-item[data-id]').forEach(item => {
                item.addEventListener('click', async () => {
                    const disc = getDisc();
                    closeModal();
                    await _addPredictionRow(
                        { id: parseInt(item.dataset.id), name: item.dataset.name,
                          country_alpha3: item.dataset.alpha3, yob: item.dataset.yob },
                        models, disc, existingIds, tbody, raceYear
                    );
                });
            });
        } catch (err) { console.error('Prediction search failed:', err); }
    }, 300));
}

function _renderPredictionResults(athletes, container) {
    if (!athletes.length) {
        container.innerHTML = '<div class="pred-result-item pred-no-results">No matching athletes</div>';
        container.classList.add('active');
        return;
    }
    const base = window.STATIC_BASE_URL || '';
    container.innerHTML = athletes.slice(0, 8).map(a => `
        <div class="pred-result-item" data-id="${a.athlete_id}"
             data-name="${escapeHtml(a.name)}" data-alpha3="${a.country_alpha3 || ''}"
             data-yob="${a.year_of_birth || ''}">
            <img class="pred-result-avatar"
                 src="${base}athlete_imgs/128/${a.athlete_id}.webp"
                 onerror="this.src='${base}imgs/default_user.jpg'" alt="">
            <div>
                <div class="pred-result-name">${escapeHtml(a.name)}</div>
                <div class="pred-result-meta">${flagImg(a.country_alpha3, a.country_name)} ${escapeHtml(a.country_name)}</div>
            </div>
        </div>`).join('');
    container.classList.add('active');
}

function _getDiscWinnerTime(tbody, disc) {
    const col = _DISC_COL[disc];
    let min = Infinity;
    for (const row of tbody.querySelectorAll('tr:not(.predicted-row)')) {
        const t = parseTime(row.cells[col]?.getAttribute('data-value') || '');
        if (isFinite(t) && t > 0 && t < min) min = t;
    }
    return isFinite(min) ? min : null;
}

function _sortTableByDisc(table, disc) {
    const col = _DISC_COL[disc];
    const headers = table.querySelectorAll('th.sortable');
    headers.forEach(h => h.classList.remove('asc', 'desc'));
    headers[col].classList.add('asc');
    sortTable(table, col, true);
}

async function _addPredictionRow(athlete, models, disc, existingIds, tbody, raceYear) {
    existingIds.add(athlete.id);

    const ATHLETE_RATING_KEY = { overall: 'overall_rating', swim: 'swim_rating', bike: 'bike_rating', run: 'run_rating' };
    let athleteRating = null;
    try {
        const data   = await fetch(`/athlete-compare/athlete/${athlete.id}`).then(r => r.json());
        athleteRating = data[ATHLETE_RATING_KEY[disc]];
    } catch (_) {}

    const model        = models[disc];
    const predTimeSecs = (model && athleteRating != null)
        ? Math.round(model.slope * athleteRating + model.intercept)
        : null;

    const winnerTime = _getDiscWinnerTime(tbody, disc);
    let gapHtml = '';
    if (predTimeSecs != null && winnerTime != null) {
        const diff = predTimeSecs - winnerTime;
        if (diff > 0)
            gapHtml = `<span class="pred-time-gap pred-behind">+${formatTime(diff)}</span>`;
        else if (diff < 0)
            gapHtml = `<span class="pred-time-gap pred-ahead">\u2212${formatTime(-diff)}</span>`;
    }

    const label      = disc === 'overall' ? '(predicted)' : `(predicted ${disc})`;
    const yobDisplay = athlete.yob
        ? `${athlete.yob}${raceYear ? ` (${raceYear - parseInt(athlete.yob)})` : ''}` : '';
    const timeStr    = predTimeSecs != null ? formatTime(predTimeSecs) : '-';
    const timeDataVal = predTimeSecs != null ? formatTime(predTimeSecs) : '';

    // Build the 9 split cells; only the predicted discipline column is populated
    const col = _DISC_COL[disc];
    const splitCells = [3, 4, 5, 6, 7, 8].map(i => {
        if (i === col)
            return `<td class="time-col${i === 8 ? ' overall-col' : ''}" data-value="${timeDataVal}">` +
                   `<span class="overall-time">${timeStr}</span>${gapHtml}</td>`;
        return `<td class="time-col${i === 8 ? ' overall-col' : ''}" data-value=""></td>`;
    }).join('');

    const deleteSvg = `<svg viewBox="0 0 16 16" fill="currentColor" width="13" height="13" aria-hidden="true">
        <path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm2.5 0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm3 .5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0V6z"/>
        <path fill-rule="evenodd" d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1h3.5a1 1 0 0 1 1 1v1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4H4.118zM2.5 3V2h11v1h-11z"/>
    </svg>`;

    const row = document.createElement('tr');
    row.className = 'predicted-row';
    row.dataset.athleteId = athlete.id;
    row.dataset.predDisc  = disc;
    if (predTimeSecs != null) row.dataset.predSecs = predTimeSecs;
    row.innerHTML = `
        <td class="position-col pred-pos-col" data-value="9999">
            <button class="pred-delete-btn" title="Remove prediction" aria-label="Remove prediction">${deleteSvg}</button>
        </td>
        <td class="athlete-col" data-value="${escapeHtml(athlete.name)}">
            <span class="pred-name">${escapeHtml(athlete.name)}</span> <span class="pred-label">${label}</span> ${flagImg(athlete.country_alpha3)}
        </td>
        <td class="yob-col" data-value="${athlete.yob || 0}">${yobDisplay}</td>
        ${splitCells}`;

    // Sort by the predicted discipline column before inserting
    _sortTableByDisc(tbody.closest('table'), disc);

    // Insert before the first real finisher slower in this discipline
    let inserted = false;
    if (predTimeSecs != null) {
        for (const existing of tbody.querySelectorAll('tr:not(.predicted-row)')) {
            const posVal = parseInt(existing.cells[0]?.getAttribute('data-value'), 10);
            if (!isNaN(posVal) && posVal < 9000) {
                const t = parseTime(existing.cells[col]?.getAttribute('data-value') || '');
                if (isFinite(t) && t > 0 && predTimeSecs < t) {
                    tbody.insertBefore(row, existing); inserted = true; break;
                }
            }
        }
    }
    if (!inserted) tbody.appendChild(row);

    _renumberPredictions(tbody);

    row.querySelector('.pred-delete-btn').addEventListener('click', () => {
        existingIds.delete(athlete.id);
        row.remove();
        _renumberPredictions(tbody);
    });

    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.classList.add('predicted-row-flash');
    setTimeout(() => row.classList.remove('predicted-row-flash'), 1200);
}

function _renumberPredictions(tbody) {
    for (const predRow of tbody.querySelectorAll('tr.predicted-row')) {
        const disc     = predRow.dataset.predDisc || 'overall';
        const col      = _DISC_COL[disc];
        const predSecs = parseInt(predRow.dataset.predSecs, 10);
        if (isNaN(predSecs)) continue;

        let pos = 1;
        // Real finishers faster in this discipline
        for (const row of tbody.querySelectorAll('tr:not(.predicted-row)')) {
            const t = parseTime(row.cells[col]?.getAttribute('data-value') || '');
            if (isFinite(t) && t > 0 && t < predSecs) pos++;
        }
        // Other predictions in the same discipline that are faster
        for (const other of tbody.querySelectorAll('tr.predicted-row')) {
            if (other === predRow) continue;
            if ((other.dataset.predDisc || 'overall') !== disc) continue;
            const o = parseInt(other.dataset.predSecs, 10);
            if (!isNaN(o) && o < predSecs) pos++;
        }
        predRow.cells[0].setAttribute('data-value', pos);
    }
}

// Results/Predictions tab toggle
function initRaceViewToggle() {
    const buttons = document.querySelectorAll('.race-view-btn');
    if (!buttons.length) return;
    const titleEl    = document.getElementById('results-section-title');
    const subtitleEl = document.getElementById('predictions-subtitle');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const view = btn.dataset.view;
            const resultsEl     = document.getElementById('results-view');
            const predictionsEl = document.getElementById('predictions-view');
            if (resultsEl)     resultsEl.classList.toggle('hidden', view !== 'results');
            if (predictionsEl) predictionsEl.classList.toggle('hidden', view !== 'predictions');
            if (subtitleEl)    subtitleEl.classList.toggle('hidden', view !== 'predictions');
            if (titleEl) titleEl.textContent = view === 'predictions' ? 'Predicted Results' : 'Race Results';
        });
    });
}

// Mobile chip-row sort + splits accordion for race page tables. Mirrors the
// athlete page implementation; each .results-sort-chips / .ratings-sort-chips
// row sorts the table inside its next-sibling wrapper.
function initRaceSortChips() {
    const defaultAscFor = { position: true, discipline: false };
    document.querySelectorAll('.results-sort-chips, .ratings-sort-chips').forEach(chipRow => {
        const table = chipRow.nextElementSibling?.querySelector('table.sortable-table');
        if (!table) return;
        const chips = chipRow.querySelectorAll('.sort-chip');

        // Default: first chip (Position, defaults asc - 1st place first).
        const first = chips[0];
        if (first) first.classList.add('selected', 'asc');

        chips.forEach(chip => {
            chip.addEventListener('click', () => {
                const col = parseInt(chip.dataset.sortCol, 10);
                const wasSelected = chip.classList.contains('selected');
                const wasDesc = chip.classList.contains('desc');
                chips.forEach(c => c.classList.remove('selected', 'asc', 'desc'));

                // Toggle direction when re-clicking; otherwise:
                //   position -> asc (1st first), other (time/rating) -> desc.
                const axis = chip.dataset.sortAxis || (col === 0 ? 'position' : 'value');
                let asc;
                if (wasSelected) asc = wasDesc;
                else asc = defaultAscFor[axis] ?? false;

                chip.classList.add('selected', asc ? 'asc' : 'desc');
                sortTable(table, col, asc);
            });
        });
    });
}

function initRaceSplitsToggle() {
    document.querySelectorAll('.results-table .splits-toggle, .rating-table .splits-toggle').forEach(btn => {
        // Skip listeners already attached after partial swaps.
        if (btn.dataset.toggleInit) return;
        btn.dataset.toggleInit = '1';
        btn.addEventListener('click', () => {
            const row = btn.closest('tr');
            if (!row) return;
            const expanded = row.classList.toggle('expanded');
            btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            btn.setAttribute('aria-label', expanded ? 'Hide splits' : 'Show splits');
        });
    });
}

// Year-picker dropdown in the race hero breadcrumb. Event delegation so
// it survives the partial swap done by switchRace().
document.addEventListener('click', (e) => {
    const trigger = e.target.closest('.race-year-trigger');
    if (trigger) {
        const picker = trigger.closest('.race-year-picker');
        document.querySelectorAll('.race-year-picker.open').forEach(p => {
            if (p !== picker) p.classList.remove('open');
        });
        picker.classList.toggle('open');
        return;
    }
    document.querySelectorAll('.race-year-picker.open').forEach(p => {
        if (!p.contains(e.target)) p.classList.remove('open');
    });
});

// Live countdown for upcoming-race hero. Each `.race-countdown[data-race-date]`
// ticks once per second to show d/h/m/s until the race date. When the delta
// hits zero we swap in a fixed "race is today" message until the page is
// reloaded. ISO date (no time) is assumed to refer to the start of the day
// in the venue's local time — we display the user's local interpretation,
// which is close enough for at-a-glance context.
function _tickCountdown(el) {
    const iso = el.dataset.raceDate;
    if (!iso) return;
    const target = new Date(iso + 'T00:00:00').getTime();
    const now = Date.now();
    const diff = target - now;
    if (diff <= 0) {
        el.textContent = "Race is today - results will be posted as soon as they're available.";
        el.dataset.done = '1';
        return;
    }
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    // Sub-day: drop the days segment.
    el.textContent = d > 0
        ? `${d}d ${h}h ${m}m ${s}s until race`
        : `${h}h ${m}m ${s}s until race`;
}
function _initCountdowns() {
    const els = document.querySelectorAll('.race-countdown[data-race-date]:not([data-done])');
    if (!els.length) return;
    els.forEach(_tickCountdown);
}
_initCountdowns();
setInterval(_initCountdowns, 1000);

