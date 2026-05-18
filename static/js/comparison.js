let selectedAthletes = {
    athlete1: null,
    athlete2: null
};
let selectedProgram = null;   // one of 'elite-short' | 'elite-long' | 'ag', once chosen
let comparisonGraphsLoaded = false;

const PROGRAM_LABELS = {
    'elite-short': 'Short Course',
    'elite-long':  'Long Course',
    'ag':          'Age Group',
};

function badgesHtml(athlete) {
    const tags = [];
    if (athlete.has_elite_short) tags.push('<span class="ptd-tag ptd-tag--sc">SC</span>');
    if (athlete.has_elite_long)  tags.push('<span class="ptd-tag ptd-tag--lc">LC</span>');
    if (athlete.has_ag)          tags.push('<span class="ptd-tag ptd-tag--ag">AG</span>');
    if (!tags.length) return '';
    return `<span class="result-meta-sep">·</span><span class="ptd-tag-row">${tags.join('')}</span>`;
}

function programsFromTags(athlete) {
    const out = [];
    if (athlete.has_elite_short) out.push('elite-short');
    if (athlete.has_elite_long)  out.push('elite-long');
    if (athlete.has_ag)          out.push('ag');
    return out;
}

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
// programFilter: optional fn returning a list of programs to restrict results to
function initSearch(searchId, resultsId, selectedId, athleteKey, genderFilter = null, programFilter = null) {
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
            let url = `/athlete-compare/search?q=${encodeURIComponent(query)}`;
            if (genderFilter) {
                const gender = genderFilter();
                if (gender) url += `&gender=${encodeURIComponent(gender)}`;
            }
            if (programFilter) {
                const programs = programFilter();
                if (programs && programs.length) {
                    url += `&programs=${encodeURIComponent(programs.join(','))}`;
                }
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
                            <div class="result-meta"><span>${athlete.country_emoji} ${escapeHtml(athlete.country_name)}${athlete.year_of_birth ? ' · ' + athlete.year_of_birth : ''}</span>${badgesHtml(athlete)}</div>
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

function statsBlockHtml(data) {
    if (data == null || data.overall_rating == null) return '';
    return `
        <div class="sel-athlete-stats">
            <div class="sel-stat">
                <span class="sel-stat-num">${data.overall_rating}</span>
                <span class="sel-stat-lbl">Rating</span>
            </div>
            <div class="sel-stat-divider"></div>
            <div class="sel-stat">
                <span class="sel-stat-num">${data.world_rank != null ? '#' + data.world_rank : '-'}</span>
                <span class="sel-stat-lbl">World rank</span>
            </div>
            <div class="sel-stat-divider"></div>
            <div class="sel-stat">
                <span class="sel-stat-num">${data.wins ?? '-'}</span>
                <span class="sel-stat-lbl">Career wins</span>
            </div>
        </div>`;
}

// Refetch stats for the selected athlete under `program` and swap them into
// the existing widget in-place. Called when the user toggles the course
// selector, or when athlete 2's selection restricts the shared programs.
async function refreshAthleteStats(athleteKey, program) {
    const athlete = selectedAthletes[athleteKey];
    if (!athlete) return;
    const selectedDiv = document.getElementById(athleteKey === 'athlete1' ? 'selected1' : 'selected2');
    const statsHost   = selectedDiv?.querySelector('.sel-athlete-details');
    if (!statsHost) return;
    const url = program
        ? `/athlete-compare/athlete/${athlete.id}?program=${encodeURIComponent(program)}`
        : `/athlete-compare/athlete/${athlete.id}`;
    try {
        const res  = await fetch(url);
        const full = await res.json();
        full.id = athlete.id;
        // Preserve programs list from the original fetch — it's program-agnostic
        // and we don't want a transient refresh to drop it.
        selectedAthletes[athleteKey] = { ...athlete, ...full, programs: athlete.programs || full.programs };
        const existing = statsHost.querySelector('.sel-athlete-stats');
        const newHtml  = statsBlockHtml(full);
        if (existing) {
            existing.outerHTML = newHtml;
        } else if (newHtml) {
            statsHost.insertAdjacentHTML('beforeend', newHtml);
        }
    } catch (_) { /* leave existing stats in place */ }
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

    // Fetch full data (rating, world rank, wins, programs). Use the currently
    // selected program if one is set — otherwise the endpoint picks its own
    // default and returns the `active_program` it chose.
    let full = athlete;
    try {
        const url = selectedProgram
            ? `/athlete-compare/athlete/${athlete.id}?program=${encodeURIComponent(selectedProgram)}`
            : `/athlete-compare/athlete/${athlete.id}`;
        const res = await fetch(url);
        full = await res.json();
        full.id = athlete.id;
        selectedAthletes[athleteKey] = { ...athlete, ...full };
    } catch (_) { /* fall back to basic info */ }

    const name         = escapeHtml(full.name         || athlete.name         || '');
    const countryEmoji = full.country_emoji            || athlete.country_emoji || '';
    const countryName  = escapeHtml(full.country_name || athlete.country_name  || '');
    const yob          = full.year_of_birth            || athlete.year_of_birth || '';

    selectedDiv.innerHTML = `
        <div class="sel-athlete-card">
            <img class="sel-athlete-img"
                src="${imgSrc}"
                onerror="this.src='${defaultImg}'"
                alt="${name}">
            <div class="sel-athlete-details">
                <div class="sel-athlete-name">${name} ${countryEmoji}</div>
                ${statsBlockHtml(full)}
            </div>
        </div>
    `;

    // Mark the parent .search-box as populated so the close-button in the navy
    // header becomes visible (CSS gates display on .has-selection).
    const searchBox = selectedDiv.closest('.search-box');
    if (searchBox) searchBox.classList.add('has-selection');

    refreshProgramPicker();
}

function clearSelectedAthlete(athleteKey, searchInput, selectedDiv, searchWrapper) {
    selectedAthletes[athleteKey] = null;
    selectedDiv.classList.remove('active');
    selectedDiv.innerHTML = '';
    const searchBox = selectedDiv.closest('.search-box');
    if (searchBox) searchBox.classList.remove('has-selection');
    if (searchWrapper) searchWrapper.classList.remove('hidden');
    searchInput.value = '';
    searchInput.focus();
    refreshProgramPicker();
}

// Wire the header close-buttons (rendered once in the template) once the DOM is ready.
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.search-box .sel-remove-btn').forEach(btn => {
        const box = btn.closest('.search-box');
        if (!box) return;
        const key = box.dataset.athleteKey;
        if (!key) return;
        btn.addEventListener('click', () => {
            const searchInput   = box.querySelector('.search-input');
            const selectedDiv   = box.querySelector(`#selected${key === 'athlete1' ? 1 : 2}`);
            const searchWrapper = box.querySelector('.search-input-wrapper');
            clearSelectedAthlete(key, searchInput, selectedDiv, searchWrapper);
        });
    });
});

