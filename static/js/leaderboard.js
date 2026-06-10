let leaderboardOffset = 50;

function initLoadMore() {
    const loadMoreBtn = document.getElementById("loadMoreBtn");
    if (!loadMoreBtn) return;

    loadMoreBtn.addEventListener("click", async () => {
        const params = new URLSearchParams(window.location.search);
        params.append("offset", leaderboardOffset);

        try {
            const res = await fetch(`/athlete-leaderboard/more?${params.toString()}`);
            const html = await res.text();
            document.querySelector(".leaderboard-grid").insertAdjacentHTML("beforeend", html);
            leaderboardOffset += 50;
            if (html.trim().length === 0) loadMoreBtn.style.display = "none";
        } catch (err) {
            console.error("Error loading more athletes", err);
        }
    });
}

function initFilterPanelToggle() {
    const btn   = document.getElementById("filterToggle");
    const panel = document.getElementById("filterPanel");
    if (!btn || !panel) return;
    btn.addEventListener("click", () => {
        const open = panel.classList.toggle("open");
        btn.setAttribute("aria-expanded", open);
    });
}

// Auto-submit the filter form whenever any chip/select/toggle changes. The
// active-only checkbox is the visible UI; the hidden input next to it holds
// the form value and is updated separately by initActiveOnlyToggle().
function initFilterAutoSubmit() {
    const form = document.getElementById("filtersForm");
    if (!form) return;
    form.addEventListener("change", e => {
        if (e.target.id === "active-only-cb") return; // bubble waits for hidden input update
        form.submit();
    });
}

function initAgePresets() {
    const yobStart = document.getElementById("yob_start");
    const yobEnd   = document.getElementById("yob_end");
    if (!yobStart || !yobEnd) return;

    const currentYear = new Date().getFullYear();

    document.querySelectorAll(".btn-age-preset").forEach(btn => {
        const maxAge = parseInt(btn.dataset.age);
        // Highlight if the inputs currently match this preset
        if (parseInt(yobStart.value) === currentYear - maxAge && parseInt(yobEnd.value) === 2010) {
            btn.classList.add("active");
        }

        btn.addEventListener("click", () => {
            yobStart.value = currentYear - maxAge;
            yobEnd.value   = 2010;
            document.querySelectorAll(".btn-age-preset").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            const form = document.getElementById("filtersForm");
            if (form) form.submit();
        });
    });

    // Clear active state if the user manually edits the inputs
    [yobStart, yobEnd].forEach(input => {
        input.addEventListener("input", () => {
            document.querySelectorAll(".btn-age-preset").forEach(b => b.classList.remove("active"));
        });
    });
}

function initActiveOnlyToggle() {
    const cb     = document.getElementById("active-only-cb");
    const hidden = document.getElementById("active-only-input");
    const text   = document.getElementById("active-only-text");
    const form   = document.getElementById("filtersForm");
    if (!cb || !hidden || !text) return;

    cb.addEventListener("change", () => {
        hidden.value     = cb.checked ? "true" : "false";
        text.textContent = cb.checked ? "On" : "Off";
        if (form) form.submit();
    });
}

// Keep the download button's URL in sync with the active filters.
function initDownloadUrl() {
    const btn  = document.getElementById("downloadBtn");
    const form = document.getElementById("filtersForm");
    const setUrl = () => {
        const params = new URLSearchParams(new FormData(form));
        btn.dataset.downloadUrl = `/api/v1/leaderboard?${params.toString()}`;
    };
    setUrl();
    form.addEventListener("change", setUrl);
}

initLoadMore();
initFilterPanelToggle();
initFilterAutoSubmit();
initAgePresets();
initActiveOnlyToggle();
initDownloadUrl();
