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
}

// --- Rating comparison graphs ---
// Store references to charts, allows for reloading once they've been created
let overallRatingsChart = null;  
let swimRatingsChart = null;
let bikeRatingsChart = null;
let runRatingsChart = null;
let transitionRatingsChart = null;

// Track alignment state
let isAligned = false;
const msPerYear = 365.25 * 24 * 60 * 60 * 1000;

// Date of athlete's first races for alignment
let athleteFirstDates = null;

// --- Initialisation functions for all graphs ---
function initOverallChart() {
    const ctx = document.getElementById('overall-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('overall-ratings-data');

    // Align data if needed
    if (isAligned) {
        const firstDates = data.datasets.map(dataset => {
            const dates = dataset.data.map(d => new Date(d.x).getTime());
            return Math.min(...dates);
        });
        athleteFirstDates = firstDates;
        data.datasets.forEach((dataset, i) => {
            dataset.data = dataset.data.map(point => ({
                ...point,
                x: new Date(point.x).getTime() - firstDates[i]
            }));
        });
    }

    if (overallRatingsChart) {
        overallRatingsChart.destroy();
    }

    overallRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: {
                line: {
                    tension: 0
                }
            },
            plugins: {
                legend: { display: true },
                tooltip: { 
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: function(context) {
                            const dataPoint = context[0].raw;
                            if (isAligned) {
                                const baseDate = athleteFirstDates ? athleteFirstDates[context[0].datasetIndex] : null;
                                if (baseDate) {
                                    const date = new Date(Number(dataPoint.x) + baseDate);
                                    return [
                                        dataPoint.race_name,
                                        date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                                    ];
                                }
                                return [dataPoint.race_name, ''];
                            }
                            const date = new Date(dataPoint.x);
                            return [
                                dataPoint.race_name,
                                date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                            ];
                        },
                        label: function(context) {
                            return context.dataset.label + ": " + context.parsed.y;
                        }
                    }
                },
            },
            scales: {
                x: isAligned ? {
                    type: 'linear',
                    min: 0,
                    ticks: {
                        stepSize: msPerYear,
                        callback: function(value) {
                            const yearIndex = Math.floor(Number(value) / msPerYear) + 1;
                            return `Season ${yearIndex}`;
                        }
                    },
                    title: { display: true, text: 'Year' }
                } : {
                    type: 'time',
                    time: { unit: 'year', tooltipFormat: 'dd-MM-yyyy' },
                    grid: { display: false },
                    ticks: { display: false },
                    title: { display: false }
                },
                y: {
                    beginAtZero: false,
                    title: { display: true, text: 'Rating' }
                }
            }
        }
    });
}

function initSwimChart() {
    const ctx = document.getElementById('swim-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('swim-ratings-data');

    // Align data if needed
        if (isAligned) {
            const firstDates = data.datasets.map(dataset => {
                const dates = dataset.data.map(d => new Date(d.x).getTime());
                return Math.min(...dates);
            });

            data.datasets.forEach((dataset, i) => {
                dataset.data = dataset.data.map(point => ({
                    ...point,
                    x: new Date(point.x).getTime() - firstDates[i]
                }));
            });
        }

    if (swimRatingsChart) {
        swimRatingsChart.destroy();
    }

    swimRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: {
                line: {
                    tension: 0
                }
            },
            plugins: {
                legend: { display: true },
                tooltip: { 
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: function(context) {
                            const dataPoint = context[0].raw;
                            if (isAligned) {
                                const baseDate = athleteFirstDates ? athleteFirstDates[context[0].datasetIndex] : null;
                                if (baseDate) {
                                    const date = new Date(Number(dataPoint.x) + baseDate);
                                    return [
                                        dataPoint.race_name,
                                        date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                                    ];
                                }
                                return [dataPoint.race_name, ''];
                            }
                            const date = new Date(dataPoint.x);
                            return [
                                dataPoint.race_name,
                                date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                            ];
                        },
                        label: function(context) {
                            return context.dataset.label + ": " + context.parsed.y;
                        }
                    }
                },
            },
            scales: {
                x: isAligned ? {
                    type: 'linear',
                    ticks: {
                        callback: function(value) {
                            const yearIndex = Math.floor(Number(value) / msPerYear) + 1;
                            return `Season ${yearIndex}`;
                        }
                    },
                    title: { display: true, text: 'Year' }
                } : {
                    type: 'time',
                    time: { unit: 'year', tooltipFormat: 'dd-MM-yyyy' },
                    grid: { display: false },
                    ticks: { display: false },
                    title: { display: false }
                },
                y: {
                    beginAtZero: false,
                    title: { display: true, text: 'Rating' }
                }
            }
        }
    });
}

