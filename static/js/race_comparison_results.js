(function () {
    'use strict';

    let currentRatingDisc = 'overall';
    let currentTimeDisc   = 'overall';
    let ratingChart = null;
    let timeChart   = null;

    // Shared dark tooltip element; one per page.
    function _ensureTooltipEl() {
        let el = document.getElementById('rc-dist-tooltip');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'rc-dist-tooltip';
        Object.assign(el.style, {
            position: 'fixed', pointerEvents: 'none', opacity: '0',
            background: '#1a1a2e', color: '#fff', borderRadius: '10px',
            fontFamily: "'Plus Jakarta Sans', sans-serif",
            border: '1px solid rgba(255,255,255,0.1)',
            boxShadow: '0 6px 24px rgba(0,0,0,0.4)',
            transition: 'opacity 0.12s', minWidth: '180px', zIndex: '200',
            padding: '10px 12px', fontSize: '12px',
        });
        document.body.appendChild(el);
        return el;
    }

    function _positionTooltip(el, chart, tooltip) {
        const rect = chart.canvas.getBoundingClientRect();
        const tipW = el.offsetWidth || 200;
        const tipH = el.offsetHeight || 90;
        const cx = rect.left + tooltip.caretX;
        const cy = rect.top  + tooltip.caretY;
        let left = cx + 16;
        if (left + tipW + 8 > window.innerWidth) left = cx - tipW - 16;
        let top = cy - tipH / 2;
        if (top < 8) top = 8;
        if (top + tipH > window.innerHeight - 8) top = window.innerHeight - tipH - 8;
        el.style.left = left + 'px';
        el.style.top  = top  + 'px';
        el.style.opacity = '1';
    }

    // Chart.js plugin: paints alternating vertical zebra stripes across the
    // chart area, one stripe per pair of x-axis ticks.
    const zebraStripesPlugin = {
        id: 'zebraStripes',
        beforeDatasetsDraw(chart) {
            const { ctx, chartArea, scales: { x } } = chart;
            if (!x || !chartArea) return;
            const ticks = x.ticks;
            if (!ticks || ticks.length < 2) return;
            ctx.save();
            for (let i = 0; i < ticks.length - 1; i += 2) {
                const x0 = x.getPixelForValue(ticks[i].value);
                const x1 = x.getPixelForValue(ticks[i + 1].value);
                ctx.fillStyle = 'rgba(0, 0, 0, 0.025)';
                ctx.fillRect(x0, chartArea.top, x1 - x0, chartArea.bottom - chartArea.top);
            }
            ctx.restore();
        },
    };

    // Pick a "nice" step size so axis ticks land on round multiples of a
    // human-friendly unit (100 ratings, full minutes, etc.) — chart.js
    // otherwise lands on cubic-spaced ticks like 1907 / 2007 / 2107.
    function _niceStep(range, fmt) {
        if (range <= 0) return undefined;
        const target = 6;                              // aim for ~6 ticks across
        const raw = range / target;
        const candidates = fmt === 'time'
            ? [30, 60, 120, 300, 600, 900, 1800, 3600]   // 30 s … 1 h
            : [25, 50, 100, 200, 250, 500, 1000];
        for (const c of candidates) if (c >= raw) return c;
        return candidates[candidates.length - 1];
    }

    function _buildHistogramChart(canvasId, data, fmt) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return null;
        if (!data || !data.datasets) return null;

        const tipEl = _ensureTooltipEl();

        const externalTooltip = ({ chart, tooltip }) => {
            if (tooltip.opacity === 0) { tipEl.style.opacity = '0'; return; }
            const pts = tooltip.dataPoints || [];
            if (!pts.length) return;
            const label = pts[0].raw.label || '';
            const rows = pts.map(dp => {
                const ds = chart.data.datasets[dp.datasetIndex];
                const color = ds.borderColor;
                return `
                    <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
                        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0"></span>
                        <span style="flex:1;color:rgba(255,255,255,0.7);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${ds.label || ''}</span>
                        <span style="color:${color};font-weight:700;font-variant-numeric:tabular-nums">${dp.raw.y}</span>
                    </div>`;
            }).join('');
            tipEl.innerHTML = `
                <div style="color:rgba(255,255,255,0.55);font-size:10px;text-transform:uppercase;letter-spacing:0.06em;font-weight:700">${fmt === 'time' ? 'Time bin' : 'Rating bin'}</div>
                <div style="font-weight:700;font-size:13px;margin-top:2px">${label}</div>
                ${rows}`;
            _positionTooltip(tipEl, chart, tooltip);
        };

        return new Chart(ctx, {
            type: 'line',
            data,
            plugins: [zebraStripesPlugin],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                parsing: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: true, position: 'top', align: 'end',
                              labels: { boxWidth: 12, boxHeight: 12, font: { size: 11 } } },
                    tooltip: {
                        enabled: false,
                        external: externalTooltip,
                    },
                },
                scales: {
                    x: (() => {
                        const labels = Array.isArray(data.labels) ? data.labels : [];
                        const xmin   = labels.length ? labels[0] : undefined;
                        const xmax   = labels.length ? labels[labels.length - 1] : undefined;
                        const step   = (xmin !== undefined && xmax !== undefined)
                                       ? _niceStep(xmax - xmin, fmt)
                                       : undefined;
                        // Snap the visible range to multiples of `step` so the
                        // first/last ticks themselves land on round numbers.
                        const lo = step ? Math.floor(xmin / step) * step : xmin;
                        const hi = step ? Math.ceil (xmax / step) * step : xmax;
                        return {
                            type: 'linear',
                            offset: false,
                            bounds: 'data',
                            min: lo,
                            max: hi,
                            title: { display: true, text: fmt === 'time' ? 'Time' : 'Rating' },
                            grid:  { display: false },
                            ticks: {
                                stepSize: step,
                                callback: v => fmt === 'time'
                                    ? (isNaN(+v) ? v : formatTime(+v))
                                    : Math.round(+v),
                            },
                        };
                    })(),
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Number of Athletes' },
                        grid:  { display: false },
                    },
                },
            },
        });
    }

    function _initRatings() {
        const data = getJSON(`rc-${currentRatingDisc}-rating-data`);
        if (ratingChart) ratingChart.destroy();
        ratingChart = _buildHistogramChart('rc-rating-main-canvas', data, 'rating');
    }

    function _initTimes() {
        const data = getJSON(`rc-${currentTimeDisc}-time-data`);
        if (timeChart) timeChart.destroy();
        timeChart = _buildHistogramChart('rc-time-main-canvas', data, 'time');
    }

    function _setSectionTitle(id, text) {
        const el = document.getElementById(id);
        if (!el) return;
        const span = el.querySelector('span');
        (span || el).textContent = text;
    }

    function switchRcRatingDisc(disc) {
        currentRatingDisc = disc;
        _setSectionTitle('rc-rating-section-title',
            `Rating distribution - ${disc.charAt(0).toUpperCase() + disc.slice(1)}`);
        _initRatings();
    }

    function switchRcTimeDisc(disc) {
        currentTimeDisc = disc;
        _setSectionTitle('rc-time-section-title',
            `Time distribution - ${disc.charAt(0).toUpperCase() + disc.slice(1)}`);
        _initTimes();
    }

    document.querySelectorAll('#rc-rating-disc-chips input[name="rc-rating-disc"]').forEach(r => {
        r.addEventListener('change', e => switchRcRatingDisc(e.target.value));
    });
    document.querySelectorAll('#rc-time-disc-chips input[name="rc-time-disc"]').forEach(r => {
        r.addEventListener('change', e => switchRcTimeDisc(e.target.value));
    });

    // Common athletes table — radio toggle to swap which split is visible.
    // All four discipline cells are pre-rendered (with their own winner
    // highlight + gap text); we just flip which is active and hide the
    // Pos columns when the user isn't looking at overall.
    const RC_CAPTION_LABELS = {
        overall: 'Athletes with the faster overall split',
        swim:    'Athletes with the faster swim split',
        bike:    'Athletes with the faster bike split',
        run:     'Athletes with the faster run split',
    };
    document.querySelectorAll('#rc-common-disc-chips input[name="rc-common-disc"]').forEach(radio => {
        radio.addEventListener('change', e => {
            const disc = e.target.value;
            const table = document.getElementById('rc-common-table');
            if (table) {
                table.dataset.disc = disc;
                table.querySelectorAll('.rc-split-side').forEach(el => {
                    el.classList.toggle('rc-split-side--active', el.dataset.disc === disc);
                });
            }
            document.querySelectorAll('#rc-wins-summary .rc-wins-val').forEach(el => {
                el.classList.toggle('rc-wins-val--active', el.dataset.disc === disc);
            });
            const caption = document.getElementById('rc-wins-caption');
            if (caption) caption.textContent = RC_CAPTION_LABELS[disc] || RC_CAPTION_LABELS.overall;
        });
    });

    _initRatings();
    _initTimes();
})();
