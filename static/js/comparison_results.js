// --- Year band background plugin for aligned view ---
// Draws alternating grey/white year columns with "Year N" labels inside the chart area.
const yearBandsPlugin = {
    id: 'yearBands',
    beforeDraw(chart) {
        if (!isAligned) return;
        const xScale = chart.scales.x;
        const yScale = chart.scales.y;
        if (!xScale || !yScale) return;

        const ctx = chart.ctx;
        const top = yScale.top;
        const bottom = yScale.bottom;
        const numYears = Math.ceil(xScale.max / msPerYear) + 1;

        ctx.save();
        for (let i = 0; i < numYears; i++) {
            const x1 = Math.max(xScale.getPixelForValue(i * msPerYear), xScale.left);
            const x2 = Math.min(xScale.getPixelForValue((i + 1) * msPerYear), xScale.right);
            if (x2 <= x1) continue;

            if (i % 2 === 0) {
                ctx.fillStyle = 'rgba(0,0,0,0.04)';
                ctx.fillRect(x1, top, x2 - x1, bottom - top);
            }

            ctx.fillStyle = 'rgba(0,0,0,0.25)';
            ctx.font = '10px Arial';
            ctx.textAlign = 'center';
            ctx.fillText(`Year ${i + 1}`, (x1 + x2) / 2, bottom - 4);
        }
        ctx.restore();
    }
};

// --- Handle alignment request ---
function alignChartStarts() {
    isAligned = !isAligned;

    const button = document.getElementById('align-btn');
    button.textContent = isAligned ? "Show Actual Dates" : "Align Start Dates";
    initOverallChart();
    initSwimChart();
    initBikeChart();
    initRunChart();
    initTransitionChart();
    initOverallRankingsChart();
    initSwimRankingsChart();
    initBikeRankingsChart();
    initRunRankingsChart();
    initTransitionRankingsChart();
}

// --- Rating comparison graphs ---
// Store references to charts, allows for reloading once they've been created
let overallRatingsChart = null;
let swimRatingsChart = null;
let bikeRatingsChart = null;
let runRatingsChart = null;
let transitionRatingsChart = null;

// --- Rankings comparison graphs ---
let overallRankingsChart = null;
let swimRankingsChart = null;
let bikeRankingsChart = null;
let runRankingsChart = null;
let transitionRankingsChart = null;

// Track alignment state
let isAligned = false;
const msPerYear = 365.25 * 24 * 60 * 60 * 1000;

// Date of athlete's first races for alignment (set by initOverallChart, used by all aligned charts)
let athleteFirstDates = null;

// Shared tooltip title callback
function tooltipTitle(context) {
    const dataPoint = context[0].raw;
    if (isAligned && athleteFirstDates) {
        const base = athleteFirstDates[context[0].datasetIndex];
        const date = new Date(Number(dataPoint.x) + base);
        return [dataPoint.race_name, date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })];
    }
    return [dataPoint.race_name, new Date(dataPoint.x).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })];
}

function alignedXAxis(withMin) {
    const cfg = {
        type: 'linear',
        ticks: { display: false },
        title: { display: true, text: 'Year' }
    };
    if (withMin) cfg.min = 0;
    return cfg;
}

const timeXAxis = {
    type: 'time',
    time: { unit: 'year', tooltipFormat: 'dd-MM-yyyy' },
    grid: { display: false },
    ticks: { display: false },
    title: { display: false }
};

// --- Rating chart init functions ---

function initOverallChart() {
    const ctx = document.getElementById('overall-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('overall-ratings-data');

    if (isAligned) {
        const firstDates = data.datasets.map(dataset =>
            Math.min(...dataset.data.map(d => new Date(d.x).getTime()))
        );
        athleteFirstDates = firstDates;
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - firstDates[i]
            }));
        });
    }

    if (overallRatingsChart) overallRatingsChart.destroy();

    overallRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": " + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(true) : timeXAxis,
                y: { beginAtZero: false, title: { display: true, text: 'Rating' } }
            }
        }
    });
}

function initSwimChart() {
    const ctx = document.getElementById('swim-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('swim-ratings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (swimRatingsChart) swimRatingsChart.destroy();

    swimRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": " + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(false) : timeXAxis,
                y: { beginAtZero: false, title: { display: true, text: 'Rating' } }
            }
        }
    });
}

