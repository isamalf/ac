"""
Quantitative performance, risk, benchmark, portfolio, and execution analytics
for Beardslee St. Research strategies (compared against SPY benchmark).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
import numpy as np

TRADING_DAYS_PER_YEAR = 252


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def compute_drawdown_series(equity: np.ndarray) -> np.ndarray:
    """Return percentage drawdown series (0.0 to -1.0)."""
    if len(equity) == 0:
        return np.array([])
    peaks = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peaks > 0, (equity - peaks) / peaks, 0.0)
    return dd


def compute_max_drawdown_duration(drawdowns: np.ndarray) -> int:
    """Maximum consecutive periods spent below previous high-water mark."""
    max_dur = 0
    cur = 0
    for d in drawdowns:
        if d < -1e-9:
            cur += 1
            if cur > max_dur:
                max_dur = cur
        else:
            cur = 0
    return int(max_dur)


def compute_all_metrics(
    dates: List[str],
    strat_equity: List[float],
    spy_prices: List[float],
    account: Dict[str, Any],
    positions: List[Dict[str, Any]],
    closed_trades: List[Dict[str, Any]],
    risk_free_rate: float = 0.045,
) -> Dict[str, Any]:
    """
    Compute full suite of institutional quantitative metrics for Klassiki vs. SPY.
    """
    eq = np.asarray([_safe_float(x) for x in strat_equity], dtype=np.float64)
    spy = np.asarray([_safe_float(x) for x in spy_prices], dtype=np.float64)

    n = min(len(eq), len(spy), len(dates))
    eq = eq[-n:]
    spy = spy[-n:]
    dates = dates[-n:]

    if n < 2 or eq[0] <= 0 or spy[0] <= 0:
        return {"error": "Insufficient history to compute metrics."}

    # Daily simple returns
    r_strat = np.diff(eq) / eq[:-1]
    r_spy = np.diff(spy) / spy[:-1]
    rf_daily = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess_strat = r_strat - rf_daily
    excess_spy = r_spy - rf_daily
    active_ret = r_strat - r_spy

    # Cumulative & annualized returns
    total_return = (eq[-1] / eq[0]) - 1.0
    spy_total_return = (spy[-1] / spy[0]) - 1.0
    years = max(len(r_strat) / TRADING_DAYS_PER_YEAR, 1.0 / TRADING_DAYS_PER_YEAR)
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1.0 else -1.0
    spy_cagr = (1.0 + spy_total_return) ** (1.0 / years) - 1.0 if spy_total_return > -1.0 else -1.0

    # Volatility & downside deviation
    vol_daily = float(np.std(r_strat, ddof=1)) if len(r_strat) > 1 else 0.0
    vol_ann = vol_daily * math.sqrt(TRADING_DAYS_PER_YEAR)
    spy_vol_ann = (float(np.std(r_spy, ddof=1)) if len(r_spy) > 1 else 0.0) * math.sqrt(TRADING_DAYS_PER_YEAR)

    downside_diff = np.minimum(r_strat - rf_daily, 0.0)
    downside_dev_ann = float(np.sqrt(np.mean(downside_diff**2))) * math.sqrt(TRADING_DAYS_PER_YEAR)

    # Drawdowns
    dd_strat = compute_drawdown_series(eq)
    dd_spy = compute_drawdown_series(spy)
    max_dd = float(np.min(dd_strat))
    cur_dd = float(dd_strat[-1])
    spy_max_dd = float(np.min(dd_spy))
    max_dd_dur = compute_max_drawdown_duration(dd_strat)
    avg_dd = float(np.mean(dd_strat[dd_strat < 0])) if np.any(dd_strat < 0) else 0.0

    # Risk-adjusted ratios
    mean_excess_ann = float(np.mean(excess_strat)) * TRADING_DAYS_PER_YEAR
    sharpe = mean_excess_ann / vol_ann if vol_ann > 1e-9 else 0.0
    sortino = mean_excess_ann / downside_dev_ann if downside_dev_ann > 1e-9 else 0.0
    calmar = cagr / abs(max_dd) if abs(max_dd) > 1e-9 else 0.0
    sterling = cagr / (abs(avg_dd) + 0.10) if abs(avg_dd) > 0 else 0.0

    # Benchmark / CAPM stats vs SPY
    spy_var = float(np.var(r_spy, ddof=1)) if len(r_spy) > 1 else 0.0
    cov = float(np.cov(r_strat, r_spy, ddof=1)[0, 1]) if len(r_strat) > 1 else 0.0
    beta = cov / spy_var if spy_var > 1e-12 else 0.0
    corr = float(np.corrcoef(r_strat, r_spy)[0, 1]) if (vol_daily > 1e-12 and spy_vol_ann > 1e-12) else 0.0
    r_squared = corr**2

    # Annualized Jensen's alpha
    spy_mean_excess_ann = float(np.mean(excess_spy)) * TRADING_DAYS_PER_YEAR
    jensen_alpha = mean_excess_ann - beta * spy_mean_excess_ann
    treynor = mean_excess_ann / beta if abs(beta) > 1e-6 else 0.0

    # Tracking error & Information Ratio
    te_ann = (float(np.std(active_ret, ddof=1)) if len(active_ret) > 1 else 0.0) * math.sqrt(TRADING_DAYS_PER_YEAR)
    info_ratio = (float(np.mean(active_ret)) * TRADING_DAYS_PER_YEAR) / te_ann if te_ann > 1e-9 else 0.0

    # Up / Down capture ratios
    up_mask = r_spy > 0
    down_mask = r_spy < 0
    up_capture = (
        (float(np.mean(r_strat[up_mask])) / float(np.mean(r_spy[up_mask])))
        if (np.any(up_mask) and abs(float(np.mean(r_spy[up_mask]))) > 1e-9)
        else 0.0
    )
    down_capture = (
        (float(np.mean(r_strat[down_mask])) / float(np.mean(r_spy[down_mask])))
        if (np.any(down_mask) and abs(float(np.mean(r_spy[down_mask]))) > 1e-9)
        else 0.0
    )

    # Omega ratio & Gain-to-Pain
    gains = np.sum(np.maximum(r_strat - rf_daily, 0.0))
    losses = np.sum(np.maximum(rf_daily - r_strat, 0.0))
    omega = float(gains / losses) if losses > 1e-12 else 0.0
    neg_sum = float(np.sum(np.abs(r_strat[r_strat < 0])))
    gain_to_pain = float(np.sum(r_strat) / neg_sum) if neg_sum > 1e-12 else 0.0

    # Tail risk: Historical VaR, CVaR (Expected Shortfall), Skew, Kurtosis, Tail Ratio
    var_95 = float(np.percentile(r_strat, 5))
    var_99 = float(np.percentile(r_strat, 1))
    cvar_95 = float(np.mean(r_strat[r_strat <= var_95])) if np.any(r_strat <= var_95) else var_95
    p95 = float(np.percentile(r_strat, 95))
    tail_ratio = abs(p95 / var_95) if abs(var_95) > 1e-9 else 0.0

    centered = r_strat - np.mean(r_strat)
    m2 = float(np.mean(centered**2))
    skew = float(np.mean(centered**3) / (m2**1.5)) if m2 > 1e-15 else 0.0
    kurt = float(np.mean(centered**4) / (m2**2) - 3.0) if m2 > 1e-15 else 0.0

    # Portfolio & Exposure ratios from live Alpaca account + positions
    equity_val = _safe_float(account.get("equity"), eq[-1])
    last_equity = _safe_float(account.get("last_equity"), eq[-2] if len(eq) >= 2 else equity_val)
    cash_val = _safe_float(account.get("cash"), 0.0)
    buying_power = _safe_float(account.get("buying_power"), 0.0)
    daily_pnl = equity_val - last_equity
    daily_pnl_pct = (daily_pnl / last_equity) if last_equity > 0 else 0.0

    long_mv = sum(_safe_float(p.get("market_value")) for p in positions if _safe_float(p.get("qty")) > 0)
    short_mv = sum(abs(_safe_float(p.get("market_value"))) for p in positions if _safe_float(p.get("qty")) < 0)
    gross_exposure = long_mv + short_mv
    net_exposure = long_mv - short_mv
    gross_pct = (gross_exposure / equity_val) if equity_val > 0 else 0.0
    net_pct = (net_exposure / equity_val) if equity_val > 0 else 0.0
    cash_pct = (cash_val / equity_val) if equity_val > 0 else 0.0
    leverage = gross_pct

    weights = [abs(_safe_float(p.get("market_value"))) / equity_val for p in positions if equity_val > 0]
    top_weight = max(weights) if weights else 0.0
    hhi = sum(w**2 for w in weights) if weights else 0.0

    # Trade & Execution Statistics
    pnls = [_safe_float(t.get("realized_pnl")) for t in closed_trades if t.get("realized_pnl") is not None]
    wins = [p for p in pnls if p > 0]
    losses_t = [p for p in pnls if p < 0]
    total_trades = len(pnls)
    win_rate = (len(wins) / total_trades) if total_trades > 0 else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses_t) / len(losses_t)) if losses_t else 0.0
    payoff_ratio = abs(avg_win / avg_loss) if abs(avg_loss) > 1e-9 else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses_t))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 1e-9 else 0.0
    expectancy = (win_rate * avg_win) + ((1.0 - win_rate) * avg_loss) if total_trades > 0 else 0.0

    # Rebased series (Base = 100) and % return series for charting
    strat_rebased = [round(float((v / eq[0] - 1.0) * 100.0), 3) for v in eq]
    spy_rebased = [round(float((v / spy[0] - 1.0) * 100.0), 3) for v in spy]

    # Rolling 20-day Sharpe for secondary chart
    rolling_sharpe: List[Optional[float]] = []
    win_len = 20
    for idx in range(len(r_strat)):
        if idx + 1 < win_len:
            rolling_sharpe.append(None)
        else:
            sl = excess_strat[idx + 1 - win_len : idx + 1]
            sd = float(np.std(sl, ddof=1))
            rs = (float(np.mean(sl)) / sd * math.sqrt(TRADING_DAYS_PER_YEAR)) if sd > 1e-9 else 0.0
            rolling_sharpe.append(round(rs, 2))
    rolling_sharpe.insert(0, None)

    return {
        "performance": {
            "total_return_pct": round(total_return * 100, 2),
            "spy_total_return_pct": round(spy_total_return * 100, 2),
            "excess_return_pct": round((total_return - spy_total_return) * 100, 2),
            "cagr_pct": round(cagr * 100, 2),
            "spy_cagr_pct": round(spy_cagr * 100, 2),
            "daily_pnl": round(daily_pnl, 2),
            "daily_pnl_pct": round(daily_pnl_pct * 100, 2),
            "cumulative_pnl": round(float(eq[-1] - eq[0]), 2),
            "best_day_pct": round(float(np.max(r_strat)) * 100, 2),
            "worst_day_pct": round(float(np.min(r_strat)) * 100, 2),
            "positive_days_pct": round(float(np.mean(r_strat > 0)) * 100, 1),
        },
        "risk_adjusted": {
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "calmar_ratio": round(calmar, 2),
            "treynor_ratio": round(treynor, 3),
            "information_ratio": round(info_ratio, 2),
            "omega_ratio": round(omega, 2),
            "sterling_ratio": round(sterling, 2),
            "gain_to_pain": round(gain_to_pain, 2),
            "risk_free_rate_pct": round(risk_free_rate * 100, 2),
        },
        "benchmark_spy": {
            "beta": round(beta, 2),
            "jensen_alpha_pct": round(jensen_alpha * 100, 2),
            "correlation": round(corr, 2),
            "r_squared": round(r_squared, 2),
            "tracking_error_pct": round(te_ann * 100, 2),
            "up_capture_pct": round(up_capture * 100, 1),
            "down_capture_pct": round(down_capture * 100, 1),
        },
        "risk_tail": {
            "volatility_ann_pct": round(vol_ann * 100, 2),
            "spy_volatility_ann_pct": round(spy_vol_ann * 100, 2),
            "downside_deviation_pct": round(downside_dev_ann * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "current_drawdown_pct": round(cur_dd * 100, 2),
            "spy_max_drawdown_pct": round(spy_max_dd * 100, 2),
            "max_drawdown_days": max_dd_dur,
            "var_95_pct": round(var_95 * 100, 2),
            "var_99_pct": round(var_99 * 100, 2),
            "cvar_95_pct": round(cvar_95 * 100, 2),
            "skewness": round(skew, 2),
            "excess_kurtosis": round(kurt, 2),
            "tail_ratio": round(tail_ratio, 2),
        },
        "portfolio": {
            "equity": round(equity_val, 2),
            "cash": round(cash_val, 2),
            "buying_power": round(buying_power, 2),
            "long_exposure": round(long_mv, 2),
            "short_exposure": round(short_mv, 2),
            "gross_exposure": round(gross_exposure, 2),
            "net_exposure": round(net_exposure, 2),
            "gross_exposure_pct": round(gross_pct * 100, 1),
            "net_exposure_pct": round(net_pct * 100, 1),
            "cash_ratio_pct": round(cash_pct * 100, 1),
            "leverage_ratio": round(leverage, 2),
            "open_positions_count": len(positions),
            "top_position_weight_pct": round(top_weight * 100, 1),
            "hhi_concentration": round(hhi, 3),
        },
        "execution": {
            "closed_trades": total_trades,
            "win_rate_pct": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 2),
            "payoff_ratio": round(payoff_ratio, 2),
            "expectancy": round(expectancy, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "largest_win": round(max(wins), 2) if wins else 0.0,
            "largest_loss": round(min(losses_t), 2) if losses_t else 0.0,
        },
        "series": {
            "dates": dates,
            "klassiki_return_pct": strat_rebased,
            "spy_return_pct": spy_rebased,
            "klassiki_equity": [round(float(v), 2) for v in eq],
            "klassiki_drawdown_pct": [round(float(d * 100), 2) for d in dd_strat],
            "spy_drawdown_pct": [round(float(d * 100), 2) for d in dd_spy],
            "rolling_20d_sharpe": rolling_sharpe,
        },
    }
