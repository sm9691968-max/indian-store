# Indian Store

A full-featured Flask e-commerce demo (Flask 3.0.3, SQLAlchemy). The data model, admin tooling, carts, loyalty, gift cards, support tickets, reviews, Q&A, price alerts and analytics are all included.

## Features
- Auth with TOTP 2FA, account lockouts and recovery codes
- Catalogue with filters (category, brand, price, stock, rating), search suggestions, pagination, recently viewed and product comparisons
- Shopping cart, saved carts, coupons, gift cards and checkout with order tracking
- Wishlist and shared wishlist pages
- Reviews, product questions/answers and price/stock alerts
- Admin dashboard: orders/fulfilment, products, reviews/questions, users, tickets, coupons, gift cards, audit logs and analytics
- JSON-LD, sitemap, robots and a small SEO surface

## Setup
```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

## Seed the demo database
The app will create `instance/shop.db` on first run, but the fastest way to get demo data is to seed it:

```bash
python seed.py        # seed only if the catalogue is empty
python seed.py --reset  # drop all tables and reseed from scratch
```

Seeding produces **2,010 products** total (10 hand-written showcase items and 2,000 deterministically generated), plus demo users, orders, reviews, alerts, gift cards, tickets and audit logs.

## Run the app
```bash
python run.py
```

The server listens on http://127.0.0.1:5000. Debug mode is opt-in via `FLASK_DEBUG=1`.

## Security
- CSRF protection is enabled (Flask-WTF). All POST forms carry `csrf_token()` and AJAX calls can use the `X-CSRFToken` header.
- Security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, CSP (`frame-ancestors 'none'`, `object-src 'none'`, `form-action 'self'`) and HSTS only over HTTPS.
- Open redirects are blocked via a `safe_next()` helper that only permits same-site relative targets.
- Sessions: HTTPOnly, SameSite=Lax and `Secure` only when `COOKIE_SECURE=1` (HTTPS).
- `SECRET_KEY` defaults to a loud dev value — set `SECRET_KEY` in the environment before deploying.
- Input is escaped (Jinja autoescape). No raw SQL, `eval`, `exec`, or `|safe` usage.
- Login rate-limiting and account lockouts are enforced (see `blueprints/auth.py`).

## Tests
Four test suites are provided. All pass with the seeded catalogue.

```bash
python test_features.py   # 240 end-to-end feature checks
python test_smoke.py      # 139 smoke/regression checks
python test_security.py   # 31 security checks (CSRF enabled)
python test_seo.py        # 21 SEO checks (JSON-LD, canonical, sitemap)
```

## SEO
- `/sitemap.xml` lists all 2,018 indexable URLs with `lastmod`, plus `/robots.txt` pointing at it.
- Product pages emit schema.org `Product` JSON-LD with price, currency, availability and ratings.
- Filter and sort query parameters are dropped from `<link rel="canonical">`, so the
  permutations of one category collapse to a single URL instead of competing with each other.
- Page 1 is canonical to the bare listing; pages 2+ keep `?page=`.

## Payments
Checkout is a simulation. No card is ever charged and no payment processor is wired up.

## Demo credentials
| Role | Email | Password |
|---|---|---|
| Shopper | `demo@indianstore.id` | `IndianStore2026` |
| Admin | `admin@indianstore.id` | `IndianStoreAdmin2026` |

The app also includes a handful of other seeded shoppers (`kenji@indianstore.id`, `priya@indianstore.id`, `sofia@indianstore.id` and `fan.*@indianstore.id`) — all use the same password `IndianStore2026`.
