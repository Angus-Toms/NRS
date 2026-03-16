// Search functionality
let searchTimeout;
const searchInput = document.getElementById('raceSearch');
const searchResults = document.getElementById('searchResults');

if (searchInput && searchResults) {
    searchInput.addEventListener('input', function() {
        const query = this.value.trim();
        clearTimeout(searchTimeout);
        if (query.length < 2) {
            searchResults.style.display = 'none';
            return;
        }
        searchResults.innerHTML = '<div class="search-loading">Searching...</div>';
        searchResults.style.display = 'block';
        searchTimeout = setTimeout(() => performSearch(query), 300);
    });
}

async function performSearch(query) {
    try {
        const response = await fetch(`/races/search?q=${encodeURIComponent(query)}`);
        const results = await response.json();
        displayResults(results);
    } catch (error) {
        console.error('Search error:', error);
        searchResults.innerHTML = '<div class="no-results">Error performing search</div>';
    }
}

function displayResults(results) {
    if (results.length === 0) {
        searchResults.innerHTML = '<div class="no-results">No events found</div>';
        return;
    }

    const PILL_LIMIT = 4;
    const html = results.map(event => {
        const races = event.races || [];
        const visibleRaces = races.slice(0, PILL_LIMIT);
        const overflow = races.length - PILL_LIMIT;

        const pillsHtml = visibleRaces.map(r =>
            `<span class="race-pill">${escapeHtml(r.prog_name)}</span>`
        ).join('');
        const overflowHtml = overflow > 0
            ? `<span class="race-pill race-pill-more">+${overflow}</span>`
            : '';

        return `
            <a href="/event/${event.event_id}" class="search-result-item">
                <div class="result-name">${escapeHtml(event.name)}</div>
                <div class="result-meta">
                    ${escapeHtml(event.country)}${event.venue ? ` · ${escapeHtml(event.venue)}` : ''} · ${event.event_date}
                </div>
                ${races.length > 0 ? `<div class="result-races">${pillsHtml}${overflowHtml}</div>` : ''}
            </a>
        `;
    }).join('');

    searchResults.innerHTML = html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

document.addEventListener('click', function(event) {
    if (!searchResults || !searchInput) return;
    if (!event.target.closest('.search-container')) {
        searchResults.style.display = 'none';
    }
});

if (searchInput && searchResults) {
    searchInput.addEventListener('focus', function() {
        if (this.value.trim().length >= 2 && searchResults.innerHTML.trim() !== '') {
            searchResults.style.display = 'block';
        }
    });
}

// Load more events
function initLoadMore() {
    const loadMoreBtn = document.getElementById('loadMoreRaces');
    const grid = document.getElementById('raceGrid');
    const pageSize = 30;

    if (!loadMoreBtn || !grid) return;

    let offset = parseInt(loadMoreBtn.dataset.offset, 10) || 0;

    loadMoreBtn.addEventListener('click', async () => {
        loadMoreBtn.disabled = true;
        const originalText = loadMoreBtn.textContent;
        loadMoreBtn.textContent = 'Loading...';

        try {
            const res = await fetch(`/races/more?offset=${offset}`);
            if (!res.ok) throw new Error('Failed to fetch');
            const html = await res.text();
            if (!html.trim()) {
                loadMoreBtn.style.display = 'none';
                return;
            }
            grid.insertAdjacentHTML('beforeend', html);
            offset += pageSize;
            loadMoreBtn.dataset.offset = offset;
            loadMoreBtn.textContent = originalText;
        } catch (err) {
            console.error('Error loading more events', err);
            loadMoreBtn.textContent = 'Try again';
        } finally {
            loadMoreBtn.disabled = false;
        }
    });
}

initLoadMore();
