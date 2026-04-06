let selectedAthletes = {
    athlete1: null,
    athlete2: null
};
let comparisonGraphsLoaded = false;

// Debounce function for search
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Initialize search for both search boxes
// genderFilter: optional fn returning a gender string to filter results by
function initSearch(searchId, resultsId, selectedId, athleteKey, genderFilter = null) {
    const searchInput = document.getElementById(searchId);
    const resultsDiv = document.getElementById(resultsId);
    const selectedDiv = document.getElementById(selectedId);
    const searchWrapper = searchInput.closest('.search-input-wrapper');

    const performSearch = debounce(async (query) => {
        if (query.length < 2) {
            resultsDiv.classList.remove('active');
            return;
        }

        try {
            let url = `/compare/search?q=${encodeURIComponent(query)}`;
            if (genderFilter) {
                const gender = genderFilter();
                if (gender) url += `&gender=${encodeURIComponent(gender)}`;
            }
            const response = await fetch(url);
            const data = await response.json();

            if (data && data.length > 0) {
                const baseUrl = window.STATIC_BASE_URL || '';
                const defaultImg = `${baseUrl}imgs/default_user.jpg`;
                resultsDiv.innerHTML = data.map(athlete => {
                    const imgSrc = `${baseUrl}athlete_imgs/128/${athlete.athlete_id}.webp`;
                    return `
                    <div class="search-result-item"
                        data-id="${athlete.athlete_id}"
                        data-name="${athlete.name}"
                        data-gender="${athlete.gender}"
                        data-country-emoji="${athlete.country_emoji}"
                        data-country-name="${athlete.country_name}"
                        data-country-alpha3="${athlete.country_alpha3}"
                        data-yob="${athlete.year_of_birth || ''}">
                        <img class="result-avatar" src="${imgSrc}" onerror="this.src='${defaultImg}'" alt="${escapeHtml(athlete.name)}">
                        <div class="result-info">
                            <div class="result-name">${escapeHtml(athlete.name)}</div>
                            <div class="result-meta">${athlete.country_emoji} ${escapeHtml(athlete.country_name)}${athlete.year_of_birth ? ' · ' + athlete.year_of_birth : ''}</div>
                        </div>
                    </div>`;
                }).join('');
                resultsDiv.classList.add('active');

                // Add click handlers
                resultsDiv.querySelectorAll('.search-result-item').forEach(item => {
                    item.addEventListener('click', () => {
                        selectAthlete(athleteKey, {
                            id: parseInt(item.dataset.id),
                            name: item.dataset.name,
                            gender: item.dataset.gender,
                            country_emoji: item.dataset.countryEmoji,
                            country_name: item.dataset.countryName,
                            country_alpha3: item.dataset.countryAlpha3,
                            year_of_birth: item.dataset.yob
                        }, searchInput, resultsDiv, selectedDiv, searchWrapper);
                    });
                });
            } else {
                resultsDiv.innerHTML = '<div class="search-result-item">No athletes found</div>';
                resultsDiv.classList.add('active');
            }
        } catch (error) {
            console.error('Search error:', error);
        }
    }, 300);

    searchInput.addEventListener('input', (e) => {
        performSearch(e.target.value);
    });

    // Close results when clicking outside
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !resultsDiv.contains(e.target)) {
            resultsDiv.classList.remove('active');
        }
    });
}

async function selectAthlete(athleteKey, athlete, searchInput, resultsDiv, selectedDiv, searchWrapper) {
    selectedAthletes[athleteKey] = athlete;

    searchInput.value = '';
    resultsDiv.classList.remove('active');
    if (searchWrapper) searchWrapper.classList.add('hidden');

    // Show placeholder while fetching full data
    selectedDiv.classList.add('active');
    selectedDiv.innerHTML = '<div style="padding:0.5rem 0;color:var(--text-lighter);font-size:0.82rem;">Loading…</div>';

    const baseUrl = window.STATIC_BASE_URL || '';
    const imgSrc       = `${baseUrl}athlete_imgs/128/${athlete.id}.webp`;
    const defaultImg   = `${baseUrl}imgs/default_user.jpg`;

    // Fetch full data (rating, world rank, wins)
    let full = athlete;
    try {
        const res = await fetch(`/compare/athlete/${athlete.id}`);
        full = await res.json();
        full.id = athlete.id;
        selectedAthletes[athleteKey] = { ...athlete, ...full };
    } catch (_) { /* fall back to basic info */ }

    const name         = escapeHtml(full.name         || athlete.name         || '');
    const countryEmoji = full.country_emoji            || athlete.country_emoji || '';
    const countryName  = escapeHtml(full.country_name || athlete.country_name  || '');
    const yob          = full.year_of_birth            || athlete.year_of_birth || '';

    const statsHtml = (full.overall_rating != null) ? `
        <div class="sel-athlete-stats">
            <div class="sel-stat">
                <span class="sel-stat-num">${full.overall_rating}</span>
                <span class="sel-stat-lbl">Rating</span>
            </div>
            <div class="sel-stat-divider"></div>
            <div class="sel-stat">
                <span class="sel-stat-num">${full.world_rank != null ? '#' + full.world_rank : '-'}</span>
                <span class="sel-stat-lbl">World rank</span>
            </div>
            <div class="sel-stat-divider"></div>
            <div class="sel-stat">
                <span class="sel-stat-num">${full.wins ?? '-'}</span>
                <span class="sel-stat-lbl">Career wins</span>
            </div>
        </div>` : '';

    selectedDiv.innerHTML = `
        <button class="sel-remove-btn" aria-label="Clear selection">&times;</button>
        <div class="sel-athlete-card">
            <img class="sel-athlete-img"
                src="${imgSrc}"
                onerror="this.src='${defaultImg}'"
                alt="${name}">
            <div class="sel-athlete-details">
                <div class="sel-athlete-name">${name} ${countryEmoji}</div>
                ${statsHtml}
            </div>
        </div>
    `;

    selectedDiv.querySelector('.sel-remove-btn').addEventListener('click', () => {
        clearSelectedAthlete(athleteKey, searchInput, selectedDiv, searchWrapper);
    });

    updateCompareButton();
}

