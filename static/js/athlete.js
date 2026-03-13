// --- Calendar year band plugin ---
// Draws alternating grey/white columns based on actual calendar years.
const calendarYearBandsPlugin = {
    id: 'calendarYearBands',
    beforeDraw(chart) {
        const xScale = chart.scales.x;
        const yScale = chart.scales.y;
        if (!xScale || !yScale) return;

        const ctx = chart.ctx;
        const top = yScale.top;
        const bottom = yScale.bottom;
        const startYear = new Date(xScale.min).getFullYear();
        const endYear   = new Date(xScale.max).getFullYear() + 1;

        ctx.save();
        for (let year = startYear; year <= endYear; year++) {
            const x1 = Math.max(xScale.getPixelForValue(new Date(year,     0, 1).getTime()), xScale.left);
            const x2 = Math.min(xScale.getPixelForValue(new Date(year + 1, 0, 1).getTime()), xScale.right);
            if (x2 <= x1) continue;

            if (year % 2 === 0) {
                ctx.fillStyle = 'rgba(0,0,0,0.04)';
                ctx.fillRect(x1, top, x2 - x1, bottom - top);
            }
        }
        ctx.restore();
    }
};

// --- Ranking charts ---
function makeRankingChart(canvasId, dataId) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    const data = getJSON(dataId);
    return new Chart(ctx, {
        type: 'line',
        data: data,
        plugins: [calendarYearBandsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            clip: false,
            elements: { line: { tension: 0 } },
            plugins: {
                legend: { display: true, position: 'bottom' },
                tooltip: {
                    mode: 'nearest',
                    intersect: true,
                    callbacks: {
                        title: function(context) {
                            const dataPoint = context[0].raw;
                            const date = new Date(dataPoint.x);
                            return [dataPoint.race_name, date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })];
                        },
                        label: context => context.dataset.label + ': #' + context.parsed.y
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'year', tooltipFormat: 'dd-MM-yyyy' },
                    grid: { display: false },
                    title: { display: false }
                },
                y: {
                    reverse: true,
                    min: 1,
                    ticks: { stepSize: 1, callback: value => '#' + value },
                    title: { display: true, text: 'Ranking' }
                }
            }
        }
    });
}

makeRankingChart('overall-world-rankings-canvas',    'overall-world-rankings-data');
makeRankingChart('swim-world-rankings-canvas',       'swim-world-rankings-data');
makeRankingChart('bike-world-rankings-canvas',       'bike-world-rankings-data');
makeRankingChart('run-world-rankings-canvas',        'run-world-rankings-data');
makeRankingChart('transition-world-rankings-canvas', 'transition-world-rankings-data');
makeRankingChart('overall-national-rankings-canvas',    'overall-national-rankings-data');
makeRankingChart('swim-national-rankings-canvas',       'swim-national-rankings-data');
makeRankingChart('bike-national-rankings-canvas',       'bike-national-rankings-data');
makeRankingChart('run-national-rankings-canvas',        'run-national-rankings-data');
makeRankingChart('transition-national-rankings-canvas', 'transition-national-rankings-data');

function toggleNotableResults(button, targetId) {
    const dropdown = document.getElementById(targetId);
    if (!dropdown) return;

    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', !expanded);
    dropdown.hidden = expanded;
    button.classList.toggle('open', !expanded);
}

// Add click handlers to sortable headers
document.addEventListener('DOMContentLoaded', () => {
    const tables = document.querySelectorAll('table.sortable-table');
    if (!tables.length) return;

    tables.forEach(table => {
        const headers = table.querySelectorAll('th.sortable');

        headers.forEach((header, index) => {
            header.addEventListener('click', () => {
                // Toggle sort direction
                const isAsc = header.classList.contains('asc');
                
                // Remove all sorting classes
                headers.forEach(h => h.classList.remove('asc', 'desc'));
                
                // Add appropriate class
                if (isAsc) {
                    header.classList.add('desc');
                    sortTable(table, index, false);
                } else {
                    header.classList.add('asc');
                    sortTable(table, index, true);
                }
            });
        });
    });
});

// Ratings chart ---------------------------------------------------------------
const ratingsCtx = document.getElementById('ratings-chart-canvas');
const ratingsChartData = getJSON('ratings-chart-data');

new Chart(ratingsCtx, {
    type: 'line',
    data: ratingsChartData,
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                display: true,
                position: 'bottom'
            },
            tooltip: {
                mode: 'nearest',
                intersect: true,
                callbacks: {
                    title: function(context) {
                        const dataPoint = context[0].raw;
                        const date = new Date(dataPoint.x);
                        // Format race date
                        return [
                            dataPoint.race_name,
                            date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        ];
                    },
                    label: function(context) {
                        return context.dataset.label + ': ' + context.parsed.y;
                    }
                }
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    unit: 'year',
                    tooltipFormat: 'dd-MM-yyyy'
                },
                grid: { display: false },
                ticks: { display: false },
                title: { display: false }
            },
            y: {
                beginAtZero: false,
                title: {
                    display: true,
                    text: 'Rating'
                }
            }
        }
    }
});

