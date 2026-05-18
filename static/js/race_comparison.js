// Race comparison page. Mirrors the athlete-compare structure: pick race 1,
// then race 2 is filtered to the same course and gender, then fire a
// `/race-compare/{id1}/{id2}` fetch which returns the results partial.
const rcSelected = { race1: null, race2: null };

function rcDebounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function rcFormatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

// Other race must share (course, gender) with race 1 for an apples-to-apples
// comparison. Returns null when no race 1 is chosen yet.
function rcFilter() {
    const r1 = rcSelected.race1;
    if (!r1) return null;
    return { course: r1.course, gender: r1.gender };
}

function rcInitSearch(key) {
    const n = key === 'race1' ? 1 : 2;
    const input    = document.getElementById(`rc-search${n}`);
    const results  = document.getElementById(`rc-results${n}`);
    const selected = document.getElementById(`rc-selected${n}`);
    const wrapper  = input.closest('.search-input-wrapper');

    const performSearch = rcDebounce(async (q) => {
        if (q.length < 2) { results.classList.remove('active'); return; }
        const params = new URLSearchParams({ q });
        if (key === 'race2') {
            const f = rcFilter();
            if (!f) return;  // race 1 not picked yet
            params.set('course', f.course || '');
            params.set('gender', f.gender || '');
        }
        try {
            const res  = await fetch(`/race-compare/search?${params.toString()}`);
            const data = await res.json();
            if (!data || !data.length) {
                results.innerHTML = '<div class="search-result-item">No races found</div>';
                results.classList.add('active');
                return;
            }
            results.innerHTML = data.map(race => `
                <div class="search-result-item rc-search-item"
                     data-id="${race.race_id}">
                    <div class="result-info">
                        <div class="result-name">${escapeHtml(race.race_title)}</div>
                        <div class="result-meta">
                            <span>${rcFormatDate(race.race_date)}</span>
                            <span class="result-meta-sep">·</span>
                            <span>${escapeHtml(race.venue ? race.venue + ', ' : '')}${escapeHtml(race.country || '')}</span>
                            <span class="result-meta-sep">·</span>
                            <span>${escapeHtml(race.prog_name || '')}</span>
                        </div>
                    </div>
                </div>`).join('');
            results.classList.add('active');
            results.querySelectorAll('.rc-search-item').forEach(item => {
                item.addEventListener('click', () => {
                    rcSelectRace(key, parseInt(item.dataset.id, 10));
                });
            });
        } catch (e) { console.error('Race search error', e); }
    }, 250);

    input.addEventListener('input', e => performSearch(e.target.value.trim()));
    document.addEventListener('click', e => {
        if (!input.contains(e.target) && !results.contains(e.target)) {
            results.classList.remove('active');
        }
    });
}

async function rcSelectRace(key, raceId) {
    const n = key === 'race1' ? 1 : 2;
    const input    = document.getElementById(`rc-search${n}`);
    const results  = document.getElementById(`rc-results${n}`);
    const selected = document.getElementById(`rc-selected${n}`);
    const wrapper  = input.closest('.search-input-wrapper');

    input.value = '';
    results.classList.remove('active');
    if (wrapper) wrapper.classList.add('hidden');
    selected.classList.add('active');
    selected.innerHTML = '<div style="padding:0.5rem 0;color:var(--text-lighter);font-size:0.82rem;">Loading...</div>';

    const res = await fetch(`/race-compare/race/${raceId}`);
    if (!res.ok) {
        selected.innerHTML = '<div style="padding:0.5rem;color:var(--error-color)">Could not load race.</div>';
        return;
    }
    const race = await res.json();
    rcSelected[key] = race;

    selected.innerHTML = `
        <div class="rc-sel-card">
            <div class="rc-sel-title">${escapeHtml(race.race_title)}</div>
            <div class="rc-sel-meta">${rcFormatDate(race.race_date)} · ${escapeHtml(race.venue ? race.venue + ', ' : '')}${escapeHtml(race.country || '')}</div>
            <div class="rc-sel-stats">
                <div class="rc-sel-stat"><span class="rc-sel-stat-num">${race.athletes}</span><span class="rc-sel-stat-lbl">Athletes</span></div>
                <div class="rc-sel-stat"><span class="rc-sel-stat-num">${race.finishers}</span><span class="rc-sel-stat-lbl">Finishers</span></div>
                <div class="rc-sel-stat"><span class="rc-sel-stat-num">${race.dnfs}</span><span class="rc-sel-stat-lbl">DNFs</span></div>
            </div>
        </div>`;
    const box = selected.closest('.search-box');
    if (box) box.classList.add('has-selection');

    rcRefreshState();
}

