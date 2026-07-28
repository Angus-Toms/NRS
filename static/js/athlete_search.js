// Athletes landing page - live search + filters
const baseUrl    = window.STATIC_BASE_URL || '';
const defaultImg = `${baseUrl}imgs/default_user.jpg`;

const input        = document.getElementById('as-input');
const results      = document.getElementById('as-results');
const filterBtn    = document.getElementById('filterToggle');
const filterPanel  = document.getElementById('filterPanel');
const filterSummary = document.getElementById('as-filter-summary');
const countryEl    = document.getElementById('as-country');
const yobStart     = document.getElementById('as-yob-start');
const yobEnd       = document.getElementById('as-yob-end');
const activeOnly   = document.getElementById('as-active-only');
const activeText   = document.getElementById('as-active-text');
const resetBtn     = document.getElementById('as-reset');

let searchTimer = null;

const DISC_LABELS  = { overall: 'Overall', swim: 'Swim', bike: 'Bike', run: 'Run' };
const ORDER_LABELS = { top: 'Top rated', hot: 'Trending' };

function getDisc()   { return document.querySelector('[name="as-disc"]:checked')?.value   || 'overall'; }
function getOrder()  { return document.querySelector('[name="as-order"]:checked')?.value  || 'top'; }
function getCourse() { return document.querySelector('[name="as-course"]:checked')?.value || 'all'; }

function updateSummary() {
    const parts = [
        DISC_LABELS[getDisc()],
        getCourse() === 'all' ? 'All courses' : (getCourse() === 'short' ? 'Short' : 'Long'),
        ORDER_LABELS[getOrder()],
    ];
    if (activeOnly.checked) parts.push('Active');
    if (countryEl.value)    parts.push(countryEl.value);
    if (yobStart.value || yobEnd.value) {
        parts.push(`b. ${yobStart.value || '…'}–${yobEnd.value || '…'}`);
    }
    filterSummary.innerHTML = parts
        .map((p, i) => (i ? '<span class="filter-summary-sep">·</span>' : '') + p)
        .join(' ');
}

filterBtn.addEventListener('click', () => {
    const open = filterPanel.classList.toggle('open');
    filterBtn.setAttribute('aria-expanded', open);
});

document.querySelectorAll('[name="as-disc"], [name="as-order"], [name="as-course"]').forEach(r => {
    r.addEventListener('change', onFilterChange);
});
countryEl.addEventListener('change', onFilterChange);
yobStart.addEventListener('input', onFilterChange);
yobEnd.addEventListener('input', onFilterChange);
activeOnly.addEventListener('change', () => {
    activeText.textContent = activeOnly.checked ? 'On' : 'Off';
    onFilterChange();
});

function onFilterChange() {
    updateSummary();
    triggerSearch();
}

// YOB presets (data-preset attr distinguishes from leaderboard's data-age)
document.querySelectorAll('.btn-age-preset[data-preset]').forEach(btn => {
    btn.addEventListener('click', () => {
        const preset = btn.dataset.preset;
        if (preset === 'junior') {
            yobStart.value = 2007;
            yobEnd.value   = '';
        } else if (preset === 'u23') {
            yobStart.value = 2004;
            yobEnd.value   = 2006;
        }
        onFilterChange();
    });
});

resetBtn.addEventListener('click', () => {
    document.getElementById('as-course-all').checked = true;
    document.getElementById('disc-overall').checked  = true;
    document.getElementById('order-top').checked     = true;
    countryEl.value = '';
    yobStart.value  = '';
    yobEnd.value    = '';
    activeOnly.checked = false;
    activeText.textContent = 'Off';
    input.value = '';
    results.innerHTML = '';
    updateSummary();
});

input.addEventListener('input', triggerSearch);
updateSummary();

function triggerSearch() {
    clearTimeout(searchTimer);
    const q = input.value.trim();
    if (q.length < 2) {
        results.innerHTML = '';
        return;
    }
    searchTimer = setTimeout(() => runSearch(q), 200);
}

async function runSearch(q) {
    results.innerHTML = '<div class="as-loading">Searching…</div>';

    const params = new URLSearchParams({ q, disc: getDisc(), order: getOrder(), course: getCourse() });
    if (countryEl.value)    params.set('country', countryEl.value);
    if (yobStart.value)     params.set('yob_start', yobStart.value);
    if (yobEnd.value)       params.set('yob_end', yobEnd.value);
    if (activeOnly.checked) params.set('active_only', 'true');

    const res  = await fetch(`/athletes/search?${params}`);
    const data = await res.json();
    renderResults(data, getDisc(), getOrder());
}

function renderResults(athletes, disc, order) {
    if (!athletes.length) {
        results.innerHTML = '<div class="as-no-results">No athletes found</div>';
        return;
    }

    const LABELS = { overall: 'Overall', swim: 'Swim', bike: 'Bike', run: 'Run', transition: 'Transition' };
    const hotCls = order === 'hot' ? ' athlete-ratings-hot' : '';

    results.innerHTML = athletes.map(a => {
        const img = a.has_img ? `${baseUrl}athlete_imgs/128/${a.athlete_id}.webp` : defaultImg;

        // Ratings block - 4 disciplines, active one highlighted
        const ratingsHtml = ['overall', 'swim', 'bike', 'run', 'transition'].map(d => `
            <div class="rating-item">
                <span class="rating-label">${LABELS[d]}</span>
                <span class="rating-value${d === disc ? ' rating-highlight' : ''}">${a[d + '_rating']}</span>
            </div>`).join('');

        // Meta row - flag · country · YOB · races · wins · [tags]
        // Each meta-item is preceded by a `<span class="meta-item-sep"> · </span>`;
        // CSS hides the leading one via :first-child so conditional items don't
        // produce an orphan separator.
        const SEP = '<span class="meta-item-sep"> · </span>';
        const yobItem = a.year_of_birth
            ? `${SEP}<span class="meta-item">b. ${a.year_of_birth}</span>`
            : '';
        const tags = [];
        if (a.has_elite_short) tags.push('<span class="ptd-tag ptd-tag--sc">SC</span>');
        if (a.has_elite_long)  tags.push('<span class="ptd-tag ptd-tag--lc">LC</span>');
        if (a.has_ag)          tags.push('<span class="ptd-tag ptd-tag--ag">AG</span>');
        const tagsItem = tags.length
            ? `${SEP}<span class="meta-item ptd-tag-row">${tags.join('')}</span>`
            : '';
        const metaHtml = `
            ${SEP}<span class="meta-item">${flagImg(a.country_alpha3, a.country_full)} ${escapeHtml(a.country_full)}</span>
            ${yobItem}
            ${SEP}<span class="meta-item"><span class="meta-val">${a.race_starts}</span> races</span>
            ${SEP}<span class="meta-item"><span class="meta-val">${a.wins}</span> wins</span>
            ${tagsItem}`;

        // When course is 'all' we don't have a clean course to hand off to
        // the profile page, so let the profile page pick its own default.
        const courseQS = getCourse() === 'all' ? '' : `?course=${encodeURIComponent(getCourse())}`;
        return `
        <a href="/athlete/${a.athlete_id}${courseQS}"${courseQS ? ' rel="nofollow"' : ''} class="as-result-item">
            <img class="as-result-avatar" src="${img}" alt="${escapeHtml(a.name)}" onerror="this.src='${defaultImg}'">
            <div class="as-result-info">
                <div class="as-result-name">${escapeHtml(a.name)}</div>
                <div class="athlete-meta">${metaHtml}</div>
            </div>
            <div class="athlete-ratings${hotCls}">${ratingsHtml}</div>
        </a>`;
    }).join('');
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