function clearSelectedAthlete(athleteKey, searchInput, selectedDiv, searchWrapper) {
    selectedAthletes[athleteKey] = null;
    selectedDiv.classList.remove('active');
    selectedDiv.innerHTML = '';
    if (searchWrapper) searchWrapper.classList.remove('hidden');
    searchInput.value = '';
    searchInput.focus();
    updateCompareButton();
}

function updateCompareButton() {
    /* Enable button if both athletes are selected */
    const btn = document.getElementById('compareBtn');
    btn.disabled = !(selectedAthletes.athlete1 && selectedAthletes.athlete2);
}

function showError(message) {
    const errorDiv = document.getElementById('errorMsg');
    errorDiv.textContent = message;
    errorDiv.classList.add('active');
    setTimeout(() => {
        errorDiv.classList.remove('active');
    }, 5000);
}

function _athleteInputs(n) {
    const searchInput = document.getElementById(`search${n}`);
    return {
        searchInput,
        resultsDiv: document.getElementById(`results${n}`),
        selectedDiv: document.getElementById(`selected${n}`),
        searchWrapper: searchInput.closest('.search-input-wrapper'),
    };
}

async function prefillFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const a1 = params.get('a1') || params.get('athlete1');
    const a2 = params.get('a2');

    if (!a1) return;

    const fetchAthlete = id => fetch(`/compare/athlete/${encodeURIComponent(id)}`).then(r => r.ok ? r.json() : null);

    try {
        if (a1 && a2) {
            // Both athletes in URL - prefill and auto-run
            const [ath1, ath2] = await Promise.all([fetchAthlete(a1), fetchAthlete(a2)]);
            if (!ath1?.athlete_id || !ath2?.athlete_id) return;

            const toPayload = a => ({ id: a.athlete_id, name: a.name, gender: a.gender,
                country_emoji: a.country_emoji, country_name: a.country_name,
                country_alpha3: a.country_alpha3, year_of_birth: a.year_of_birth });

            const i1 = _athleteInputs(1), i2 = _athleteInputs(2);
            await selectAthlete('athlete1', toPayload(ath1), i1.searchInput, i1.resultsDiv, i1.selectedDiv, i1.searchWrapper);
            await selectAthlete('athlete2', toPayload(ath2), i2.searchInput, i2.resultsDiv, i2.selectedDiv, i2.searchWrapper);
            await performComparison(/* pushState= */ false);
        } else if (a1) {
            // Single athlete pre-fill (legacy ?athlete1= support)
            const ath = await fetchAthlete(a1);
            if (!ath?.athlete_id) return;
            const i1 = _athleteInputs(1);
            selectAthlete('athlete1', { id: ath.athlete_id, name: ath.name, gender: ath.gender,
                country_emoji: ath.country_emoji, country_name: ath.country_name,
                country_alpha3: ath.country_alpha3, year_of_birth: ath.year_of_birth },
                i1.searchInput, i1.resultsDiv, i1.selectedDiv, i1.searchWrapper);
        }
    } catch (error) {
        console.error('Prefill error:', error);
    }
}

async function performComparison(pushState = true) {
    const loadingDiv = document.getElementById('loading');
    const resultsDiv = document.getElementById('comparisonResults');

    loadingDiv.classList.add('active');
    resultsDiv.classList.remove('active');

    const id1 = selectedAthletes.athlete1.id;
    const id2 = selectedAthletes.athlete2.id;

    try {
        const response = await fetch(`/compare/${id1}/${id2}`, { headers: { 'X-Partial': '1' } });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Comparison failed');
        }

        const html = await response.text();
        resultsDiv.innerHTML = html;
        resultsDiv.classList.add('active');

        if (pushState) {
            history.pushState({ a1: id1, a2: id2 }, '', `?a1=${id1}&a2=${id2}`);
        }

        loadComparisonResultsJs();

    } catch (error) {
        showError(error.message);
    } finally {
        loadingDiv.classList.remove('active');
    }
}

// --- Load comparison charts dynamically from their js ---
function loadComparisonResultsJs() {
    if (comparisonGraphsLoaded) {
        initRatings();
        initRankings();
        return;
    }

    const script = document.createElement("script");
    const baseUrl = window.STATIC_BASE_URL || "https://www.static.protridata/";
    script.src = `${baseUrl}js/comparison_results.js`;
    document.body.appendChild(script);

    script.onload = () => {
        comparisonGraphsLoaded = true;
        initRatings();
        initRankings();
    };
}

// Initialize
initSearch('search1', 'results1', 'selected1', 'athlete1');
initSearch('search2', 'results2', 'selected2', 'athlete2', () => selectedAthletes.athlete1?.gender);
prefillFromUrl();

document.getElementById('compareBtn').addEventListener('click', () => performComparison());