// Intersection of both athletes' available programs. Returns [] until both selected.
function sharedPrograms() {
    const a1 = selectedAthletes.athlete1;
    const a2 = selectedAthletes.athlete2;
    if (!a1 || !a2) return [];
    const a1p = a1.programs || programsFromTags(a1);
    const a2p = a2.programs || programsFromTags(a2);
    return a1p.filter(p => a2p.includes(p));
}

// Render the program picker (or hide it). Only shown when both athletes are
// selected AND they share more than one program; single-program pairs auto-
// select without UI, no-shared pairs surface an inline hint.
function refreshProgramPicker() {
    const picker     = document.getElementById('programPicker');
    const chips      = document.getElementById('programPickerChips');
    const hint       = document.getElementById('programPickerHint');
    const compareBtn = document.getElementById('compareBtn');
    const bothSelected = !!(selectedAthletes.athlete1 && selectedAthletes.athlete2);

    picker.hidden = true;
    chips.innerHTML = '';
    hint.textContent = '';
    hint.hidden = true;

    if (!bothSelected) {
        selectedProgram = null;
        compareBtn.disabled = true;
        return;
    }

    const shared = sharedPrograms();
    if (shared.length === 0) {
        selectedProgram = null;
        compareBtn.disabled = true;
        hint.textContent = "These athletes don't share a program and can't be compared.";
        hint.hidden = false;
        return;
    }

    // Keep a previously-picked program if still valid, else default to first.
    const prevSelected = selectedProgram;
    if (!selectedProgram || !shared.includes(selectedProgram)) {
        selectedProgram = shared[0];
    }
    // Whenever the active program changes, refresh both widgets' stats so the
    // displayed rating / rank / wins reflect the current course.
    if (selectedProgram !== prevSelected) {
        refreshAthleteStats('athlete1', selectedProgram);
        refreshAthleteStats('athlete2', selectedProgram);
    }

    if (shared.length === 1) {
        // One option — no UI needed, just enable Compare.
        compareBtn.disabled = false;
        return;
    }

    // More than one shared program — render radio-chips matching the rest of the site.
    chips.innerHTML = shared.map(p => `
        <input type="radio" name="compare-program" id="program-${p}" value="${p}"${p === selectedProgram ? ' checked' : ''}>
        <label for="program-${p}">${PROGRAM_LABELS[p] || p}</label>
    `).join('');
    chips.querySelectorAll('input[name="compare-program"]').forEach(r => {
        r.addEventListener('change', () => {
            if (r.checked) {
                selectedProgram = r.value;
                refreshAthleteStats('athlete1', selectedProgram);
                refreshAthleteStats('athlete2', selectedProgram);
            }
        });
    });
    picker.hidden = false;
    compareBtn.disabled = false;
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
    const urlProgram = params.get('program');

    if (!a1) return;

    const fetchAthlete = id => fetch(`/athlete-compare/athlete/${encodeURIComponent(id)}`).then(r => r.ok ? r.json() : null);

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
            // Honour ?program= if it's one of the shared options.
            const shared = sharedPrograms();
            if (urlProgram && shared.includes(urlProgram)) {
                selectedProgram = urlProgram;
                refreshProgramPicker();
            }
            if (selectedProgram) await performComparison(/* pushState= */ false);
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
    if (!selectedProgram) return;
    const loadingDiv = document.getElementById('loading');
    const resultsDiv = document.getElementById('comparisonResults');

    loadingDiv.classList.add('active');
    resultsDiv.classList.remove('active');

    const id1 = selectedAthletes.athlete1.id;
    const id2 = selectedAthletes.athlete2.id;
    const program = selectedProgram;

    try {
        const response = await fetch(`/athlete-compare/${id1}/${id2}?program=${encodeURIComponent(program)}`,
                                     { headers: { 'X-Partial': '1' } });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Comparison failed');
        }

        const html = await response.text();
        resultsDiv.innerHTML = html;
        resultsDiv.classList.add('active');

        if (pushState) {
            history.pushState({ a1: id1, a2: id2, program },
                              '', `?a1=${id1}&a2=${id2}&program=${program}`);
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
    // comparison_results.js wires up the chips on load via its IIFE. On re-runs
    // (new athlete pair while on the same page), re-append the script so the
    // newly rendered DOM is wired up fresh.
    const script = document.createElement("script");
    const baseUrl = window.STATIC_BASE_URL || "https://www.static.protridata/";
    script.src = `${baseUrl}js/comparison_results.js?ts=${Date.now()}`;
    document.body.appendChild(script);
    script.onload = () => { comparisonGraphsLoaded = true; };
}

// Initialize.
// Athlete 1: any athlete with at least one rating.
// Athlete 2: restricted to athletes sharing ≥1 program (short/long/AG) with athlete 1.
initSearch('search1', 'results1', 'selected1', 'athlete1');
initSearch('search2', 'results2', 'selected2', 'athlete2',
    () => selectedAthletes.athlete1?.gender,
    () => selectedAthletes.athlete1
        ? (selectedAthletes.athlete1.programs || programsFromTags(selectedAthletes.athlete1))
        : null
);
prefillFromUrl();

document.getElementById('compareBtn').addEventListener('click', () => performComparison());
