"""
Live market data & execution telemetry service for Beardslee St. Research.
- Pulls live account, equity curve, open positions, orders, and market clock from Alpaca Paper Trading API.
- Pulls live SPY benchmark history, live prices, and T-Bill risk-free rate via yfinance.
- Provides a clearly labeled paper-simulation fallback ONLY when DEMO_MODE=true or keys are unset.
"""

from __future__ import annotations

import datetime as dt
import math
import os
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx
import numpy as np

from metrics import compute_all_metrics

ET = ZoneInfo("America/New_York")

ALPACA_PAPER_BASE = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")


def _alpaca_headers() -> Optional[Dict[str, str]]:
    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not key or not secret:
        return None
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    }


def get_market_status() -> Dict[str, Any]:
    """
    Determine US Equity market status (Open / Pre-Market / After-Hours / Closed).
    Uses Alpaca /v2/clock when API keys are configured, with US Eastern schedule fallback.
    """
    now_et = dt.datetime.now(ET)
    headers = _alpaca_headers()
    if headers:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{ALPACA_PAPER_BASE}/v2/clock", headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    is_open = bool(data.get("is_open"))
                    return {
                        "is_open": is_open,
                        "session": "REGULAR_HOURS" if is_open else _infer_extended_session(now_et),
                        "label": "Market Open" if is_open else _session_label(_infer_extended_session(now_et)),
                        "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
                        "next_open": data.get("next_open"),
                        "next_close": data.get("next_close"),
                        "source": "Alpaca Clock API",
                    }
        except Exception:
            pass

    session = _infer_extended_session(now_et)
    is_open = session == "REGULAR_HOURS"
    return {
        "is_open": is_open,
        "session": session,
        "label": _session_label(session),
        "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
        "next_open": None,
        "next_close": None,
        "source": "NYSE Schedule (ET)",
    }


def _infer_extended_session(now_et: dt.datetime) -> str:
    if now_et.weekday() >= 5:
        return "CLOSED"
    t = now_et.time()
    if dt.time(9, 30) <= t < dt.time(16, 0):
        return "REGULAR_HOURS"
    if dt.time(4, 0) <= t < dt.time(9, 30):
        return "PRE_MARKET"
    if dt.time(16, 0) <= t < dt.time(20, 0):
        return "AFTER_HOURS"
    return "CLOSED"


def _session_label(session: str) -> str:
    return {
        "REGULAR_HOURS": "Market Open",
        "PRE_MARKET": "Pre-Market",
        "AFTER_HOURS": "After-Hours",
        "CLOSED": "Market Closed",
    }.get(session, "Market Closed")


