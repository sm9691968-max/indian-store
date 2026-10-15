# Architecture

This document describes how the application is put together and the conventions it follows.
It is meant to sit next to the source, not to replace it.

## Request lifecycle

1. `wsgi.py` (production) or `run.py` (development) calls `create_app()` from `app.py`, passing
   a config class resolved from `APP_ENV`.
2. `create_app` configures logging, initialises SQLAlchemy and Migrate, attaches CSRFProtect,
   and registers the blueprints.
3. Each request is tagged with a random `X-Request-ID` in `before_request`. Static and health
   paths skip session work so probes stay cheap.
4. `after_request` adds the security headers, an `X-Request-ID`, and writes one access-log line.
5. Errors are answered as JSON under `/assistant` and `/api` (and for `Accept:
   application/json`), and as HTML error pages otherwise.

## Layers

| Layer | Location | Responsibility |
|---|---|---|
| Entry points | `wsgi.py`, `run.py` | Choose a config, build the app, serve it. |
| Application factory | `app.py` | Wiring, context, filters, error handlers, headers, health. |
| Configuration | `config.py` | `Config` plus `Development`/`Testing`/`Production` subclasses. |
| Domain | `models.py` | SQLAlchemy models; every foreign key is indexed. |
| Business helpers | `helpers.py` | Pricing, cart totals, currency, session ids, decorators. |
| Assistant | `ai.py` | Intent parsing and product ranking; standard library only. |
| Blueprints | `blueprints/` | HTTP surface, grouped by concern. |
| Persistence | `extensions.py` | The shared `db` and `migrate` objects. |

Blueprints never import each other. They depend on `models`, `helpers`, `ai` and `extensions`,
which keeps the dependency graph acyclic and the modules independently testable.

## Configuration

`config.py` exposes `Config` and three subclasses. `create_app(config_class=Config)` keeps a
stable default so tests can call it with no arguments, while real entry points call
`get_config()`, which maps `APP_ENV` (or `FLASK_ENV`) to a class. `ProductionConfig` requires a
`SECRET_KEY` and marks the session cookie `Secure`; `create_app` refuses to start in production
without a real secret.

## Schema and migrations

Development and the test suites create tables with `db.create_all()` for speed. When
`APP_ENV=production` that call is skipped and Alembic owns the schema; the container runs
`flask --app wsgi db upgrade` before serving. Foreign keys carry explicit indexes because
SQLite — the default engine — does not index them automatically.

## The assistant

`ai.py` is deterministic and dependency-free. `extract_intent` turns a question into a budget,
a minimum rating, brand and category hints, and a topic; `rank_products` scores catalogue rows
against those signals; `compose_answer` and `summarize` render a reply. Because it performs no
I/O it is safe to call in a request handler and simple to test.

## Testing

The four suites are runnable scripts rather than pytest plugins, and share one SQLite database,
so they run sequentially. `test_smoke` and `test_features` drive the app end-to-end,
`test_security` runs with CSRF enabled and asserts the hardening headers and guards, and
`test_seo` checks the structured-data and canonical-URL surface. A green run means: 139 + 21 +
31 + 240 checks pass.

## Deployment

The `Dockerfile` installs pinned dependencies, runs as an unprivileged user, applies migrations,
and serves with waitress. `docker-compose.yml` mounts a named volume for the SQLite file and
sets `APP_ENV=production`. The health endpoints (`/healthz`, `/readyz`) are suitable for
orchestrator probes. Any SQLAlchemy URL can replace SQLite via `DATABASE_URL` for a managed
database.