// Overall % behind leader chart -----------------------------------------------
const overallPctBehindCtx = document.getElementById('overall-pct-behind-chart-canvas');
const overallPctBehindData = getJSON('overall-pct-behind-chart-data');

new Chart(overallPctBehindCtx, {
    type: 'line',
    data: overallPctBehindData,
    options: {
        spanGaps: true,
        responsive: true,
        maintainAspectRatio: false,
        clip: false,
        elements: {
            line: {
                tension: 0
            }
        },
        plugins: {
            legend: {
                display: false
            },
            tooltip: {
                mode: 'nearest',
                intersect: true,
                callbacks: {
                    title: function(context) {
                        const dataPoint = context[0].raw;
                        const date = new Date(dataPoint.x);
                        return [
                            dataPoint.race_name,
                            date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        ];
                    },
                    label: function(context) {
                        return ' ' + context.parsed.y + '%';
                    }
                }
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    unit: 'year',
                    tooltipFormat: 'dd-MM-yyyy'
                },
                grid: { display: false },
                ticks: { display: false },
                title: { display: false }
            },
            y: {
                suggestedMin: -0.3,
                title: {
                    display: true,
                    text: '% Behind Leader'
                }
            }
        }
    }
});

// Swim % behind leader chart -----------------------------------------------
const swimPctBehindCtx = document.getElementById('swim-pct-behind-chart-canvas');
const swimPctBehindData = getJSON('swim-pct-behind-chart-data');

new Chart(swimPctBehindCtx, {
    type: 'line',
    data: swimPctBehindData,
    options: {
        spanGaps: true,
        responsive: true,
        maintainAspectRatio: false,
        clip: false,
        elements: {
            line: {
                tension: 0
            }
        },
        plugins: {
            legend: {
                display: false
            },
            tooltip: {
                mode: 'nearest',
                intersect: true,
                callbacks: {
                    title: function(context) {
                        const dataPoint = context[0].raw;
                        const date = new Date(dataPoint.x);
                        return [
                            dataPoint.race_name,
                            date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        ];
                    },
                    label: function(context) {
                        return ' ' + context.parsed.y + '%';
                    }
                }
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    unit: 'year',
                    tooltipFormat: 'dd-MM-yyyy'
                },
                grid: { display: false },
                ticks: { display: false },
                title: { display: false }
            },
            y: {
                suggestedMin: -0.3,
                title: {
                    display: true,
                    text: '% Behind Leader'
                }
            }
        }
    }
});

// Bike % behind leader chart --------------------------------------------------
const bikePctBehindCtx = document.getElementById('bike-pct-behind-chart-canvas');
const bikePctBehindData = getJSON('bike-pct-behind-chart-data');

new Chart(bikePctBehindCtx, {
    type: 'line',
    data: bikePctBehindData,
    options: {
        spanGaps: true,
        responsive: true,
        maintainAspectRatio: false,
        clip: false,
        elements: {
            line: {
                tension: 0
            }
        },
        plugins: {
            legend: {
                display: false
            },
            tooltip: {
                mode: 'nearest',
                intersect: true,
                callbacks: {
                    title: function(context) {
                        const dataPoint = context[0].raw;
                        const date = new Date(dataPoint.x);
                        return [
                            dataPoint.race_name,
                            date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        ];
                    },
                    label: function(context) {
                        return ' ' + context.parsed.y + '%';
                    }
                }
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    unit: 'year',
                    tooltipFormat: 'dd-MM-yyyy'
                },
                grid: { display: false },
                ticks: { display: false },
                title: { display: false }
            },
            y: {
                suggestedMin: -0.3,
                title: {
                    display: true,
                    text: '% Behind Leader'
                }
            }
        }
    }
});

// Run % behind leader chart ---------------------------------------------------
const runPctBehindCtx = document.getElementById('run-pct-behind-chart-canvas');
const runPctBehindData = getJSON('run-pct-behind-chart-data');