function initBikeChart() {
    const ctx = document.getElementById('bike-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('bike-ratings-data');

    // Align data if needed
        if (isAligned) {
            const firstDates = data.datasets.map(dataset => {
                const dates = dataset.data.map(d => new Date(d.x).getTime());
                return Math.min(...dates);
            });

            data.datasets.forEach((dataset, i) => {
                dataset.data = dataset.data.map(point => ({
                    ...point,
                    x: new Date(point.x).getTime() - firstDates[i]
                }));
            });
        }

    if (bikeRatingsChart) {
        bikeRatingsChart.destroy();
    }

    bikeRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: {
                line: {
                    tension: 0
                }
            },
            plugins: {
                legend: { display: true },
                tooltip: { 
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: function(context) {
                            const dataPoint = context[0].raw;
                            if (isAligned) {
                                const baseDate = athleteFirstDates ? athleteFirstDates[context[0].datasetIndex] : null;
                                if (baseDate) {
                                    const date = new Date(Number(dataPoint.x) + baseDate);
                                    return [
                                        dataPoint.race_name,
                                        date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                                    ];
                                }
                                return [dataPoint.race_name, ''];
                            }
                            const date = new Date(dataPoint.x);
                            return [
                                dataPoint.race_name,
                                date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                            ];
                        },
                        label: function(context) {
                            return context.dataset.label + ": " + context.parsed.y;
                        }
                    }
                },
            },
            scales: {
                x: isAligned ? {
                    type: 'linear',
                    ticks: {
                        callback: function(value) {
                            const yearIndex = Math.floor(Number(value) / msPerYear) + 1;
                            return `Season ${yearIndex}`;
                        }
                    },
                    title: { display: true, text: 'Year' }
                } : {
                    type: 'time',
                    time: { unit: 'year', tooltipFormat: 'dd-MM-yyyy' },
                    grid: { display: false },
                    ticks: { display: false },
                    title: { display: false }
                },
                y: {
                    beginAtZero: false,
                    title: { display: true, text: 'Rating' }
                }
            }
        }
    });
}

function initRunChart() {
    const ctx = document.getElementById('run-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('run-ratings-data');

    if (runRatingsChart) {
        runRatingsChart.destroy();
    }

    // Align data if needed
        if (isAligned) {
            const firstDates = data.datasets.map(dataset => {
                const dates = dataset.data.map(d => new Date(d.x).getTime());
                return Math.min(...dates);
            });

            data.datasets.forEach((dataset, i) => {
                dataset.data = dataset.data.map(point => ({
                    ...point,
                    x: new Date(point.x).getTime() - firstDates[i]
                }));
            });
        }

    runRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: {
                line: {
                    tension: 0
                }
            },
            plugins: {
                legend: { display: true },
                tooltip: { 
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: function(context) {
                            const dataPoint = context[0].raw;
                            if (isAligned) {
                                const baseDate = athleteFirstDates ? athleteFirstDates[context[0].datasetIndex] : null;
                                if (baseDate) {
                                    const date = new Date(Number(dataPoint.x) + baseDate);
                                    return [
                                        dataPoint.race_name,
                                        date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                                    ];
                                }
                                return [dataPoint.race_name, ''];
                            }
                            const date = new Date(dataPoint.x);
                            return [
                                dataPoint.race_name,
                                date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                            ];
                        },
                        label: function(context) {
                            return context.dataset.label + ": " + context.parsed.y;
                        }
                    }
                },
            },
            scales: {
                x: isAligned ? {
                    type: 'linear',
                    ticks: {
                        callback: function(value) {
                            const yearIndex = Math.floor(Number(value) / msPerYear) + 1;
                            return `Season ${yearIndex}`;
                        }
                    },
                    title: { display: true, text: 'Year' }
                } : {
                    type: 'time',
                    time: { unit: 'year', tooltipFormat: 'dd-MM-yyyy' },
                    grid: { display: false },
                    ticks: { display: false },
                    title: { display: false }
                },
                y: {
                    beginAtZero: false,
                    title: { display: true, text: 'Rating' }
                }
            }
        }
    });
}

function initTransitionChart() {
    const ctx = document.getElementById('transition-ratings-canvas');
    if (!ctx) return;

    const data = getJSON('transition-ratings-data');

    // Align data if needed
        if (isAligned) {
            const firstDates = data.datasets.map(dataset => {
                const dates = dataset.data.map(d => new Date(d.x).getTime());
                return Math.min(...dates);
            });

            data.datasets.forEach((dataset, i) => {
                dataset.data = dataset.data.map(point => ({
                    ...point,
                    x: new Date(point.x).getTime() - firstDates[i]
                }));
            });
        }

    if (transitionRatingsChart) {
        transitionRatingsChart.destroy();
    }

    transitionRatingsChart = new Chart(ctx, {
        type: 'line',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            elements: {
                line: {
                    tension: 0
                }
            },
            plugins: {
                legend: { display: true },
                tooltip: { 
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: function(context) {
                            const dataPoint = context[0].raw;
                            if (isAligned) {
                                const baseDate = athleteFirstDates ? athleteFirstDates[context[0].datasetIndex] : null;
                                if (baseDate) {
                                    const date = new Date(Number(dataPoint.x) + baseDate);
                                    return [
                                        dataPoint.race_name,
                                        date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                                    ];
                                }
                                return [dataPoint.race_name, ''];
                            }
                            const date = new Date(dataPoint.x);
                            return [
                                dataPoint.race_name,
                                date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                            ];
                        },
                        label: function(context) {
                            return context.dataset.label + ": " + context.parsed.y;
                        }
                    }
                },
            },
            scales: {
                x: isAligned ? {
                    type: 'linear',
                    ticks: {
                        callback: function(value) {
                            const yearIndex = Math.floor(Number(value) / msPerYear) + 1;
                            return `Season ${yearIndex}`;
                        }
                    },
                    title: { display: true, text: 'Year' }
                } : {
                    type: 'time',
                    time: { unit: 'year', tooltipFormat: 'dd-MM-yyyy' },
                    grid: { display: false },
                    ticks: { display: false },
                    title: { display: false }
                },
                y: {
                    beginAtZero: false,
                    title: { display: true, text: 'Rating' }
                }
            }
        }
    });
}
