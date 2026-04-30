(function () {
    'use strict';

    // yearBandsPlugin only needs to exist once per page, even if this script re-runs
    if (!window._yearBandsPlugin) {
        window._yearBandsPlugin = {
            id: 'yearBands',
            beforeDraw(chart) {
                if (!isAligned) return;
                const xScale = chart.scales.x;
                const yScale = chart.scales.y;
                if (!xScale || !yScale) return;

                const ctx = chart.ctx;
                const top = yScale.top, bottom = yScale.bottom;
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
    }
    const yearBandsPlugin = window._yearBandsPlugin;

    // --- State (reset fresh on each comparison) ---
    let isAligned = false;
    const msPerYear = 365.25 * 24 * 60 * 60 * 1000;
    let athleteFirstDates = null;
    let currentRatingsDisc = 'overall';
    let currentRankingsDisc = 'overall';
    const discs = ['overall', 'swim', 'bike', 'run', 'transition'];

    let ratingsMainChart = null;
    const ratingsMiniCharts = {};
    let rankingsMainChart = null;
    const rankingsMiniCharts = {};

    // --- Tooltip ---

    // Reuse a single tooltip element; create it only once per page
    if (!document.getElementById('comparison-chart-tooltip')) {
        const el = document.createElement('div');
        el.id = 'comparison-chart-tooltip';
        Object.assign(el.style, {
            position: 'fixed', pointerEvents: 'auto', opacity: '0',
            background: '#1a1a2e', borderRadius: '10px',
            fontFamily: "'Plus Jakarta Sans', sans-serif",
            border: '1px solid rgba(255,255,255,0.1)',
            boxShadow: '0 6px 24px rgba(0,0,0,0.4)',
            transition: 'opacity 0.12s', width: '220px', zIndex: '200',
            overflow: 'hidden',
        });
        document.body.appendChild(el);
        el.addEventListener('mouseenter', () => { window._cmpTipHovered = true; });
        el.addEventListener('mouseleave', () => {
            window._cmpTipHovered = false;
            el.style.opacity = '0';
        });
    }
    const tooltipEl = document.getElementById('comparison-chart-tooltip');

    function _positionTooltip(chart, tooltip) {
        const rect   = chart.canvas.getBoundingClientRect();
        const tipW   = tooltipEl.offsetWidth || 220;
        const caretX = rect.left + tooltip.caretX;
        const caretY = rect.top  + tooltip.caretY;
        if (caretX + tipW + 16 <= window.innerWidth) {
            tooltipEl.style.left = (caretX + 16) + 'px';
            tooltipEl.style.top  = (caretY - 50) + 'px';
        } else {
            const tipH = tooltipEl.offsetHeight || 130;
            let left = Math.max(8, caretX - tipW / 2);
            if (left + tipW > window.innerWidth - 8) left = window.innerWidth - tipW - 8;
            tooltipEl.style.left = left + 'px';
            tooltipEl.style.top  = Math.max(8, caretY - tipH - 12) + 'px';
        }
        tooltipEl.style.opacity = '1';
    }

    function _resolveDate(rawX, datasetIndex) {
        if (isAligned && athleteFirstDates) {
            return new Date(Number(rawX) + athleteFirstDates[datasetIndex]);
        }
        return new Date(rawX);
    }

    function _chevronSvg(up, size, col) {
        const pts = up ? '18 15 12 9 6 15' : '6 9 12 15 18 9';
        return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${col}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-bottom:1px"><polyline points="${pts}"></polyline></svg>`;
    }
    function fmtChangeArrow(n) {
        if (n == null) return '';
        const v = Math.round(n);
        const col = v >= 0 ? '#5eead4' : '#f87171';
        return `<span style="color:${col};font-size:11px;font-weight:600;white-space:nowrap;margin-left:4px">${_chevronSvg(v >= 0, 11, col)}${Math.abs(v)}</span>`;
    }
    function fmtRankChange(n) {
        if (n == null || n === 0) return '';
        const v = Math.round(n);
        const col = v > 0 ? '#5eead4' : '#f87171';
        return `<span style="color:${col};font-size:10px;font-weight:600;margin-left:3px;white-space:nowrap">${_chevronSvg(v > 0, 10, col)}${Math.abs(v)}</span>`;
    }

    function buildExternalTooltip(isRankings) {
        return function ({ chart, tooltip }) {
            if (tooltip.opacity === 0) {
                if (!window._cmpTipHovered) tooltipEl.style.opacity = '0';
                return;
            }

            const dp      = tooltip.dataPoints[0];
            const d       = dp.raw;
            const dataset = chart.data.datasets[dp.datasetIndex];
            const color   = dataset.borderColor;
            const name    = dataset.label || '';
            const date    = _resolveDate(d.x, dp.datasetIndex)
                                .toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

            const raceLink = d.race_id
                ? `<a href="/race/${d.race_id}" style="color:#fff;text-decoration:none;font-weight:700;font-size:13px;line-height:1.2;display:block" onmouseover="this.style.color='#E87722'" onmouseout="this.style.color='#fff'">${d.race_name}</a>`
                : `<span style="color:#fff;font-weight:700;font-size:13px">${d.race_name}</span>`;

            const changeHtml = isRankings ? fmtRankChange(d.rank_chg) : fmtChangeArrow(d.change);
            const valueLine  = isRankings
                ? `<div style="font-size:22px;font-weight:800;color:${color};line-height:1">#${d.y}${changeHtml}</div>`
                : `<div style="font-size:22px;font-weight:800;color:${color};line-height:1">${d.y}${changeHtml}</div>`;
            const valueLabel = isRankings ? 'World ranking' : 'Rating';

            tooltipEl.innerHTML = `
                <div style="padding:7px 12px;border-bottom:1px solid rgba(255,255,255,0.08)">
                    <div style="margin-bottom:2px">${raceLink}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.4)">${date}</div>
                </div>
                <div style="padding:10px 12px">
                    <div style="font-size:11px;color:${color};font-weight:600;margin-bottom:5px">${name}</div>
                    ${valueLine}
                    <div style="font-size:11px;color:rgba(255,255,255,0.4);margin-top:3px">${valueLabel}</div>
                </div>
            `;

            _positionTooltip(chart, tooltip);
        };
    }

    const ratingsTooltip  = buildExternalTooltip(false);
    const rankingsTooltip = buildExternalTooltip(true);

    // --- Shared axis config ---

    function alignedXAxis(withMin) {
        const cfg = { type: 'linear', ticks: { display: false }, grid: { display: false }, title: { display: true, text: 'Year' } };
        if (withMin) cfg.min = 0;
        return cfg;
    }

    const timeXAxis = {
        type: 'time',
        time: { unit: 'year' },
        grid: { display: false },
        ticks: { display: false },
    };

    function applyAlignmentToData(data) {
        if (!isAligned || !athleteFirstDates) return data;
        return {
            ...data,
            datasets: data.datasets.map((dataset, i) => ({
                ...dataset,
                data: dataset.data.map(point => ({
                    ...point,
                    x: new Date(point.x).getTime() - athleteFirstDates[i]
                }))
            }))
        };
    }

    // --- Chart builders ---

    function buildMainChart(canvasId, disc, dataPrefix, isRankings) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return null;

        let data = getJSON(`${disc}-${dataPrefix}-data`);

        // Compute alignment offsets from the overall ratings dataset (done once)
        if (disc === 'overall' && dataPrefix === 'ratings' && isAligned) {
            athleteFirstDates = data.datasets.map(dataset =>
                Math.min(...dataset.data.map(d => new Date(d.x).getTime()))
            );
        }

        data = applyAlignmentToData(data);

        // Style each dataset with matching hover points
        data = {
            ...data,
            datasets: data.datasets.map(ds => ({
                ...ds,
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 5,
                pointBackgroundColor: ds.borderColor,
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: ds.borderColor,
                pointHoverBorderWidth: 2,
                fill: false,
                clip: false,
            }))
        };

        const yAxis = isRankings
            ? { reverse: true, min: 1, beginAtZero: false,
                grid: { color: 'rgba(0,0,0,0.05)' },
                ticks: { color: '#999', stepSize: 1, callback: v => '#' + v },
                title: { display: true, text: 'Ranking' } }
            : { beginAtZero: false,
                grid: { color: 'rgba(0,0,0,0.05)' },
                ticks: { color: '#999' },
                title: { display: true, text: 'Rating' } };

        return new Chart(ctx, {
            type: 'line',
            data,
            plugins: [yearBandsPlugin],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                clip: false,
                interaction: { mode: 'nearest', intersect: false },
                plugins: {
                    legend: { display: true },
                    tooltip: {
                        enabled: false,
                        external: isRankings ? rankingsTooltip : ratingsTooltip,
                    },
                },
                scales: {
                    x: isAligned ? alignedXAxis(disc === 'overall') : timeXAxis,
                    y: yAxis,
                }
            }
        });
    }

    function buildMiniChart(canvasId, disc, dataPrefix, isRankings) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return null;

        let data = applyAlignmentToData(getJSON(`${disc}-${dataPrefix}-data`));

        data = {
            ...data,
            datasets: data.datasets.map(ds => ({
                ...ds,
                tension: 0.3,
                borderWidth: 1.5,
                pointRadius: 0,
                fill: false,
                clip: false,
            }))
        };

        return new Chart(ctx, {
            type: 'line',
            data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { enabled: false },
                    yearBands: false,
                },
                scales: {
                    x: {
                        display: false,
                        type: isAligned ? 'linear' : 'time',
                        grid: { display: false },
                        border: { display: false },
                    },
                    y: {
                        display: false,
                        reverse: isRankings,
                        grid: { display: false },
                        border: { display: false },
                    },
                }
            }
        });
    }

    // --- Ratings ---

    function initRatings() {
        if (ratingsMainChart) ratingsMainChart.destroy();
        ratingsMainChart = buildMainChart('ratings-main-canvas', currentRatingsDisc, 'ratings', false);
        discs.forEach(disc => {
            if (ratingsMiniCharts[disc]) ratingsMiniCharts[disc].destroy();
            ratingsMiniCharts[disc] = buildMiniChart(`ratings-mini-${disc}`, disc, 'ratings', false);
        });
    }

    function _setSectionTitle(id, text) {
        const el = document.getElementById(id);
        if (!el) return;
        const span = el.querySelector('span');
        (span || el).textContent = text;
    }

    function switchRatingsDisc(disc) {
        currentRatingsDisc = disc;
        document.querySelectorAll('#ratings-section .ratings-mini').forEach(el =>
            el.classList.toggle('active', el.dataset.disc === disc)
        );
        _setSectionTitle('ratings-section-title',
            `Rating History - ${disc.charAt(0).toUpperCase() + disc.slice(1)}`);
        if (ratingsMainChart) ratingsMainChart.destroy();
        ratingsMainChart = buildMainChart('ratings-main-canvas', disc, 'ratings', false);
    }

    // --- Rankings ---

    function initRankings() {
        if (rankingsMainChart) rankingsMainChart.destroy();
        rankingsMainChart = buildMainChart('rankings-main-canvas', currentRankingsDisc, 'rankings', true);
        discs.forEach(disc => {
            if (rankingsMiniCharts[disc]) rankingsMiniCharts[disc].destroy();
            rankingsMiniCharts[disc] = buildMiniChart(`rankings-mini-${disc}`, disc, 'rankings', true);
        });
    }

    function switchRankingsDisc(disc) {
        currentRankingsDisc = disc;
        document.querySelectorAll('#rankings-section .ratings-mini').forEach(el =>
            el.classList.toggle('active', el.dataset.disc === disc)
        );
        _setSectionTitle('rankings-section-title',
            `World Rankings - ${disc.charAt(0).toUpperCase() + disc.slice(1)}`);
        if (rankingsMainChart) rankingsMainChart.destroy();
        rankingsMainChart = buildMainChart('rankings-main-canvas', disc, 'rankings', true);
    }

    // --- Alignment ---

    function setAlignMode(aligned) {
        if (isAligned === aligned) return;
        isAligned = aligned;
        initRatings();   // sets athleteFirstDates
        initRankings();
    }

    function wireAlignChips() {
        const chips = document.getElementById('align-chips');
        if (!chips) return;
        chips.querySelectorAll('input[name="align-mode"]').forEach(r => {
            r.addEventListener('change', () => {
                if (r.checked) setAlignMode(r.value === 'career');
            });
        });
    }

    // --- H2H discipline picker (overall / swim / bike / run) ---

    function switchH2hDisc(disc) {
        const table = document.querySelector('.h2h-table');
        if (table) {
            table.dataset.disc = disc;
            table.querySelectorAll('.h2h-disc-val').forEach(el => {
                el.hidden = !el.classList.contains(`h2h-disc-${disc}`);
            });
        }
        const a1El = document.querySelector('.h2h-wins-a1');
        const a2El = document.querySelector('.h2h-wins-a2');
        if (a1El && a2El) {
            const n1 = parseInt(a1El.dataset[disc] || '0', 10);
            const n2 = parseInt(a2El.dataset[disc] || '0', 10);
            a1El.textContent = n1;
            a2El.textContent = n2;
            a1El.classList.toggle('h2h-wins-leader', n1 > n2);
            a2El.classList.toggle('h2h-wins-leader', n2 > n1);
        }
    }

    function wireH2hDiscChips() {
        const chips = document.getElementById('h2h-disc-chips');
        if (!chips) return;
        chips.querySelectorAll('input[name="h2h-disc"]').forEach(r => {
            r.addEventListener('change', () => {
                if (r.checked) switchH2hDisc(r.value);
            });
        });
    }

    // Export to global scope for onclick handlers and comparison.js
    window.initRatings      = initRatings;
    window.initRankings     = initRankings;
    window.switchRatingsDisc  = switchRatingsDisc;
    window.switchRankingsDisc = switchRankingsDisc;

    // Init
    initRatings();
    initRankings();
    wireAlignChips();
    wireH2hDiscChips();

})();