function initBikeChart() {
    const ctx = document.getElementById('bike-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('bike-ratings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (bikeRatingsChart) bikeRatingsChart.destroy();

    bikeRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": " + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(false) : timeXAxis,
                y: { beginAtZero: false, title: { display: true, text: 'Rating' } }
            }
        }
    });
}

function initRunChart() {
    const ctx = document.getElementById('run-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('run-ratings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (runRatingsChart) runRatingsChart.destroy();

    runRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": " + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(false) : timeXAxis,
                y: { beginAtZero: false, title: { display: true, text: 'Rating' } }
            }
        }
    });
}

function initTransitionChart() {
    const ctx = document.getElementById('transition-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('transition-ratings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (transitionRatingsChart) transitionRatingsChart.destroy();

    transitionRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": " + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(false) : timeXAxis,
                y: { beginAtZero: false, title: { display: true, text: 'Rating' } }
            }
        }
    });
}

// --- Rankings chart init functions ---
// y-axis is reversed: rank 1 (best) at top
// Uses athleteFirstDates set by initOverallChart for alignment

function initOverallRankingsChart() {
    const ctx = document.getElementById('overall-rankings-canvas');
    if (!ctx) return;

    const data = getJSON('overall-rankings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (overallRankingsChart) overallRankingsChart.destroy();

    overallRankingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            clip: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": #" + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(true) : timeXAxis,
                y: { reverse: true, min: 1, ticks: { stepSize: 1, callback: value => '#' + value }, title: { display: true, text: 'Ranking' } }
            }
        }
    });
}

function initSwimRankingsChart() {
    const ctx = document.getElementById('swim-rankings-canvas');
    if (!ctx) return;

    const data = getJSON('swim-rankings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (swimRankingsChart) swimRankingsChart.destroy();

    swimRankingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            clip: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": #" + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(false) : timeXAxis,
                y: { reverse: true, min: 1, ticks: { stepSize: 1, callback: value => '#' + value }, title: { display: true, text: 'Ranking' } }
            }
        }
    });
}

function initBikeRankingsChart() {
    const ctx = document.getElementById('bike-rankings-canvas');
    if (!ctx) return;

    const data = getJSON('bike-rankings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (bikeRankingsChart) bikeRankingsChart.destroy();

    bikeRankingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            clip: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": #" + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(false) : timeXAxis,
                y: { reverse: true, min: 1, ticks: { stepSize: 1, callback: value => '#' + value }, title: { display: true, text: 'Ranking' } }
            }
        }
    });
}

function initRunRankingsChart() {
    const ctx = document.getElementById('run-rankings-canvas');
    if (!ctx) return;

    const data = getJSON('run-rankings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (runRankingsChart) runRankingsChart.destroy();

    runRankingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            clip: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": #" + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(false) : timeXAxis,
                y: { reverse: true, min: 1, ticks: { stepSize: 1, callback: value => '#' + value }, title: { display: true, text: 'Ranking' } }
            }
        }
    });
}

function initTransitionRankingsChart() {
    const ctx = document.getElementById('transition-rankings-canvas');
    if (!ctx) return;

    const data = getJSON('transition-rankings-data');

    if (isAligned && athleteFirstDates) {
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - athleteFirstDates[i]
            }));
        });
    }

    if (transitionRankingsChart) transitionRankingsChart.destroy();

    transitionRankingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [yearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            clip: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: tooltipTitle,
                        label: context => context.dataset.label + ": #" + context.parsed.y
                    }
                }
            },
            scales: {
                x: isAligned ? alignedXAxis(false) : timeXAxis,
                y: { reverse: true, min: 1, ticks: { stepSize: 1, callback: value => '#' + value }, title: { display: true, text: 'Ranking' } }
            }
        }
    });
}

// Init all charts on page load
initOverallChart();
initSwimChart();
initBikeChart();
initRunChart();
initTransitionChart();
initOverallRankingsChart();
initSwimRankingsChart();
initBikeRankingsChart();
initRunRankingsChart();
initTransitionRankingsChart();
