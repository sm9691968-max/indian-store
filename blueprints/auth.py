import time
from urllib.parse import urljoin, urlparse

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    abort,
)
from sqlalchemy import func

from extensions import db
from models import EmailLog, User
from helpers import (
    audit,
    clear_login_failures,
    clear_totp_failures,
    current_user,
    login_locked_for,
    normalise_email,
    record_login_failure,
    record_totp_failure,
    safe_next,
    shopper_session,
    touch_login,
    totp_locked_for,
    valid_email,
    wants_json,
)
import totp

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("catalog.index"))

    next_url = safe_next(request.args.get("next"))

    if request.method == "POST":
        locked = login_locked_for()
        if locked:
            flash(
                f"Too many failed attempts. Try again in {locked // 60 + 1} minute(s).",
                "error",
            )
            return render_template(
                "auth/login.html", mode="signin", email=email_of_request(),
                next_url=next_url, locked=True
            ), 429

        email = normalise_email(request.form.get("email"))
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))
        next_url = safe_next(request.form.get("next"))

        if not valid_email(email):
            flash("Enter a valid email address.", "error")
            return render_template(
                "auth/login.html", mode="signin", email=email, next_url=next_url
            ), 400

        user = db.session.scalar(db.select(User).filter(func.lower(User.email) == email))

        if user is None or not user.check_password(password):
            locked_for = record_login_failure()
            if locked_for:
                flash("Too many failed attempts. This device is locked for 5 minutes.", "error")
                return (
                    render_template(
                        "auth/login.html", mode="signin", email=email,
                        next_url=next_url, locked=True
                    ),
                    429,
                )
            flash("Those credentials do not match an account.", "error")
            return render_template(
                "auth/login.html", mode="signin", email=email, next_url=next_url
            ), 401

        # Second factor before the session is ever created.
        if user.totp_enabled:
            session["totp_pending"] = user.id
            session["totp_pending_at"] = time.time()
            session["totp_pending_next"] = next_url
            session["totp_pending_remember"] = remember
            return redirect(url_for("auth.verify"))

        clear_login_failures()
        _establish_session(user, remember)
        flash(f"Welcome back, {user.full_name.split()[0]}.", "success")
        return redirect(next_url or url_for("catalog.index"))

    return render_template(
        "auth/login.html", mode="signin", email="", next_url=next_url
    )


def email_of_request() -> str:
    return normalise_email(request.form.get("email"))


TOTP_PENDING_TTL = 300


@auth_bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Second step of a two-factor sign-in.

    The password has already been checked; the session only carries a short-lived
    pointer to the half-authenticated user, never a logged-in one.
    """
    user_id = session.get("totp_pending")
    issued = session.get("totp_pending_at") or 0
    if not user_id or time.time() - issued > TOTP_PENDING_TTL:
        _clear_pending()
        flash("Your sign-in attempt expired. Please enter your password again.", "info")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_id)
    if user is None or not user.totp_enabled:
        _clear_pending()
        return redirect(url_for("auth.login"))

    next_url = session.get("totp_pending_next")
    remember = bool(session.get("totp_pending_remember"))

    if request.method == "POST":
        locked = totp_locked_for()
        if locked:
            flash(
                f"Too many failed codes. Try again in {locked // 60 + 1} minute(s).",
                "error",
            )
            return _render_totp(user, next_url, 429)

        code = (request.form.get("totp_code") or "").strip()
        ok = bool(code) and totp.verify(user.totp_secret, code)
        if not ok and code:
            used, remaining = totp.consume_recovery_code(user.recovery_codes, code)
            if used:
                user.recovery_codes = remaining
                db.session.commit()
                ok = True
        if not ok:
            locked_for = record_totp_failure()
            if locked_for:
                flash("Too many failed codes. This device is locked for 5 minutes.", "error")
                return _render_totp(user, next_url, 429)
            flash("That verification code is not valid.", "error")
            return _render_totp(user, next_url, 401)

        clear_totp_failures()
        clear_login_failures()
        _clear_pending()
        _establish_session(user, remember)
        flash(f"Welcome back, {user.full_name.split()[0]}.", "success")
        return redirect(next_url or url_for("catalog.index"))

    return _render_totp(user, next_url, 200)


def _render_totp(user: User, next_url: str | None, status: int):
    return (
        render_template(
            "auth/login.html",
            mode="signin",
            email=user.email,
            next_url=next_url,
            totp_stage=True,
        ),
        status,
    )


def _clear_pending() -> None:
    for key in ("totp_pending", "totp_pending_at", "totp_pending_next", "totp_pending_remember"):
        session.pop(key, None)


def _establish_session(user: User, remember: bool) -> None:
    # Sign in without discarding the basket: an anonymous shopper may have
    # already picked a currency, a coupon or comparison items.
    kept = shopper_session()
    session.clear()
    session.update(kept)
    session["user_id"] = user.id
    session.permanent = remember
    user.remember = remember
    user.failed_logins = 0
    user.locked_until = None
    touch_login(user)
    audit(user, "login", "User", user.id, f"remember={remember}")
    db.session.commit()


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("catalog.index"))

    next_url = safe_next(request.args.get("next"))

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = normalise_email(request.form.get("email"))
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        accept_terms = bool(request.form.get("accept_terms"))
        next_url = safe_next(request.form.get("next"))

        errors = []
        if len(full_name) < 2:
            errors.append("Please enter your full name.")
        if not valid_email(email):
            errors.append("Enter a valid email address.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        elif not any(c.isupper() for c in password) or not any(c.isdigit() for c in password):
            errors.append("Password needs at least one uppercase letter and one number.")
        if not confirm:
            errors.append("Please confirm your password.")
        elif password != confirm:
            errors.append("Passwords do not match.")
        if not accept_terms:
            errors.append("You must accept the terms to create an account.")

        if not errors:
            exists = db.session.scalar(db.select(User).filter(func.lower(User.email) == email))
            if exists:
                errors.append("An account with that email already exists.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template(
                "auth/login.html",
                mode="signup",
                email=email,
                full_name=full_name,
                next_url=next_url,
            ), 400

        user = User(full_name=full_name, email=email, remember=False, points=0)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        db.session.add(
            EmailLog(
                user_id=user.id,
                to_addr=email,
                subject="Welcome to Indian Store",
                body=(
                    f"Hi {full_name.split()[0]},\n\n"
                    "Your Indian Store account is live. You have 0 loyalty points so far — "
                    "you earn 1 point per dollar on every order.\n\n"
                    "— The Indian Store team"
                ),
                kind="welcome",
            )
        )
        db.session.commit()

        session.clear()
        session["user_id"] = user.id
        flash("Account created. Welcome to Indian Store.", "success")
        audit(user, "register", "User", user.id, user.email)
        db.session.commit()
        return redirect(next_url or url_for("catalog.index"))

    return render_template(
        "auth/login.html", mode="signup", email="", full_name="", next_url=next_url
    )


@auth_bp.post("/logout")
def logout():
    user = current_user()
    if user is not None:
        audit(user, "logout", "User", user.id)
        db.session.commit()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.get("/session/status")
def session_status():
    user = current_user()
    return jsonify(
        {
            "authenticated": user is not None,
            "name": user.full_name if user else None,
            "is_admin": bool(user and user.is_admin),
            "points": user.points if user else 0,
            "tier": user.loyalty_tier[0] if user else None,
        }
    )
