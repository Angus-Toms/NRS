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

function initAdvancedFilters() {
    const btn     = document.getElementById("toggleAdvanced");
    const section = document.getElementById("advancedFilters");
    if (!btn || !section) return;

    // Auto-expand only if a non-default advanced filter was actually submitted (check URL params)
    const params = new URLSearchParams(window.location.search);
    const hasActive =
        (params.get("country") ?? "all") !== "all" ||
        parseInt(params.get("yob_start") ?? "1930") !== 1930 ||
        parseInt(params.get("yob_end")   ?? "2010") !== 2010;

    if (hasActive) {
        section.classList.add("open");
        btn.classList.add("open");
    }

    btn.addEventListener("click", () => {
        const isOpen = section.classList.toggle("open");
        btn.classList.toggle("open", isOpen);
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
    if (!cb || !hidden || !text) return;

    cb.addEventListener("change", () => {
        hidden.value     = cb.checked ? "true" : "false";
        text.textContent = cb.checked ? "On" : "Off";
    });
}

initLoadMore();
initAdvancedFilters();
initAgePresets();
initActiveOnlyToggle();
