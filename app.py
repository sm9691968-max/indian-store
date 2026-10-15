import secrets
from datetime import datetime, timezone

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect

from config import _DEV_SECRET, Config
from extensions import db, migrate
from helpers import (
    CURRENCIES,
    SHIPPING_FLAT_RATE,
    currency_code,
    currency_symbol,
    current_user,
    free_delivery_label,
    live_activity_events,
    money_str,
    pending_alerts,
    safe_next,
    sold_last_24h,
    trending_searches,
    visitor_id,
    wants_json,
    wishlist_ids,
)
from logging_setup import configure_logging


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_class)

    configure_logging(app)
    db.init_app(app)
    migrate.init_app(app, db)
    csrf = CSRFProtect(app)

    from blueprints.account import account_bp
    from blueprints.admin import admin_bp
    from blueprints.assistant import assistant_bp
    from blueprints.auth import auth_bp
    from blueprints.cart import cart_bp
    from blueprints.catalog import catalog_bp
    from blueprints.health import health_bp
    from blueprints.seo import seo_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(cart_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(seo_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(health_bp)

    # The assistant chat endpoint is a read-only JSON "ask a question" call, so
    # it is CSRF-exempt like a public API and rate-limited server-side instead.
    csrf.exempt(assistant_bp)

    @app.before_request
    def assign_request_id():
        """Tag each request and give guests a stable id for live search."""
        g.request_id = secrets.token_hex(8)
        if request.path.startswith(("/healthz", "/readyz", "/static/")):
            return None
        visitor_id()
        return None

    @app.after_request
    def set_security_headers(response):
        """Baseline hardening headers for every response."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        csp = (
            "default-src 'self'; "
            "img-src 'self' https: data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        )
        csp += (
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "object-src 'none'"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers.setdefault("X-Request-ID", getattr(g, "request_id", ""))
        if not request.path.startswith("/static/"):
            app.logger.info(
                "%s %s -> %s [%s]",
                request.method,
                request.full_path.rstrip("?"),
                response.status_code,
                getattr(g, "request_id", "-"),
            )
        return response

    @app.context_processor
    def inject_globals():
        user = current_user()
        code = currency_code()
        return {
            "current_user": user,
            "cart_count": user.cart_count if user else 0,
            "saved_count": user.saved_cart_count if user else 0,
            "wish_count": len(wishlist_ids(user)),
            "alert_count": pending_alerts(user),
            "compare_count": len(session.get("compare", [])),
            "now_year": datetime.now(timezone.utc).year,
            "currencies": CURRENCIES,
            "currency": code,
            "currency_sym": currency_symbol(code),
            "free_delivery_label": free_delivery_label(),
            "shipping_flat": SHIPPING_FLAT_RATE,
            "live_events": live_activity_events(),
            "trending_searches": trending_searches(),
            "site_domain": request.host,
        }

    @app.post("/currency")
    def set_currency():
        """Switch display currency; prices stay in USD underneath."""
        code = (request.form.get("currency") or "USD").upper()
        if code not in CURRENCIES:
            code = "USD"
        session["currency"] = code
        return redirect(safe_next(request.referrer) or url_for("catalog.index"))

    @app.template_filter("money")
    def money(value):
        """Render a USD amount in the shopper's selected currency."""
        try:
            return money_str(value)
        except (TypeError, ValueError):
            return f"{currency_symbol()}0.00"

    @app.template_filter("usd")
    def usd(value):
        """Render the untouched base-currency figure, for admin and invoices."""
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):
            return "$0.00"

    @app.template_filter("ccy")
    def ccy(value, code):
        """Render a base-USD amount in a specific currency, for invoices."""
        try:
            return money_str(value, code)
        except (TypeError, ValueError):
            return money_str(0, code)

    @app.template_filter("stars")
    def stars(value):
        try:
            score = float(value)
        except (TypeError, ValueError):
            return "&#9734;" * 5
        out = []
        for i in range(1, 6):
            out.append("&#9733;" if score >= i - 0.25 else "&#9734;")
        return "".join(out)

    @app.template_filter("pct_of")
    def pct_of(part, whole):
        try:
            if not whole:
                return 0
            return int(round((float(part) / float(whole)) * 100))
        except (TypeError, ValueError, ZeroDivisionError):
            return 0

    @app.template_filter("ts")
    def ts(value):
        """Relative time: 3h ago, 2d ago."""
        if not value:
            return ""
        if isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value, tz=timezone.utc)
        delta = datetime.now(timezone.utc) - value.replace(tzinfo=timezone.utc)
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        if seconds < 604800:
            return f"{seconds // 86400}d ago"
        return value.strftime("%d %b %Y")

    # Exposed to templates so product cards can show real 24h demand without
    # each view having to pre-compute a mapping.
    app.jinja_env.globals["sold_last_24h"] = sold_last_24h

    def _wants_json_error() -> bool:
        return wants_json() or request.path.startswith(("/assistant", "/api"))

    @app.errorhandler(403)
    def forbidden(_):
        if _wants_json_error():
            return jsonify({"error": "forbidden"}), 403
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_):
        if _wants_json_error():
            return jsonify({"error": "not_found"}), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_):
        if _wants_json_error():
            return jsonify({"error": "internal_server_error"}), 500
        return render_template("errors/500.html"), 500

    if app.config.get("ENV") != "production":
        with app.app_context():
            db.create_all()

    secret = app.config.get("SECRET_KEY")
    if app.config.get("ENV") == "production" and (not secret or secret == _DEV_SECRET):
        raise RuntimeError(
            "SECRET_KEY must be set to a strong value when APP_ENV=production."
        )
    if secret == _DEV_SECRET:
        app.logger.warning(
            "SECRET_KEY is still the development default. Set the SECRET_KEY "
            "environment variable before serving this anywhere public."
        )

    return app
