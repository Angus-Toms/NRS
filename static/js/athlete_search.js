// Search functionality
let searchTimeout;
const searchInput = document.getElementById('athleteSearch');
const searchResults = document.getElementById('searchResults');

if (searchInput && searchResults) {
    searchInput.addEventListener('input', function() {
        const query = this.value.trim();
        
        // Clear previous timeout
        clearTimeout(searchTimeout);
        
        // Hide results if query is too short
        if (query.length < 3) {
            searchResults.style.display = 'none';
            return;
        }
        
        // Show loading state
        searchResults.innerHTML = '<div class="search-loading">Searching...</div>';
        searchResults.style.display = 'block';
        
        // Debounce search
        searchTimeout = setTimeout(() => {
            performSearch(query);
        }, 300);
    });
}

async function performSearch(query) {
    try {
        const response = await fetch(`/athletes/search?q=${encodeURIComponent(query)}`);
        const results = await response.json();
        
        displayResults(results);
    } catch (error) {
        console.error('Search error:', error);
        searchResults.innerHTML = '<div class="no-results">Error performing search</div>';
    }
}

function displayResults(results) {
    if (results.length === 0) {
        searchResults.innerHTML = '<div class="no-results">No athletes found</div>';
        return;
    }

    const baseUrl = window.STATIC_BASE_URL || '';
    const defaultImg = `${baseUrl}imgs/default_user.jpg`;

    const html = results.map(athlete => {
        const imgSrc = `${baseUrl}athlete_imgs/128/${athlete.athlete_id}.webp`;
        return `
        <a href="/athlete/${athlete.athlete_id}" class="search-result-item">
            <img class="result-avatar" src="${imgSrc}" onerror="this.src='${defaultImg}'" alt="${escapeHtml(athlete.name)}">
            <div class="result-info">
                <div class="result-name">${escapeHtml(athlete.name)}</div>
                <div class="result-country">${athlete.country} ${escapeHtml(athlete.country_full)}</div>
                <hr class="result-divider">
                <div class="result-yob">${athlete.year_of_birth || '—'}</div>
            </div>
        </a>`;
    }).join('');

    searchResults.innerHTML = html;
}

// Close search results when clicking outside
document.addEventListener('click', function(event) {
    if (!searchResults || !searchInput) return;
    if (!event.target.closest('.search-container')) {
        searchResults.style.display = 'none';
    }
});

// Reopen results when focusing on input with existing query
if (searchInput && searchResults) {
    searchInput.addEventListener('focus', function() {
        if (this.value.trim().length >= 3 && searchResults.innerHTML.trim() !== '') {
            searchResults.style.display = 'block';
        }
    });
}