function rcClearRace(key) {
    rcSelected[key] = null;
    const n = key === 'race1' ? 1 : 2;
    const input    = document.getElementById(`rc-search${n}`);
    const results  = document.getElementById(`rc-results${n}`);
    const selected = document.getElementById(`rc-selected${n}`);
    const wrapper  = input.closest('.search-input-wrapper');

    selected.classList.remove('active');
    selected.innerHTML = '';
    if (wrapper) wrapper.classList.remove('hidden');
    const box = selected.closest('.search-box');
    if (box) box.classList.remove('has-selection');

    // Clearing race 1 invalidates race 2 (filter changes), so reset both.
    if (key === 'race1') {
        if (rcSelected.race2) rcClearRace('race2');
        document.getElementById('rc-search2').disabled = true;
        document.getElementById('rc-search2').placeholder = 'Pick race 1 first...';
    }
    input.value = '';
    rcRefreshState();
}

function rcRefreshState() {
    const btn  = document.getElementById('rc-compareBtn');
    const hint = document.getElementById('rc-pickerHint');
    const search2 = document.getElementById('rc-search2');

    if (rcSelected.race1) {
        search2.disabled = false;
        search2.placeholder = `Search another ${rcSelected.race1.gender === 'male' ? "men's" : "women's"} ${rcSelected.race1.course === 'long' ? 'long-course' : 'short-course'} race...`;
    } else {
        search2.disabled = true;
        search2.placeholder = 'Pick race 1 first...';
    }

    hint.hidden = true;
    if (rcSelected.race1 && rcSelected.race2) {
        if (rcSelected.race1.race_id === rcSelected.race2.race_id) {
            hint.textContent = 'Pick two different races to compare.';
            hint.hidden = false;
            btn.disabled = true;
            return;
        }
        btn.disabled = false;
    } else {
        btn.disabled = true;
    }
}

async function rcPerformComparison(pushState = true) {
    const r1 = rcSelected.race1, r2 = rcSelected.race2;
    if (!r1 || !r2) return;
    const loading = document.getElementById('rc-loading');
    const out     = document.getElementById('rc-results');

    loading.classList.add('active');
    out.classList.remove('active');

    try {
        const res = await fetch(`/race-compare/${r1.race_id}/${r2.race_id}`, {
            headers: { 'X-Partial': '1' },
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Comparison failed');
        }
        out.innerHTML = await res.text();
        out.classList.add('active');
        if (pushState) {
            history.pushState({ r1: r1.race_id, r2: r2.race_id }, '',
                              `?r1=${r1.race_id}&r2=${r2.race_id}`);
        }
        rcLoadResultsJs();
    } catch (e) {
        const err = document.getElementById('rc-errorMsg');
        err.textContent = e.message;
        err.classList.add('active');
        setTimeout(() => err.classList.remove('active'), 5000);
    } finally {
        loading.classList.remove('active');
    }
}

function rcLoadResultsJs() {
    const script  = document.createElement('script');
    const baseUrl = window.STATIC_BASE_URL || '';
    script.src = `${baseUrl}js/race_comparison_results.js?ts=${Date.now()}`;
    document.body.appendChild(script);
}

async function rcPrefillFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const r1 = params.get('r1');
    const r2 = params.get('r2');
    if (r1) await rcSelectRace('race1', parseInt(r1, 10));
    if (r1 && r2) {
        await rcSelectRace('race2', parseInt(r2, 10));
        if (rcSelected.race1 && rcSelected.race2) rcPerformComparison(false);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    rcInitSearch('race1');
    rcInitSearch('race2');
    document.querySelectorAll('.search-box .sel-remove-btn').forEach(btn => {
        const box = btn.closest('.search-box');
        if (!box) return;
        btn.addEventListener('click', () => rcClearRace(box.dataset.raceKey));
    });
    document.getElementById('rc-compareBtn').addEventListener('click', () => rcPerformComparison());
    rcPrefillFromUrl();
});
