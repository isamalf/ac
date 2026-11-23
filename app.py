"""
Beardslee St. Research — Public Website & Secure Live Strategy Dashboard
FastAPI server with strict server-side authentication, CSRF protection,
rate limiting, HttpOnly sessions, Alpaca Paper Trading, and yfinance telemetry.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from market_data import get_klassiki_telemetry, get_market_status
from security import (
    AuthStore,
    RateLimiter,
    SessionRecord,
    get_password_hash_for_email,
    is_email_allowed,
    verify_password,
)

BASE_DIR = Path(__file__).resolve().parent
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "false").lower() == "true"

app = FastAPI(
    title="Beardslee St. Research",
    docs_url=None,      # Disable Swagger/OpenAPI docs in production
    redoc_url=None,     # Disable ReDoc in production
    openapi_url=None,   # Do not expose API schema to scanners
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

auth_store = AuthStore(
    idle_ttl=int(os.getenv("SESSION_IDLE_SECONDS", "3600")),
    absolute_ttl=int(os.getenv("SESSION_MAX_SECONDS", "28800")),
    preauth_ttl=300,
)
email_limiter = RateLimiter(max_attempts=10, window_seconds=300, lockout_threshold=8, lockout_seconds=900)
login_limiter = RateLimiter(max_attempts=8, window_seconds=300, lockout_threshold=5, lockout_seconds=900)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    if FORCE_HTTPS:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    if request.url.path.startswith(("/dashboard", "/login", "/api/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
    return response


def _require_session(bsr_session: Optional[str]) -> SessionRecord:
    sess = auth_store.get_session(bsr_session)
    if not sess:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return sess


def _verify_csrf(cookie_csrf: Optional[str], header_csrf: Optional[str]) -> None:
    if not cookie_csrf or not header_csrf or not hmac.compare_digest(cookie_csrf, header_csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")


class EmailCheckPayload(BaseModel):
    email: str


class LoginPayload(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Public Pages (Zero dashboard HTML, zero credentials, zero strategy data)
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, bsr_session: Optional[str] = Cookie(default=None)):
    if auth_store.get_session(bsr_session):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    csrf_token = secrets.token_urlsafe(32)
    resp = templates.TemplateResponse("login.html", {"request": request, "csrf_token": csrf_token})
    resp.set_cookie(
        key="bsr_csrf",
        value=csrf_token,
        httponly=False,  # Readable by login.js to send in X-CSRF-Token header (double-submit pattern)
        secure=FORCE_HTTPS,
        samesite="strict",
        max_age=3600,
    )
    return resp


# ---------------------------------------------------------------------------
# Protected Dashboard Page (Server-side session gate)
# ---------------------------------------------------------------------------


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, bsr_session: Optional[str] = Cookie(default=None)):
    sess = auth_store.get_session(bsr_session)
    if not sess:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user_email": sess.email,
            "csrf_token": sess.csrf_token,
        },
    )


# ---------------------------------------------------------------------------
# Two-Step Authentication API
# ---------------------------------------------------------------------------


@app.post("/api/auth/check-email")
async def api_check_email(
    payload: EmailCheckPayload,
    request: Request,
    response: Response,
    bsr_csrf: Optional[str] = Cookie(default=None),
    x_csrf_token: Optional[str] = Header(default=None),
):
    _verify_csrf(bsr_csrf, x_csrf_token)
    ip = _client_ip(request)
    allowed_rate, retry_after = email_limiter.check_allowed(f"email:{ip}")
    if not allowed_rate:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Try again in {retry_after} seconds.",
        )

    email = (payload.email or "").strip().lower()
    if not is_email_allowed(email):
        email_limiter.record_failure(f"email:{ip}")
        # Clear any stale pre-auth cookie
        response.delete_cookie("bsr_preauth")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted. This email is not authorized for dashboard access.",
        )

    email_limiter.record_success(f"email:{ip}")
    preauth_token = auth_store.create_preauth(email=email, ip=ip)
    res = JSONResponse({"ok": True, "email": email})
    res.set_cookie(
        key="bsr_preauth",
        value=preauth_token,
        httponly=True,
        secure=FORCE_HTTPS,
        samesite="strict",
        max_age=300,
    )
    return res


@app.post("/api/auth/login")
async def api_login(
    payload: LoginPayload,
    request: Request,
    bsr_csrf: Optional[str] = Cookie(default=None),
    bsr_preauth: Optional[str] = Cookie(default=None),
    x_csrf_token: Optional[str] = Header(default=None),
):
    _verify_csrf(bsr_csrf, x_csrf_token)
    ip = _client_ip(request)
    email = (payload.email or "").strip().lower()
    rate_key = f"login:{ip}:{email}"

    allowed_rate, retry_after = login_limiter.check_allowed(rate_key)
    if not allowed_rate:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account temporarily locked. Try again in {retry_after} seconds.",
        )

    # Enforce strict two-step verification (pre-auth cookie must be valid for this email + IP)
    if not is_email_allowed(email) or not auth_store.verify_preauth(bsr_preauth, email, ip):
        login_limiter.record_failure(rate_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required before entering password.",
        )

    stored_hash = get_password_hash_for_email(email)
    if not stored_hash:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password not configured on server. Run set_passwords.py to initialize credentials.",
        )

    if not verify_password(payload.password, stored_hash):
        login_limiter.record_failure(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    login_limiter.record_success(rate_key)
    auth_store.consume_preauth(bsr_preauth)
    sess = auth_store.create_session(email=email, ip=ip)

    res = JSONResponse({"ok": True, "redirect": "/dashboard"})
    res.delete_cookie("bsr_preauth")
    res.set_cookie(
        key="bsr_session",
        value=sess.session_id,
        httponly=True,
        secure=FORCE_HTTPS,
        samesite="strict",
        max_age=auth_store.absolute_ttl,
    )
    res.set_cookie(
        key="bsr_csrf",
        value=sess.csrf_token,
        httponly=False,
        secure=FORCE_HTTPS,
        samesite="strict",
        max_age=auth_store.absolute_ttl,
    )
    return res


@app.post("/api/auth/logout")
async def api_logout(
    bsr_session: Optional[str] = Cookie(default=None),
    bsr_csrf: Optional[str] = Cookie(default=None),
    x_csrf_token: Optional[str] = Header(default=None),
):
    sess = auth_store.get_session(bsr_session)
    if sess:
        _verify_csrf(bsr_csrf, x_csrf_token)
        auth_store.revoke_session(bsr_session)
    res = JSONResponse({"ok": True, "redirect": "/login"})
    res.delete_cookie("bsr_session")
    res.delete_cookie("bsr_preauth")
    return res


# ---------------------------------------------------------------------------
# Protected Strategy & Market Telemetry APIs
# ---------------------------------------------------------------------------


@app.get("/api/market-status")
async def api_market_status(bsr_session: Optional[str] = Cookie(default=None)):
    _require_session(bsr_session)
    return get_market_status()


@app.get("/api/strategies")
async def api_strategies(bsr_session: Optional[str] = Cookie(default=None)):
    _require_session(bsr_session)
    klassiki = get_klassiki_telemetry(timeframe="6M")
    m = klassiki["metrics"]
    return {
        "market_status": klassiki["market_status"],
        "spy_quote": klassiki["spy_quote"],
        "strategies": [
            {
                "id": "klassiki",
                "name": "Klassiki",
                "asset_class": klassiki["strategy"]["asset_class"],
                "venue": klassiki["strategy"]["venue"],
                "benchmark": "SPY",
                "status": klassiki["strategy"]["status"],
                "mode": klassiki["mode"],
                "equity": m["portfolio"]["equity"],
                "daily_pnl_pct": m["performance"]["daily_pnl_pct"],
                "total_return_pct": m["performance"]["total_return_pct"],
                "sharpe_ratio": m["risk_adjusted"]["sharpe_ratio"],
                "max_drawdown_pct": m["risk_tail"]["max_drawdown_pct"],
                "beta": m["benchmark_spy"]["beta"],
                "open_positions": m["portfolio"]["open_positions_count"],
            }
        ],
        "updated_at": klassiki["updated_at"],
    }


@app.get("/api/strategies/{strategy_id}")
async def api_strategy_detail(
    strategy_id: str,
    timeframe: str = "6M",
    bsr_session: Optional[str] = Cookie(default=None),
):
    _require_session(bsr_session)
    if strategy_id.lower() != "klassiki":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found.")
    return get_klassiki_telemetry(timeframe=timeframe)
