# Beardslee St. Research — Website & Live Strategy Dashboard

Production FastAPI web application for **Beardslee St. Research**, featuring:
1. **Public Website (`/`)**: Deep maroon institutional landing page with white Beardslee St. Research branding, Substack **Our Research** link, **Dashboard** login button, and 2026 informational/non-advice legal disclosures.
2. **Hardened Two-Step Login (`/login`)**: No sign-up. Users must enter an authorized email (`isamalfuqaha@gmail.com` or `samuelnoginsky@gmail.com`) and press **Next** before the server unlocks the password step.
3. **Live Strategy Dashboard (`/dashboard`)**: Black-background institutional telemetry dashboard for **Klassiki**, pulling live account equity, positions, orders, and market clock from **Alpaca Paper Trading** and live **SPY** benchmark & price feeds from **yfinance**.

## Security Architecture
- **Zero Client-Side Secrets**: Authorized emails, scrypt password hashes, Alpaca API keys, and dashboard HTML/data live strictly on the server. Inspect Element on `/` or `/login` cannot reveal allowlisted emails, credentials, or any strategy telemetry.
- **Server-Side Route Protection**: Unauthenticated requests to `/dashboard` receive an HTTP 302 redirect to `/login`; `/api/*` endpoints return `401 Unauthorized`.
- **Two-Step Pre-Auth Nonce**: `/api/auth/check-email` verifies the email in constant time and issues a 5-minute IP-bound `HttpOnly` pre-auth cookie; `/api/auth/login` rejects any password attempt without a valid pre-auth cookie.
- **Scrypt Hashing & Rate Limiting**: Passwords use memory-hard `scrypt` (`N=32768, r=8, p=1`) with constant-time comparison, sliding-window rate limiting, and 15-minute lockout after 5 failed attempts.
- **Hardened Cookies & Headers**: `HttpOnly`, `SameSite=Strict`, `Secure` (when `FORCE_HTTPS=true`), double-submit CSRF tokens, `Content-Security-Policy`, `X-Frame-Options: DENY`, `Cache-Control: no-store`, and disabled OpenAPI/Swagger endpoints.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python3 set_passwords.py   # Paste AUTH_HASH_ISAM and AUTH_HASH_SAMUEL into .env
```

Add your Alpaca Paper Trading keys (`ALPACA_API_KEY` and `ALPACA_SECRET_KEY`) to `.env`, then run:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Production Deployment Notes
- Deploy behind HTTPS (e.g., Render, Railway, Fly.io, or Nginx/Caddy with Let's Encrypt) and set `FORCE_HTTPS=true` in `.env` so `Secure` cookies and HSTS are enforced.
- Run a single worker (`uvicorn app:app --workers 1`) or back `AuthStore` / `RateLimiter` with Redis if scaling across multiple workers.
