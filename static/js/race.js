// Chart.js global defaults — match site typography and colour palette
Chart.defaults.font.family = "'Plus Jakarta Sans', sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.color = '#6b7280'; // --text-light

Chart.defaults.plugins.tooltip.backgroundColor = '#1a1a2e'; // --navy
Chart.defaults.plugins.tooltip.titleColor = '#ffffff';
Chart.defaults.plugins.tooltip.bodyColor = 'rgba(255,255,255,0.7)';
Chart.defaults.plugins.tooltip.padding = { x: 10, y: 8 };
Chart.defaults.plugins.tooltip.cornerRadius = 6;
Chart.defaults.plugins.tooltip.displayColors = false;
Chart.defaults.plugins.tooltip.titleFont = { family: "'Plus Jakarta Sans', sans-serif", weight: '600', size: 12 };
Chart.defaults.plugins.tooltip.bodyFont = { family: "'Plus Jakarta Sans', sans-serif", size: 11 };

// Sort table click listeners — safe to call multiple times after partial swaps
function initSortableListeners() {
    document.querySelectorAll('table.sortable-table').forEach(table => {
        const headers = table.querySelectorAll('th.sortable');
        headers.forEach((header, index) => {
            header.addEventListener('click', () => {
                const isAsc = header.classList.contains('asc');
                headers.forEach(h => h.classList.remove('asc', 'desc'));
                header.classList.add(isAsc ? 'desc' : 'asc');
                sortTable(table, index, !isAsc);
            });
        });
    });
}

// Chart initialisation — destroys existing instances before recreating
function initRaceCharts() {
    const charts = [
        // Time histograms
        { id: 'overall-times-canvas', data: 'overall-time-hist-data', label: 'Overall Time', fmt: 'time' },
        { id: 'swim-times-canvas',    data: 'swim-time-hist-data',    label: 'Swim Time',    fmt: 'time' },
        { id: 'bike-times-canvas',    data: 'bike-time-hist-data',    label: 'Bike Time',    fmt: 'time' },
        { id: 'run-times-canvas',     data: 'run-time-hist-data',     label: 'Run Time',     fmt: 'time' },
        { id: 't1-times-canvas',      data: 't1-time-hist-data',      label: 'T1 Time',      fmt: 'time' },
        { id: 't2-times-canvas',      data: 't2-time-hist-data',      label: 'T2 Time',      fmt: 'time' },
        // Rating histograms
        { id: 'overall-ratings-canvas',    data: 'overall-ratings-hist-data',    label: 'Overall Rating',    fmt: 'rating' },
        { id: 'swim-ratings-canvas',       data: 'swim-ratings-hist-data',       label: 'Swim Rating',       fmt: 'rating' },
        { id: 'bike-ratings-canvas',       data: 'bike-ratings-hist-data',       label: 'Bike Rating',       fmt: 'rating' },
        { id: 'run-ratings-canvas',        data: 'run-ratings-hist-data',        label: 'Run Rating',        fmt: 'rating' },
        { id: 'transition-ratings-canvas', data: 'transition-ratings-hist-data', label: 'Transition Rating', fmt: 'rating' },
    ];

    charts.forEach(({ id, data: dataId, label, fmt }) => {
        const canvas = document.getElementById(id);
        if (!canvas) return;
        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();
        const data = getJSON(dataId);
        if (!data || !data.datasets) return;

        new Chart(canvas, {
            type: 'bar',
            data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                parsing: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: 'index',
                        callbacks: {
                            title: ctx => ctx[0].raw.label,
                            label: ctx => `Athletes: ${ctx.raw.y}`,
                        }
                    }
                },
                scales: {
                    x: {
                        beginAtZero: false,
                        type: 'linear',
                        title: { display: true, text: label },
                        ticks: fmt === 'time'
                            ? { callback: v => isNaN(+v) ? v : formatTime(+v) }
                            : { stepSize: 100, callback: v => Math.round(v) }
                    },
                    y: {
                        beginAtZero: true,
                        type: 'linear',
                        title: { display: true, text: 'Number of Athletes' }
                    }
                }
            }
        });
    });
}
