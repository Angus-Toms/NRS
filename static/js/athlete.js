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
                beginAtZero: true,
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
                beginAtZero: true,
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
                beginAtZero: true,
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
                beginAtZero: true,
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