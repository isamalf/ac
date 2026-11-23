(function () {
    const state = {
        view: 'list',
        strategyId: 'klassiki',
        timeframe: '6M',
        chartMode: 'return',
        autoPoll: true,
        pollTimer: null,
        chart: null,
        detailData: null,
    };

    function getCsrf() {
        return document.body.getAttribute('data-csrf') || '';
    }

    function fmtUsd(v) {
        const n = Number(v || 0);
        const sign = n < 0 ? '-' : '';
        return sign + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function fmtSignedUsd(v) {
        const n = Number(v || 0);
        return (n > 0 ? '+' : n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function fmtPct(v, signed = true) {
        const n = Number(v || 0);
        const prefix = signed && n > 0 ? '+' : '';
        return prefix + n.toFixed(2) + '%';
    }

    function signClass(v) {
        const n = Number(v || 0);
        return n > 0 ? 'pos' : n < 0 ? 'neg' : '';
    }

    async function apiGet(url) {
        const res = await fetch(url, { credentials: 'same-origin' });
        if (res.status === 401) {
            window.location.href = '/login';
            throw new Error('Unauthorized');
        }
        if (!res.ok) throw new Error('Request failed: ' + res.status);
        return res.json();
    }

    function updateMarketHeader(ms, spy) {
        if (ms) {
            const dot = document.getElementById('marketDot');
            dot.className = 'status-dot ' + (ms.is_open ? 'open' : (ms.session === 'PRE_MARKET' || ms.session === 'AFTER_HOURS' ? 'ext' : 'closed'));
            document.getElementById('marketLabel').textContent = ms.label || 'Market Closed';
            document.getElementById('marketTime').textContent = ms.timestamp_et || '';
        }
        if (spy) {
            document.getElementById('spyPrice').textContent = '$' + Number(spy.price || 0).toFixed(2);
            const chgEl = document.getElementById('spyChg');
            chgEl.textContent = fmtPct(spy.change_pct, true);
            chgEl.className = 'spy-chg ' + signClass(spy.change_pct);
        }
    }

    async function loadStrategiesList() {
        const data = await apiGet('/api/strategies');
        updateMarketHeader(data.market_status, data.spy_quote);
        document.getElementById('listUpdatedAt').textContent = 'Updated ' + (data.updated_at || '');

        const tbody = document.getElementById('strategiesTableBody');
        tbody.innerHTML = '';
        for (const s of data.strategies || []) {
            const tr = document.createElement('tr');
            tr.innerHTML =
                '<td><strong>' + s.name + '</strong><div class="panel-sub">' + s.asset_class + '</div></td>' +
                '<td><span class="tag-live">' + s.status + '</span></td>' +
                '<td class="num">' + fmtUsd(s.equity) + '</td>' +
                '<td class="num ' + signClass(s.daily_pnl_pct) + '">' + fmtPct(s.daily_pnl_pct) + '</td>' +
                '<td class="num ' + signClass(s.total_return_pct) + '">' + fmtPct(s.total_return_pct) + '</td>' +
                '<td class="num">' + Number(s.sharpe_ratio).toFixed(2) + '</td>' +
                '<td class="num neg">' + fmtPct(s.max_drawdown_pct, false) + '</td>' +
                '<td class="num">' + Number(s.beta).toFixed(2) + '</td>' +
                '<td class="num">' + s.open_positions + '</td>' +
                '<td class="num"><button type="button" class="btn-open">Inspect</button></td>';
            tr.addEventListener('click', function () {
                openStrategyDetail(s.id);
            });
            tbody.appendChild(tr);
        }
    }

    function renderKv(elId, rows) {
        const el = document.getElementById(elId);
        el.innerHTML = rows.map(function (r) {
            const cls = r.cls ? ' class="' + r.cls + '"' : '';
            return '<div class="kv-row"><dt>' + r.k + '</dt><dd' + cls + '>' + r.v + '</dd></div>';
        }).join('');
    }

    function renderChart(data) {
        const series = data.metrics.series;
        const ctx = document.getElementById('mainChart').getContext('2d');
        if (state.chart) state.chart.destroy();

        let datasets = [];
        let yFmt = function (v) { return v.toFixed(1) + '%'; };
        const titleEl = document.getElementById('chartTitle');
        const subEl = document.getElementById('chartSub');

        if (state.chartMode === 'return') {
            titleEl.textContent = 'Cumulative Return vs. SPY Benchmark';
            subEl.textContent = 'Rebased percentage performance · Live SPY via yfinance';
            datasets = [
                {
                    label: 'Klassiki (%)',
                    data: series.klassiki_return_pct,
                    borderColor: '#ffffff',
                    backgroundColor: 'rgba(145, 5, 32, 0.18)',
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: true,
                    tension: 0.15,
                },
                {
                    label: 'SPY Benchmark (%)',
                    data: series.spy_return_pct,
                    borderColor: '#7b8496',
                    borderDash: [5, 4],
                    borderWidth: 1.6,
                    pointRadius: 0,
                    fill: false,
                    tension: 0.15,
                },
            ];
        } else if (state.chartMode === 'equity') {
            titleEl.textContent = 'Strategy Equity Curve (USD)';
            subEl.textContent = 'Alpaca Paper Trading account net liquidation value';
            yFmt = function (v) { return '$' + Math.round(v).toLocaleString('en-US'); };
            datasets = [
                {
                    label: 'Klassiki Equity ($)',
                    data: series.klassiki_equity,
                    borderColor: '#ffffff',
                    backgroundColor: 'rgba(145, 5, 32, 0.2)',
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: true,
                    tension: 0.15,
                },
            ];
        } else if (state.chartMode === 'drawdown') {
            titleEl.textContent = 'Underwater Drawdown vs. SPY';
            subEl.textContent = 'Peak-to-trough percentage drawdown';
            datasets = [
                {
                    label: 'Klassiki DD (%)',
                    data: series.klassiki_drawdown_pct,
                    borderColor: '#ef4444',
                    backgroundColor: 'rgba(239, 68, 68, 0.16)',
                    borderWidth: 1.8,
                    pointRadius: 0,
                    fill: true,
                    tension: 0.1,
                },
                {
                    label: 'SPY DD (%)',
                    data: series.spy_drawdown_pct,
                    borderColor: '#6b7280',
                    borderDash: [4, 4],
                    borderWidth: 1.4,
                    pointRadius: 0,
                    fill: false,
                    tension: 0.1,
                },
            ];
        } else {
            titleEl.textContent = 'Rolling 20-Day Annualized Sharpe Ratio';
            subEl.textContent = 'Short-horizon risk-adjusted momentum consistency';
            yFmt = function (v) { return v.toFixed(2); };
            datasets = [
                {
                    label: 'Rolling 20D Sharpe',
                    data: series.rolling_20d_sharpe,
                    borderColor: '#e11d48',
                    backgroundColor: 'rgba(225, 29, 72, 0.12)',
                    borderWidth: 1.8,
                    pointRadius: 0,
                    fill: true,
                    tension: 0.2,
                },
            ];
        }

        state.chart = new Chart(ctx, {
            type: 'line',
            data: { labels: series.dates, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        labels: { color: '#c4c9d4', boxWidth: 14, font: { family: 'Inter', size: 12 } },
                    },
                    tooltip: {
                        backgroundColor: '#111217',
                        borderColor: '#272a34',
                        borderWidth: 1,
                        titleColor: '#ffffff',
                        bodyColor: '#d1d5db',
                    },
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: { color: '#7a808f', maxTicksLimit: 8, font: { size: 11 } },
                    },
                    y: {
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { color: '#7a808f', font: { size: 11 }, callback: yFmt },
                    },
                },
            },
        });
    }

    function renderDetail(data) {
        state.detailData = data;
        updateMarketHeader(data.market_status, data.spy_quote);

        const notice = document.getElementById('modeNotice');
        if (data.mode !== 'LIVE_ALPACA_PAPER') {
            notice.textContent = data.live_feed_notice || 'Running in paper-simulation demo mode until ALPACA_API_KEY and ALPACA_SECRET_KEY are configured in server .env.';
            notice.hidden = false;
        } else {
            notice.hidden = true;
        }

        document.getElementById('detailName').textContent = data.strategy.name;
        document.getElementById('detailStatus').textContent = data.strategy.status;
        document.getElementById('detailVenue').textContent = data.strategy.venue + ' · Updated ' + data.updated_at;

        const m = data.metrics;
        const p = m.performance;
        const ra = m.risk_adjusted;
        const bm = m.benchmark_spy;
        const rt = m.risk_tail;
        const pf = m.portfolio;
        const ex = m.execution;

        document.getElementById('heroKpis').innerHTML = [
            { l: 'Net Equity', v: fmtUsd(pf.equity), s: 'Cash ' + fmtPct(pf.cash_ratio_pct, false) },
            { l: 'Daily P&L', v: fmtSignedUsd(p.daily_pnl), s: fmtPct(p.daily_pnl_pct), cls: signClass(p.daily_pnl) },
            { l: 'Total Return', v: fmtPct(p.total_return_pct), s: 'vs SPY ' + fmtPct(p.spy_total_return_pct), cls: signClass(p.total_return_pct) },
            { l: 'Sharpe Ratio', v: ra.sharpe_ratio.toFixed(2), s: 'Sortino ' + ra.sortino_ratio.toFixed(2) },
            { l: 'Max Drawdown', v: fmtPct(rt.max_drawdown_pct, false), s: 'Current ' + fmtPct(rt.current_drawdown_pct, false), cls: 'neg' },
            { l: 'Beta (SPY)', v: bm.beta.toFixed(2), s: 'Alpha ' + fmtPct(bm.jensen_alpha_pct) },
        ].map(function (k) {
            return '<div class="kpi-box"><div class="kpi-label">' + k.l + '</div><div class="kpi-val ' + (k.cls || '') + '">' + k.v + '</div><div class="kpi-sub">' + k.s + '</div></div>';
        }).join('');

        renderKv('kvPerformance', [
            { k: 'Total Return', v: fmtPct(p.total_return_pct), cls: signClass(p.total_return_pct) },
            { k: 'SPY Benchmark Return', v: fmtPct(p.spy_total_return_pct), cls: signClass(p.spy_total_return_pct) },
            { k: 'Excess Return vs. SPY', v: fmtPct(p.excess_return_pct), cls: signClass(p.excess_return_pct) },
            { k: 'Annualized Return (CAGR)', v: fmtPct(p.cagr_pct), cls: signClass(p.cagr_pct) },
            { k: 'SPY CAGR', v: fmtPct(p.spy_cagr_pct) },
            { k: 'Cumulative P&L', v: fmtSignedUsd(p.cumulative_pnl), cls: signClass(p.cumulative_pnl) },
            { k: 'Best Trading Day', v: fmtPct(p.best_day_pct), cls: 'pos' },
            { k: 'Worst Trading Day', v: fmtPct(p.worst_day_pct), cls: 'neg' },
            { k: 'Positive Days', v: p.positive_days_pct.toFixed(1) + '%' },
        ]);

        renderKv('kvRiskAdjusted', [
            { k: 'Sharpe Ratio (Ann.)', v: ra.sharpe_ratio.toFixed(2) },
            { k: 'Sortino Ratio (Ann.)', v: ra.sortino_ratio.toFixed(2) },
            { k: 'Calmar Ratio', v: ra.calmar_ratio.toFixed(2) },
            { k: 'Treynor Ratio', v: ra.treynor_ratio.toFixed(3) },
            { k: 'Information Ratio', v: ra.information_ratio.toFixed(2) },
            { k: 'Omega Ratio', v: ra.omega_ratio.toFixed(2) },
            { k: 'Sterling Ratio', v: ra.sterling_ratio.toFixed(2) },
            { k: 'Gain-to-Pain Ratio', v: ra.gain_to_pain.toFixed(2) },
            { k: 'Risk-Free Rate (T-Bill)', v: ra.risk_free_rate_pct.toFixed(2) + '%' },
        ]);

        renderKv('kvBenchmark', [
            { k: 'CAPM Beta (SPY)', v: bm.beta.toFixed(2) },
            { k: "Jensen's Alpha (Ann.)", v: fmtPct(bm.jensen_alpha_pct), cls: signClass(bm.jensen_alpha_pct) },
            { k: 'Correlation (r)', v: bm.correlation.toFixed(2) },
            { k: 'R-Squared (R²)', v: bm.r_squared.toFixed(2) },
            { k: 'Tracking Error (Ann.)', v: bm.tracking_error_pct.toFixed(2) + '%' },
            { k: 'Upside Capture Ratio', v: bm.up_capture_pct.toFixed(1) + '%' },
            { k: 'Downside Capture Ratio', v: bm.down_capture_pct.toFixed(1) + '%' },
        ]);

        renderKv('kvRiskTail', [
            { k: 'Annualized Volatility', v: rt.volatility_ann_pct.toFixed(2) + '%' },
            { k: 'SPY Volatility (Ann.)', v: rt.spy_volatility_ann_pct.toFixed(2) + '%' },
            { k: 'Downside Deviation', v: rt.downside_deviation_pct.toFixed(2) + '%' },
            { k: 'Max Drawdown', v: fmtPct(rt.max_drawdown_pct, false), cls: 'neg' },
            { k: 'Current Drawdown', v: fmtPct(rt.current_drawdown_pct, false), cls: rt.current_drawdown_pct < 0 ? 'neg' : '' },
            { k: 'SPY Max Drawdown', v: fmtPct(rt.spy_max_drawdown_pct, false) },
            { k: 'Max DD Duration', v: rt.max_drawdown_days + ' trading days' },
            { k: 'Daily VaR (95% / 99%)', v: rt.var_95_pct.toFixed(2) + '% / ' + rt.var_99_pct.toFixed(2) + '%' },
            { k: 'Daily CVaR / ES (95%)', v: rt.cvar_95_pct.toFixed(2) + '%' },
            { k: 'Skewness / Excess Kurtosis', v: rt.skewness.toFixed(2) + ' / ' + rt.excess_kurtosis.toFixed(2) },
            { k: 'Tail Ratio (95/5)', v: rt.tail_ratio.toFixed(2) },
        ]);

        renderKv('kvPortfolio', [
            { k: 'Net Liquidation Equity', v: fmtUsd(pf.equity) },
            { k: 'Cash Balance', v: fmtUsd(pf.cash) },
            { k: 'Buying Power', v: fmtUsd(pf.buying_power) },
            { k: 'Long Exposure', v: fmtUsd(pf.long_exposure) },
            { k: 'Short Exposure', v: fmtUsd(pf.short_exposure) },
            { k: 'Gross Exposure', v: fmtUsd(pf.gross_exposure) + ' (' + pf.gross_exposure_pct.toFixed(1) + '%)' },
            { k: 'Net Exposure', v: fmtUsd(pf.net_exposure) + ' (' + pf.net_exposure_pct.toFixed(1) + '%)' },
            { k: 'Leverage Ratio', v: pf.leverage_ratio.toFixed(2) + 'x' },
            { k: 'Cash-to-Equity Ratio', v: pf.cash_ratio_pct.toFixed(1) + '%' },
            { k: 'Top Position Weight', v: pf.top_position_weight_pct.toFixed(1) + '%' },
            { k: 'HHI Concentration', v: pf.hhi_concentration.toFixed(3) },
        ]);

        renderKv('kvExecution', [
            { k: 'Closed Trades', v: String(ex.closed_trades) },
            { k: 'Win Rate', v: ex.win_rate_pct.toFixed(1) + '%' },
            { k: 'Profit Factor', v: ex.profit_factor.toFixed(2) },
            { k: 'Payoff Ratio (Win/Loss)', v: ex.payoff_ratio.toFixed(2) },
            { k: 'Trade Expectancy', v: fmtSignedUsd(ex.expectancy), cls: signClass(ex.expectancy) },
            { k: 'Average Win', v: fmtSignedUsd(ex.avg_win), cls: 'pos' },
            { k: 'Average Loss', v: fmtSignedUsd(ex.avg_loss), cls: 'neg' },
            { k: 'Largest Win', v: fmtSignedUsd(ex.largest_win), cls: 'pos' },
            { k: 'Largest Loss', v: fmtSignedUsd(ex.largest_loss), cls: 'neg' },
        ]);

        renderChart(data);

        const posBody = document.getElementById('positionsBody');
        document.getElementById('positionsCountLabel').textContent = (data.positions || []).length + ' active';
        posBody.innerHTML = (data.positions || []).length
            ? data.positions.map(function (pos) {
                return '<tr>' +
                    '<td><strong>' + pos.symbol + '</strong></td>' +
                    '<td>' + pos.side + '</td>' +
                    '<td class="num">' + pos.qty + '</td>' +
                    '<td class="num">' + fmtUsd(pos.avg_entry) + '</td>' +
                    '<td class="num">' + fmtUsd(pos.current_price) + '</td>' +
                    '<td class="num">' + fmtUsd(pos.market_value) + '</td>' +
                    '<td class="num ' + signClass(pos.unrealized_pl) + '">' + fmtSignedUsd(pos.unrealized_pl) + '</td>' +
                    '<td class="num ' + signClass(pos.unrealized_plpc) + '">' + fmtPct(pos.unrealized_plpc) + '</td>' +
                    '<td class="num">' + Number(pos.weight_pct).toFixed(1) + '%</td>' +
                    '</tr>';
            }).join('')
            : '<tr><td colspan="9" class="muted-cell">No open positions</td></tr>';

        const ordBody = document.getElementById('ordersBody');
        ordBody.innerHTML = (data.orders || []).length
            ? data.orders.map(function (o) {
                return '<tr>' +
                    '<td>' + o.submitted_at + '</td>' +
                    '<td>' + o.id + '</td>' +
                    '<td><strong>' + o.symbol + '</strong></td>' +
                    '<td>' + o.side + '</td>' +
                    '<td class="num">' + o.qty + '</td>' +
                    '<td>' + o.order_type + '</td>' +
                    '<td class="num">' + (o.filled_avg_price != null ? fmtUsd(o.filled_avg_price) : '--') + '</td>' +
                    '<td>' + o.status + '</td>' +
                    '</tr>';
            }).join('')
            : '<tr><td colspan="8" class="muted-cell">No recent orders</td></tr>';
    }

    async function loadStrategyDetail() {
        const data = await apiGet('/api/strategies/' + encodeURIComponent(state.strategyId) + '?timeframe=' + encodeURIComponent(state.timeframe));
        renderDetail(data);
    }

    function openStrategyDetail(id) {
        state.strategyId = id;
        state.view = 'detail';
        document.getElementById('strategiesView').hidden = true;
        document.getElementById('detailView').hidden = false;
        loadStrategyDetail();
    }

    function showStrategiesList() {
        state.view = 'list';
        document.getElementById('detailView').hidden = true;
        document.getElementById('strategiesView').hidden = false;
        loadStrategiesList();
    }

    function refreshCurrent() {
        if (state.view === 'detail') loadStrategyDetail();
        else loadStrategiesList();
    }

    function setupPolling() {
        if (state.pollTimer) window.clearInterval(state.pollTimer);
        if (state.autoPoll) {
            state.pollTimer = window.setInterval(refreshCurrent, 15000);
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.getElementById('backToListBtn').addEventListener('click', showStrategiesList);
        document.getElementById('refreshBtn').addEventListener('click', refreshCurrent);

        const autoBtn = document.getElementById('autoPollBtn');
        autoBtn.addEventListener('click', function () {
            state.autoPoll = !state.autoPoll;
            autoBtn.setAttribute('data-active', String(state.autoPoll));
            autoBtn.textContent = state.autoPoll ? 'Auto: 15s' : 'Auto: Off';
            setupPolling();
        });

        document.querySelectorAll('#timeframeGroup button').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('#timeframeGroup button').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                state.timeframe = btn.getAttribute('data-tf');
                loadStrategyDetail();
            });
        });

        document.querySelectorAll('#chartModeGroup button').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('#chartModeGroup button').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                state.chartMode = btn.getAttribute('data-mode');
                if (state.detailData) renderChart(state.detailData);
            });
        });

        document.getElementById('logoutBtn').addEventListener('click', async function () {
            await fetch('/api/auth/logout', {
                method: 'POST',
                headers: { 'X-CSRF-Token': getCsrf() },
                credentials: 'same-origin',
            });
            window.location.href = '/login';
        });

        loadStrategiesList();
        setupPolling();
    });
})();
