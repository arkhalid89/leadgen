# LeadGen Project Context

Last updated: 2026-04-03

## Scope of this context

This document consolidates project context from the repository contents currently available in this workspace.

### Previous chat sessions audit

- Searched workspace for likely transcript/session artifacts (`chat`, `session`, `history`, `conversation`, `transcript`).
- No prior chat transcript files were found in the project directory.
- Therefore, this context is based on code/docs present in the repo, not external chat history.

## Project summary

LeadGen is a multi-tool lead generation platform built on Flask. It provides:

- Google Maps scraping
- LinkedIn scraping
- Instagram scraping
- Generic website crawling for lead/contact discovery
- Email outreach support workflows
- User auth, activation/license flow, dashboard analytics, and lead management/export

It supports both:

- Web/server mode (Flask/Gunicorn, optional Docker deployment)
- Desktop mode via `pywebview` (`desktop.py`)

## Core architecture

### Backend

- Main app: `app.py` (monolithic Flask app with auth, billing, scraping APIs, dashboard, leads DB APIs)
- DB: SQLite (`leadgen.db` by default, configurable via env)
- Output: CSV files in `output/` (configurable)

### Async/job system

- Celery app: `task_queue/celery_app.py`
- Broker/backend defaults: Redis (`redis://localhost:6379/0` and `/1`)
- Job state persistence: Redis JSON blobs in `task_queue/job_store.py`
- Worker task: `task_queue/tasks.py` (`leadgen.scrape_gmaps_job`, `leadgen.scrape_cell`)
- Scraper execution entrypoint: `workers/scraper_worker.py`

### Scraper modules

- Google Maps: `scraper.py`
- LinkedIn: `linkedin_scraper.py`
- Instagram: `instagram_scraper.py`
- Web crawler: `web_crawler.py`

### Frontend

- Templates: `templates/*.html`
- Static JS/CSS: `static/js/*.js`, `static/css/style.css`

## Security and auth model (implemented)

From app configuration/docs:

- Session-based auth (Flask session cookies)
- `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE=Lax`, secure cookie in production
- CSRF protection via Flask-WTF, with manual protection for non-`/api/` routes
- Rate limiting via Flask-Limiter
- bcrypt password hashing (legacy migration support referenced in docs)
- Session fixation mitigation on login (`session.clear()` then reissue)

## Payments/licensing

Stripe integration exists in `app.py`:

- `POST /api/stripe/create-checkout`
- `POST /api/stripe/webhook`
- Handles checkout completion flows for activation/license usage

## Main route groups (from `app.py`)

### Auth & account

- UI: `/login`, `/register`, `/activate`, `/logout`
- API: `/api/auth/register`, `/api/auth/login`, `/api/auth/activate`, `/api/auth/me`
- Account: `/api/account/profile`, `/api/account/password`, `/api/account/delete`

### App pages

- `/`, `/dashboard`, `/database`, `/settings`
- Tool pages:
  - `/tools/google-maps`
  - `/tools/linkedin`
  - `/tools/instagram`
  - `/tools/web-crawler`
  - `/tools/email-outreach`

### Scraping APIs

- Google Maps: `/api/scrape`, `/api/status/<job_id>`, `/api/results/<job_id>`, `/api/download/<job_id>`, `/api/stop/<job_id>`
- LinkedIn: `/api/linkedin/*`
- Instagram: `/api/instagram/*`
- Web crawler: `/api/webcrawler/*`

### Leads/data APIs

- `/api/dashboard/stats`, `/api/dashboard/history`
- `/api/leads`, `/api/leads/filters`, `/api/leads/export`, `/api/leads/stats`
- `/api/leads/quality/<job_id>`
- Delete endpoints for single/bulk leads

### Utility

- Health endpoint: `/health`

## Data model (SQLite tables initialized in `app.py`)

- `users`
- `license_keys`
- `scrape_history`
- `leads`

These support authentication, licensing, scrape audit/history, and normalized lead records with JSON payloads.

## Runtime and deployment modes

### Local development

- `python app.py` for Flask-only run
- `./run_app.sh` starts Redis fallback check + Celery worker + Flask app

### Docker production

- `Dockerfile` uses `python:3.11-slim`, installs Chromium + chromedriver
- `docker-compose.yml` runs service `leadgen` with volumes for DB/output and healthcheck
- Designed/tuned for Oracle Cloud ARM free tier per `DEPLOYMENT.md`

### Desktop app

- `desktop.py` starts Flask on localhost and opens a native window with pywebview
- Sets `LEADGEN_DESKTOP=1` and resource paths for packaged mode

## Key environment variables

- `LEADGEN_SECRET_KEY` (required in production)
- `LEADGEN_DB_PATH`
- `LEADGEN_OUTPUT_DIR`
- `FLASK_ENV`
- `ALLOWED_ORIGINS`
- `RATELIMIT_STORAGE_URI`
- `LEADGEN_CELERY_BROKER`
- `LEADGEN_CELERY_BACKEND`
- `LEADGEN_REDIS_URL`
- Stripe: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_PRO`

## Dependencies snapshot

Core dependencies in `requirements.txt` include:

- Flask + CORS + Limiter + WTF
- Selenium + webdriver-manager
- BeautifulSoup + lxml + pandas
- Celery + Redis
- Stripe + bcrypt
- Gunicorn

## Operational notes

- Project currently has generated artifacts and runtime data in repo (`build/`, `dist/`, `output/`, `leadgen.db`).
- Queue state TTL in Redis defaults to 24h (`LEADGEN_JOB_TTL_SECONDS=86400`).
- In-memory job dicts are still present in `app.py` for some tool flows while GMaps is integrated with queue/job_store lifecycle.

## Suggested next step for future context continuity

If you want future sessions auto-incorporated, keep a running changelog/context file in-repo (e.g., append each completed chat outcome to this file or to `docs/SESSION_LOG.md`).
