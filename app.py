from flask import Flask, render_template, request, session, redirect, url_for
from flask_wtf.csrf import CSRFProtect

from config import Config, _DEV_SECRET
from extensions import db
from helpers import (
    CURRENCIES,
    current_user,
    convert,
    currency_code,
    currency_symbol,
    pending_alerts,
    safe_next,
    visitor_id,
    wishlist_ids,
)


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_class)

    db.init_app(app)
    csrf = CSRFProtect(app)

    from blueprints.auth import auth_bp
    from blueprints.catalog import catalog_bp
    from blueprints.cart import cart_bp
    from blueprints.account import account_bp
    from blueprints.admin import admin_bp
    from blueprints.seo import seo_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(cart_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(seo_bp)

    @app.before_request
    def ensure_visitor_id():
        """Every guest gets a stable id for recently-viewed and live search."""
        visitor_id()
        return None

    @app.after_request
    def set_security_headers(response):
        """Baseline hardening headers for every response."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' https: data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "object-src 'none'",
        )
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
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
            "now_year": 2026,
            "currencies": CURRENCIES,
            "currency": code,
            "currency_sym": currency_symbol(code),
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
            return f"{currency_symbol()}{convert(value):,.2f}"
        except (TypeError, ValueError):
            return f"{currency_symbol()}0.00"

    @app.template_filter("usd")
    def usd(value):
        """Render the untouched base-currency figure, for admin and invoices."""
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):
            return "$0.00"

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
        from datetime import datetime, timezone

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

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_):
        return render_template("errors/500.html"), 500

    with app.app_context():
        db.create_all()

    if not app.debug and app.config["SECRET_KEY"] == _DEV_SECRET:
        app.logger.warning(
            "SECRET_KEY is still the development default. Set the SECRET_KEY "
            "environment variable before serving this anywhere public."
        )

    return app
