let raceLeaderboardOffset = 50;

function initFilterPanelToggle() {
    const btn   = document.getElementById("filterToggle");
    const panel = document.getElementById("filterPanel");
    if (!btn || !panel) return;
    btn.addEventListener("click", () => {
        const open = panel.classList.toggle("open");
        btn.setAttribute("aria-expanded", open);
    });
}

function initFilterAutoSubmit() {
    const form = document.querySelector(".filters-card form");
    if (!form) return;
    form.addEventListener("change", () => form.submit());
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

initFilterPanelToggle();
initFilterAutoSubmit();
initLoadMore();