def fetch_yfinance_benchmark(period: str = "6mo") -> Dict[str, Any]:
    """Fetch SPY benchmark history and live quote using yfinance."""
    try:
        import yfinance as yf  # type: ignore

        spy = yf.Ticker("SPY")
        hist = spy.history(period=period, interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            raise ValueError("Empty SPY history from yfinance")
        dates = [idx.strftime("%Y-%m-%d") for idx in hist.index]
        closes = [float(c) for c in hist["Close"].tolist()]
        last_price = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else last_price
        chg_pct = ((last_price / prev_close) - 1.0) * 100.0 if prev_close > 0 else 0.0

        # Live 13-week T-Bill rate (^IRX) for risk-free rate when available
        rf_rate = 0.045
        try:
            irx = yf.Ticker("^IRX").history(period="5d")
            if irx is not None and not irx.empty:
                rf_rate = float(irx["Close"].iloc[-1]) / 100.0
        except Exception:
            pass

        return {
            "symbol": "SPY",
            "dates": dates,
            "closes": closes,
            "last_price": round(last_price, 2),
            "change_pct": round(chg_pct, 2),
            "risk_free_rate": rf_rate,
            "source": "yfinance",
        }
    except Exception as exc:
        return {"error": str(exc)}


def fetch_yfinance_live_prices(symbols: List[str]) -> Dict[str, float]:
    """Pull latest prices for open positions via yfinance."""
    if not symbols:
        return {}
    try:
        import yfinance as yf  # type: ignore

        prices: Dict[str, float] = {}
        data = yf.download(
            tickers=" ".join(sorted(set(symbols))),
            period="2d",
            interval="1m",
            progress=False,
            threads=True,
        )
        if data is not None and not data.empty and "Close" in data:
            close_df = data["Close"]
            if len(symbols) == 1:
                val = close_df.dropna().iloc[-1]
                prices[symbols[0]] = float(val)
            else:
                for sym in symbols:
                    if sym in close_df and not close_df[sym].dropna().empty:
                        prices[sym] = float(close_df[sym].dropna().iloc[-1])
        return prices
    except Exception:
        return {}


def _timeframe_to_params(tf: str) -> Tuple[str, str]:
    mapping = {
        "1M": ("1M", "1mo"),
        "3M": ("3M", "3mo"),
        "6M": ("6M", "6mo"),
        "YTD": ("1A", "ytd"),
        "1Y": ("1A", "1y"),
        "ALL": ("all", "2y"),
    }
    return mapping.get(tf.upper(), ("6M", "6mo"))


def get_klassiki_telemetry(timeframe: str = "6M") -> Dict[str, Any]:
    """
    Assemble complete live telemetry for the Klassiki strategy.
    Uses Alpaca Paper Trading + yfinance when configured; falls back to
    deterministic simulated paper telemetry when DEMO_MODE=true or keys are unset.
    """
    alpaca_period, yf_period = _timeframe_to_params(timeframe)
    headers = _alpaca_headers()
    demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"

    if headers and not demo_mode:
        try:
            with httpx.Client(timeout=8.0) as client:
                acc_r = client.get(f"{ALPACA_PAPER_BASE}/v2/account", headers=headers)
                acc_r.raise_for_status()
                account = acc_r.json()

                hist_r = client.get(
                    f"{ALPACA_PAPER_BASE}/v2/account/portfolio/history",
                    headers=headers,
                    params={"period": alpaca_period, "timeframe": "1D", "extended_hours": "false"},
                )
                hist_r.raise_for_status()
                ph = hist_r.json()

                pos_r = client.get(f"{ALPACA_PAPER_BASE}/v2/positions", headers=headers)
                pos_r.raise_for_status()
                raw_positions = pos_r.json()

                ord_r = client.get(
                    f"{ALPACA_PAPER_BASE}/v2/orders",
                    headers=headers,
                    params={"status": "all", "limit": 25, "direction": "desc"},
                )
                ord_r.raise_for_status()
                raw_orders = ord_r.json()

            timestamps = ph.get("timestamp", [])
            equities = ph.get("equity", [])
            strat_dates = [
                dt.datetime.fromtimestamp(ts, tz=ET).strftime("%Y-%m-%d")
                for ts, eq in zip(timestamps, equities)
                if eq is not None and float(eq) > 0
            ]
            strat_eq = [float(eq) for eq in equities if eq is not None and float(eq) > 0]

            # Align with SPY via yfinance
            spy_data = fetch_yfinance_benchmark(period=yf_period)
            if "error" in spy_data:
                raise RuntimeError(f"yfinance error: {spy_data['error']}")

            spy_map = dict(zip(spy_data["dates"], spy_data["closes"]))
            aligned_dates, aligned_eq, aligned_spy = [], [], []
            last_spy = spy_data["closes"][0]
            for d, e in zip(strat_dates, strat_eq):
                if d in spy_map:
                    last_spy = spy_map[d]
                aligned_dates.append(d)
                aligned_eq.append(e)
                aligned_spy.append(last_spy)

            # Enrich open positions with live yfinance quotes if available
            symbols = [p.get("symbol", "") for p in raw_positions if p.get("symbol")]
            yf_quotes = fetch_yfinance_live_prices(symbols)
            equity_val = float(account.get("equity", 1.0) or 1.0)

            positions = []
            for p in raw_positions:
                sym = p.get("symbol", "")
                mv = float(p.get("market_value", 0) or 0)
                live_px = yf_quotes.get(sym, float(p.get("current_price", 0) or 0))
                positions.append(
                    {
                        "symbol": sym,
                        "side": p.get("side", "long").upper(),
                        "qty": float(p.get("qty", 0) or 0),
                        "avg_entry": round(float(p.get("avg_entry_price", 0) or 0), 2),
                        "current_price": round(live_px, 2),
                        "market_value": round(mv, 2),
                        "unrealized_pl": round(float(p.get("unrealized_pl", 0) or 0), 2),
                        "unrealized_plpc": round(float(p.get("unrealized_plpc", 0) or 0) * 100, 2),
                        "weight_pct": round(abs(mv) / equity_val * 100, 1) if equity_val > 0 else 0.0,
                    }
                )

            orders = [
                {
                    "id": o.get("id", "")[:8],
                    "submitted_at": (o.get("submitted_at") or "")[:19].replace("T", " "),
                    "symbol": o.get("symbol", ""),
                    "side": (o.get("side") or "").upper(),
                    "qty": o.get("qty") or o.get("notional") or "0",
                    "order_type": (o.get("type") or "").upper(),
                    "filled_avg_price": round(float(o["filled_avg_price"]), 2)
                    if o.get("filled_avg_price")
                    else None,
                    "status": (o.get("status") or "").upper(),
                }
                for o in raw_orders[:20]
            ]

            metrics = compute_all_metrics(
                dates=aligned_dates,
                strat_equity=aligned_eq,
                spy_prices=aligned_spy,
                account=account,
                positions=positions,
                closed_trades=[],
                risk_free_rate=spy_data.get("risk_free_rate", 0.045),
            )

            return {
                "mode": "LIVE_ALPACA_PAPER",
                "strategy": {
                    "id": "klassiki",
                    "name": "Klassiki",
                    "asset_class": "US Equities · Systematic",
                    "venue": "Alpaca Paper Trading",
                    "benchmark": "SPY",
                    "status": "LIVE",
                    "timeframe": timeframe.upper(),
                },
                "market_status": get_market_status(),
                "spy_quote": {
                    "symbol": "SPY",
                    "price": spy_data["last_price"],
                    "change_pct": spy_data["change_pct"],
                    "source": "yfinance",
                },
                "metrics": metrics,
                "positions": positions,
                "orders": orders,
                "updated_at": dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET"),
            }
        except Exception as exc:
            # Fall back gracefully with error notice if live feed fails
            fallback = _build_demo_telemetry(timeframe)
            fallback["live_feed_notice"] = f"Alpaca/yfinance fallback active: {exc}"
            return fallback

    return _build_demo_telemetry(timeframe)


def _build_demo_telemetry(timeframe: str = "6M") -> Dict[str, Any]:
    """
    Deterministic paper-trading simulation telemetry used only before Alpaca API keys
    are added to .env. Clearly flagged as mode='DEMO_SIMULATION'.
    """
    days_map = {"1M": 22, "3M": 63, "6M": 126, "YTD": 180, "1Y": 252, "ALL": 252}
    n_days = days_map.get(timeframe.upper(), 126)

    rng = np.random.default_rng(20260401)
    today = dt.date.today()
    dates: List[str] = []
    cur = today - dt.timedelta(days=int(n_days * 1.5))
    while len(dates) < n_days:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur += dt.timedelta(days=1)

    spy_rets = rng.normal(0.00045, 0.0095, size=n_days - 1)
    alpha_rets = rng.normal(0.00042, 0.0062, size=n_days - 1)
    strat_rets = 0.62 * spy_rets + alpha_rets

    spy_prices = [540.0]
    strat_eq = [100000.0]
    for rs, rb in zip(strat_rets, spy_rets):
        spy_prices.append(spy_prices[-1] * (1.0 + rb))
        strat_eq.append(strat_eq[-1] * (1.0 + rs))

    eq_now = round(strat_eq[-1], 2)
    eq_prev = round(strat_eq[-2], 2)
    cash = round(eq_now * 0.142, 2)

    positions = [
        {"symbol": "NVDA", "side": "LONG", "qty": 165, "avg_entry": 124.40, "current_price": 138.90, "market_value": 22918.50, "unrealized_pl": 2392.50, "unrealized_plpc": 11.66, "weight_pct": round(22918.50 / eq_now * 100, 1)},
        {"symbol": "MSFT", "side": "LONG", "qty": 48, "avg_entry": 418.20, "current_price": 436.75, "market_value": 20964.00, "unrealized_pl": 890.40, "unrealized_plpc": 4.44, "weight_pct": round(20964.00 / eq_now * 100, 1)},
        {"symbol": "META", "side": "LONG", "qty": 32, "avg_entry": 562.10, "current_price": 594.80, "market_value": 19033.60, "unrealized_pl": 1046.40, "unrealized_plpc": 5.82, "weight_pct": round(19033.60 / eq_now * 100, 1)},
        {"symbol": "JPM", "side": "LONG", "qty": 75, "avg_entry": 226.80, "current_price": 241.20, "market_value": 18090.00, "unrealized_pl": 1080.00, "unrealized_plpc": 6.35, "weight_pct": round(18090.00 / eq_now * 100, 1)},
        {"symbol": "LLY", "side": "LONG", "qty": 18, "avg_entry": 842.00, "current_price": 826.50, "market_value": 14877.00, "unrealized_pl": -279.00, "unrealized_plpc": -1.84, "weight_pct": round(14877.00 / eq_now * 100, 1)},
        {"symbol": "IWM", "side": "SHORT", "qty": -40, "avg_entry": 224.60, "current_price": 219.85, "market_value": -8794.00, "unrealized_pl": 190.00, "unrealized_plpc": 2.11, "weight_pct": round(8794.00 / eq_now * 100, 1)},
    ]

    closed_trades = [
        {"realized_pnl": 1420.50}, {"realized_pnl": -540.20}, {"realized_pnl": 890.00},
        {"realized_pnl": 1150.75}, {"realized_pnl": -310.00}, {"realized_pnl": 640.40},
        {"realized_pnl": -485.10}, {"realized_pnl": 1780.00}, {"realized_pnl": 520.30},
        {"realized_pnl": -295.00}, {"realized_pnl": 965.20}, {"realized_pnl": 710.80},
    ]

    orders = [
        {"id": "alp-9f21", "submitted_at": f"{dates[-1]} 10:15:02", "symbol": "NVDA", "side": "BUY", "qty": "25", "order_type": "LIMIT", "filled_avg_price": 137.85, "status": "FILLED"},
        {"id": "alp-8e14", "submitted_at": f"{dates[-1]} 09:42:18", "symbol": "IWM", "side": "SELL", "qty": "40", "order_type": "MARKET", "filled_avg_price": 224.60, "status": "FILLED"},
        {"id": "alp-7c09", "submitted_at": f"{dates[-2]} 15:48:11", "symbol": "JPM", "side": "BUY", "qty": "75", "order_type": "LIMIT", "filled_avg_price": 226.80, "status": "FILLED"},
        {"id": "alp-6b88", "submitted_at": f"{dates[-3]} 11:20:45", "symbol": "AMD", "side": "SELL", "qty": "90", "order_type": "MARKET", "filled_avg_price": 154.30, "status": "FILLED"},
    ]

    account = {
        "equity": eq_now,
        "last_equity": eq_prev,
        "cash": cash,
        "buying_power": round(cash * 4.0, 2),
    }

    metrics = compute_all_metrics(
        dates=dates,
        strat_equity=strat_eq,
        spy_prices=spy_prices,
        account=account,
        positions=positions,
        closed_trades=closed_trades,
        risk_free_rate=0.045,
    )

    spy_chg = ((spy_prices[-1] / spy_prices[-2]) - 1.0) * 100.0
    return {
        "mode": "DEMO_SIMULATION",
        "strategy": {
            "id": "klassiki",
            "name": "Klassiki",
            "asset_class": "US Equities · Systematic",
            "venue": "Alpaca Paper Trading (Demo Feed — Set ALPACA_API_KEY in .env)",
            "benchmark": "SPY",
            "status": "PAPER SIM",
            "timeframe": timeframe.upper(),
        },
        "market_status": get_market_status(),
        "spy_quote": {
            "symbol": "SPY",
            "price": round(spy_prices[-1], 2),
            "change_pct": round(spy_chg, 2),
            "source": "Simulated (yfinance active in live mode)",
        },
        "metrics": metrics,
        "positions": positions,
        "orders": orders,
        "updated_at": dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET"),
    }