new Chart(runPctBehindCtx, {
    type: 'line',
    data: runPctBehindData,
    options: {
        spanGaps: true,
        responsive: true,
        maintainAspectRatio: false,
        clip: false,
        elements: {
            line: {
                tension: 0
            }
        },
        plugins: {
            legend: {
                display: false
            },
            tooltip: {
                mode: 'nearest',
                intersect: true,
                callbacks: {
                    title: function(context) {
                        const dataPoint = context[0].raw;
                        const date = new Date(dataPoint.x);
                        return [
                            dataPoint.race_name,
                            date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        ];
                    },
                    label: function(context) {
                        return ' ' + context.parsed.y + '%';
                    }
                }
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    unit: 'year',
                    tooltipFormat: 'dd-MM-yyyy'
                },
                grid: { display: false },
                ticks: { display: false },
                title: { display: false }
            },
            y: {
                suggestedMin: -0.3,
                title: {
                    display: true,
                    text: '% Behind Leader'
                }
            }
        }
    }
});

// Swim splits chart -----------------------------------------------------------
const swimTimesCtx = document.getElementById('swim-times-chart-canvas');
const swimTimesData = getJSON('swim-times-chart-data');

new Chart(swimTimesCtx, {
    type: 'line',
    data: swimTimesData,
    options: {
        responsive: true,
        maintainAspectRatio: false,
        elements: {
            line: {
                tension: 0
            }
        },
        plugins: {
            legend: {
                display: true,
                position: 'bottom'
            },
            tooltip: {
                mode: 'nearest',
                intersect: true,
                callbacks: {
                    title: function(context) {
                        const dataPoint = context[0].raw;
                        const date = new Date(dataPoint.x);
                        return [
                            dataPoint.race_name,
                            date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        ];
                    },
                    label: function(context) {
                        const seconds = context.parsed.y;
                        return ' ' + formatTime(seconds);
                    }
                }
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    unit: 'year',
                    tooltipFormat: 'dd-MM-yyyy'
                },
                grid: { display: false },
                ticks: { display: false },
                title: { display: false }
            },
            y: {
                beginAtZero: false,
                title: {
                    display: true,
                    text: 'Time'
                },
                ticks: {
                    callback: function(value, index, ticks) {
                        const num = Number(value);
                        return isNaN(num) ? value : formatTime(num);
                    }
                }
            }
        }
    }
});

// Bike splits chart -----------------------------------------------------------
const bikeTimesCtx = document.getElementById('bike-times-chart-canvas');
const bikeTimesData = getJSON('bike-times-chart-data');

new Chart(bikeTimesCtx, {
    type: 'line',
    data: bikeTimesData,
    options: {
        responsive: true,
        maintainAspectRatio: false,
        elements: {
            line: {
                tension: 0 
            }
        },
        plugins: {
            legend: {
                display: true,
                position: 'bottom'
            },
            tooltip: {
                mode: 'nearest',
                intersect: true,
                callbacks: {
                    title: function(context) {
                        const dataPoint = context[0].raw;
                        const date = new Date(dataPoint.x);
                        return [
                            dataPoint.race_name,
                            date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        ];
                    },
                    label: function(context) {
                        const seconds = context.parsed.y;
                        return ' ' + formatTime(seconds);
                    }
                }
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    unit: 'year',
                    tooltipFormat: 'dd-MM-yyyy'
                },
                grid: { display: false },
                ticks: { display: false },
                title: { display: false }
            },
            y: {
                beginAtZero: false,
                title: {
                    display: true,
                    text: 'Time'
                },
                ticks: {
                    callback: function(value, index, ticks) {
                        const num = Number(value);
                        return isNaN(num) ? value : formatTime(num);
                    }
                }
            }
        }
    }
});

// Run splits chart ------------------------------------------------------------
const runTimesCtx = document.getElementById('run-times-chart-canvas');
const runTimesData = getJSON('run-times-chart-data');

new Chart(runTimesCtx, {
    type: 'line',
    data: runTimesData,
    options: {
        responsive: true,
        maintainAspectRatio: false,
        elements: {
            line: {
                tension: 0 
            }
        },
        plugins: {
            legend: {
                display: true,
                position: 'bottom'
            },
            tooltip: {
                mode: 'nearest',
                intersect: true,
                callbacks: {
                    title: function(context) {
                        const dataPoint = context[0].raw;
                        const date = new Date(dataPoint.x);
                        return [
                            dataPoint.race_name,
                            date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
                        ];
                    },
                    label: function(context) {
                        const seconds = context.parsed.y;
                        return ' ' + formatTime(seconds);
                    }
                }
            }
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    unit: 'year',
                    tooltipFormat: 'dd-MM-yyyy'
                },
                grid: { display: false },
                ticks: { display: false },
                title: { display: false }
            },
            y: {
                beginAtZero: false,
                title: {
                    display: true,
                    text: 'Time'
                },
                maxTicksLimit: 4,
                ticks: {
                    callback: function(value, index, ticks) {
                        const num = Number(value);
                        return isNaN(num) ? value : formatTime(num);
                    }
                }
            }
        }
    }
});