const input       = document.getElementById('rs-input');
const filterBtn   = document.getElementById('rs-filter-toggle');
const filterPanel = document.getElementById('rs-filters');
const results     = document.getElementById('rs-results');

// ── Filter toggle ──
filterBtn.addEventListener('click', () => {
    const open = filterPanel.hidden;
    filterPanel.hidden = !open;
    filterBtn.setAttribute('aria-expanded', open);
});

// ── Live search ──
let searchTimer;

function getSort()    { return document.querySelector('input[name="rs-sort"]:checked')?.value || 'desc'; }
function getCountry() { return document.getElementById('rs-country').value; }
function getYearStart() { return document.getElementById('rs-year-start').value; }
function getYearEnd()   { return document.getElementById('rs-year-end').value; }

function runSearch() {
    const q = input.value.trim();
    if (q.length < 2) {
        results.innerHTML = '';
        return;
    }
    const params = new URLSearchParams({ q, sort: getSort() });
    const country = getCountry();
    const ys = getYearStart(), ye = getYearEnd();
    if (country)  params.set('country', country);
    if (ys)       params.set('year_start', ys);
    if (ye)       params.set('year_end', ye);

    results.innerHTML = '<div class="rs-loading">Searching…</div>';

    fetch(`/races/search?${params}`)
        .then(r => r.json())
        .then(renderResults);
}

input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 200);
});

// Re-run on filter changes
document.querySelectorAll('input[name="rs-sort"]').forEach(r => r.addEventListener('change', runSearch));
document.getElementById('rs-country').addEventListener('change', runSearch);
document.getElementById('rs-year-start').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 400);
});
document.getElementById('rs-year-end').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 400);
});

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

        const pillsHtml = races.map(r =>
            `<a href="/race/${r.race_id}" class="race-pill">${esc(r.prog_name)}</a>`
        ).join('');

        return `
            <div class="rs-result-item">
                <a href="/event/${ev.event_id}" class="rs-result-name">${esc(ev.name)}</a>
                <div class="rs-result-meta">${meta}</div>
                ${races.length ? `<div class="rs-result-races">${pillsHtml}</div>` : ''}
            </div>
        `;
    }).join('');

    results.innerHTML = html;
}
