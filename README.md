# Indian Store

A full-featured e-commerce web application built with Flask 3 and SQLAlchemy. It covers the
whole shopping surface of a small online store: catalogue and search, cart and checkout,
accounts and addresses, orders and tracking, reviews and questions, support tickets, gift
cards, a loyalty programme, and an operations console for admins.

The application is self-contained. It has no external services, API keys, or network calls at
runtime: the shopping assistant is an in-process, pure-Python engine (`ai.py`) rather than a
hosted model, and checkout is a simulation that never charges a card. It runs on SQLite by
default, so a fresh clone starts with no configuration.

## Stack

- Python 3.12, Flask 3.0, Flask-WTF (CSRF), Flask-SQLAlchemy / SQLAlchemy 2.
- Flask-Migrate (Alembic) for schema migrations.
- Jinja templates and hand-written CSS/JS; no build step.
- waitress as the production WSGI server; the Flask server is for development only.
- Python-dotenv for optional `.env` loading.

## Project layout

```
app.py              application factory, filters, error handlers, security headers
config.py           environment-driven config classes (development / testing / production)
wsgi.py             production entry point (waitress); also the Flask CLI target
run.py              development entry point
extensions.py       shared SQLAlchemy and Migrate instances
models.py           SQLAlchemy models for the whole domain
helpers.py          cart/pricing/currency/session helpers and view decorators
ai.py               in-house shopping assistant engine (pure Python, no I/O)
seed.py             deterministic demo data generator
logging_setup.py    logging configuration
blueprints/         auth, catalog, cart, account, admin, seo, assistant, health
migrations/         Alembic migration environment and versions
templates/          Jinja templates
static/             CSS and JS
test_*.py           test suites (see below)
```

## Quickstart

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

python seed.py --reset      # create instance/shop.db and load the demo catalogue
python run.py               # http://127.0.0.1:5000
```

The database is created automatically on first run in development. To load demo data,
`seed.py` builds a deterministic catalogue of 2,010 products (10 hand-written showcase items
and 2,000 generated), plus demo users, orders, reviews, alerts, gift cards, tickets and audit
logs. `python seed.py` alone is a no-op when the catalogue is already populated.

## Configuration

Settings come from environment variables, with safe development defaults. Copy `.env.example`
to `.env` to override locally.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | Selects the config class: `development`, `testing`, `production`. |
| `SECRET_KEY` | dev value | Signing key. **Required** when `APP_ENV=production`. |
| `DATABASE_URL` | `sqlite:///instance/shop.db` | Any SQLAlchemy URL. |
| `COOKIE_SECURE` | `0` | Set to `1` when serving over HTTPS. |
| `FLASK_DEBUG` | `0` | Flask debug server and reloader. Never `1` in production. |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Dev bind address. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |

## Database and migrations

Development and tests create tables directly for convenience. Production schema is owned by
Alembic, and the application does not call `create_all` when `APP_ENV=production`.

```bash
flask --app wsgi db upgrade     # apply migrations (fresh database)
flask --app wsgi db migrate -m "message"   # autogenerate after a model change
flask --app wsgi db current     # show the applied revision
```

An existing database created before migrations were introduced is brought up to date once
with `flask --app wsgi db stamp head`.

## Running

Development:

```bash
python run.py
```

Production (waitress):

```bash
APP_ENV=production SECRET_KEY=... python wsgi.py
```

Both expose health endpoints, which are excluded from session and access-log noise:

- `GET /healthz` — liveness; returns `{"status": "ok"}`.
- `GET /readyz` — readiness; runs `SELECT 1` and returns 200 or 503.

## Docker

```bash
docker compose up --build
```

The image runs as a non-root user, applies migrations on start, and serves via waitress.
Provide a real `SECRET_KEY` through the environment; compose falls back to a placeholder.

## Tests and lint

The four suites are plain scripts; run them in order because they share a database.

```bash
python test_smoke.py      # 139 end-to-end checks
python test_seo.py        # 21 SEO checks (JSON-LD, canonical, sitemap)
python test_security.py   # 31 security checks (CSRF enabled)
python test_features.py   # 240 feature checks
```

```bash
ruff check .    # lint
ruff format .   # format
```

`make test`, `make lint` and the rest are available where GNU Make is installed.

## The assistant

`ai.py` is a self-contained intent and ranking engine. It tokenises and stems a question,
matches it against the catalogue with a small synonym table and price/rating/brand signals,
and composes an answer. It performs no network I/O and has no third-party dependency beyond
the standard library. It is exposed through `blueprints/assistant.py` at `/assistant/chat`,
`/assistant/status` and `/assistant/recommend`, and is rate-limited per client.

## Security

- CSRF protection via Flask-WTF; AJAX can send `X-CSRFToken`.
- Security headers on every response: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, a restrictive `Content-Security-Policy`, and HSTS over HTTPS. Each
  response also carries an `X-Request-ID`.
- Open redirects are blocked by `safe_next()`, which permits only same-site relative targets.
- Sessions are HTTPOnly and SameSite=Lax; `Secure` is set only when `COOKIE_SECURE=1`.
- Login attempts are rate-limited and accounts lock out after repeated failures.
- Jinja autoescaping is on; there is no raw SQL, `eval`, `exec` or `|safe` usage.

## Payments

Checkout is a simulation. No card is charged and no payment processor is integrated.

## Demo credentials

| Role | Email | Password |
|---|---|---|
| Shopper | `demo@indianstore.id` | `IndianStore2026` |
| Admin | `admin@indianstore.id` | `IndianStoreAdmin2026` |

Other seeded shoppers (`kenji@`, `priya@`, `sofia@`, `fan.*@indianstore.id`) share the
`IndianStore2026` password.
