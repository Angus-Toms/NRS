let raceLeaderboardOffset = 50;

function initAdvancedFilters() {
    const btn     = document.getElementById("toggleAdvanced");
    const section = document.getElementById("advancedFilters");
    if (!btn || !section) return;

    const params = new URLSearchParams(window.location.search);
    const hasActive =
        (params.get("country") ?? "all") !== "all" ||
        (params.get("level")   ?? "all") !== "all";
    if (hasActive) {
        section.classList.add("open");
        btn.classList.add("open");
    }
    btn.addEventListener("click", () => {
        const isOpen = section.classList.toggle("open");
        btn.classList.toggle("open", isOpen);
    });
}

function initLoadMore() {
    const loadMoreBtn = document.getElementById("loadMoreBtn");
    if (!loadMoreBtn) return;

    loadMoreBtn.addEventListener("click", async () => {
        const params = new URLSearchParams(window.location.search);
        params.set("offset", raceLeaderboardOffset);
        try {
            const res = await fetch(`/race-leaderboard/more?${params.toString()}`);
            const html = await res.text();
            document.querySelector(".race-leaderboard-grid").insertAdjacentHTML("beforeend", html);
            raceLeaderboardOffset += 50;
            if (html.trim().length === 0) loadMoreBtn.style.display = "none";
        } catch (err) {
            console.error("Error loading more races", err);
        }
    });
}

initAdvancedFilters();
initLoadMore();
