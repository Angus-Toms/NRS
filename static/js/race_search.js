const input         = document.getElementById('rs-input');
const filterBtn     = document.getElementById('filterToggle');
const filterPanel   = document.getElementById('filterPanel');
const filterSummary = document.getElementById('rs-filter-summary');
const results       = document.getElementById('rs-results');
const countryEl     = document.getElementById('rs-country');
const yearStartEl   = document.getElementById('rs-year-start');
const yearEndEl     = document.getElementById('rs-year-end');
const resetBtn      = document.getElementById('rs-reset');

function getSort()      { return document.querySelector('input[name="rs-sort"]:checked')?.value || 'desc'; }
function getCountry()   { return countryEl.value; }
function getYearStart() { return yearStartEl.value; }
function getYearEnd()   { return yearEndEl.value; }

function updateSummary() {
    const parts = [ getSort() === 'desc' ? 'Newest first' : 'Oldest first' ];
    if (getCountry()) parts.push(getCountry());
    if (getYearStart() || getYearEnd()) {
        parts.push(`${getYearStart() || '…'}–${getYearEnd() || '…'}`);
    }
    filterSummary.innerHTML = parts
        .map((p, i) => (i ? '<span class="filter-summary-sep">·</span>' : '') + p)
        .join(' ');
}

filterBtn.addEventListener('click', () => {
    const open = filterPanel.classList.toggle('open');
    filterBtn.setAttribute('aria-expanded', open);
});

let searchTimer;

function runSearch() {
    const q = input.value.trim();
    if (q.length < 2) {
        results.innerHTML = '';
        return;
    }
    const params = new URLSearchParams({ q, sort: getSort() });
    if (getCountry())   params.set('country', getCountry());
    if (getYearStart()) params.set('year_start', getYearStart());
    if (getYearEnd())   params.set('year_end', getYearEnd());

    results.innerHTML = '<div class="rs-loading">Searching…</div>';

    fetch(`/races/search?${params}`)
        .then(r => r.json())
        .then(renderResults);
}

function onFilterChange() {
    updateSummary();
    runSearch();
}

input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 200);
});

document.querySelectorAll('input[name="rs-sort"]').forEach(r => r.addEventListener('change', onFilterChange));
countryEl.addEventListener('change', onFilterChange);
yearStartEl.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(onFilterChange, 400);
});
yearEndEl.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(onFilterChange, 400);
});

resetBtn.addEventListener('click', () => {
    document.getElementById('sort-desc').checked = true;
    countryEl.value   = '';
    yearStartEl.value = '';
    yearEndEl.value   = '';
    input.value       = '';
    results.innerHTML = '';
    updateSummary();
});

updateSummary();

function esc(text) {
    const d = document.createElement('div');
    d.textContent = text || '';
    return d.innerHTML;
}

function renderResults(events) {
    if (!events.length) {
        results.innerHTML = '<div class="rs-no-results">No events found</div>';
        return;
    }

    const html = events.map(ev => {
        const races = ev.races || [];
        const meta = [
            ev.venue ? esc(ev.venue) : null,
            esc(ev.country),
            esc(ev.event_date),
        ].filter(Boolean).join(' · ');

        // Programs at an event share course length, so emit one chip next
        // to the event name rather than repeating it on every program pill.
        const eventCourse = races.find(r => r.course)?.course || null;
        const eventChip = eventCourse === 'long'
            ? '<span class="ptd-tag ptd-tag--sm ptd-tag--lc">LC</span>'
            : eventCourse === 'short'
                ? '<span class="ptd-tag ptd-tag--sm ptd-tag--sc">SC</span>'
                : '';

        const pillsHtml = races.map(r =>
            `<a href="/race/${r.race_id}" class="race-pill">${esc(r.prog_name)}</a>`
        ).join('');

        return `
            <div class="rs-result-item">
                <div class="rs-result-name-row">
                    <a href="/event/${ev.event_id}" class="rs-result-name">${esc(ev.name)}</a>
                    ${eventChip}
                </div>
                <div class="rs-result-meta">${meta}</div>
                ${races.length ? `<div class="rs-result-races">${pillsHtml}</div>` : ''}
            </div>
        `;
    }).join('');

    results.innerHTML = html;
}
