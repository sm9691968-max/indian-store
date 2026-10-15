import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import totp
from extensions import db
from helpers import auth_fingerprint, current_user, login_required, loyalty_summary
from models import (
    Address,
    GiftCard,
    LoyaltyEvent,
    Order,
    Review,
    StockAlert,
    SupportTicket,
    TicketMessage,
    User,
    WishlistItem,
    utcnow,
)

account_bp = Blueprint("account", __name__, url_prefix="/account")

ADDRESS_FIELDS = ("label", "full_name", "line1", "line2", "city", "postal", "country", "phone")


@account_bp.get("/")
@login_required
def overview():
    user = current_user()
    orders = db.session.scalars(
        db.select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()).limit(4)
    ).all()

    stats = {
        "orders": db.session.scalar(
            db.select(func.count()).select_from(Order).where(Order.user_id == user.id)
        ),
        "spent": user.total_spent,
        "wishlist": db.session.scalar(
            db.select(func.count())
            .select_from(WishlistItem)
            .where(WishlistItem.user_id == user.id)
        ),
        "reviews": db.session.scalar(
            db.select(func.count()).select_from(Review).where(Review.user_id == user.id)
        ),
    }

    return render_template(
        "account/overview.html",
        user=user,
        orders=orders,
        stats=stats,
        wish_count=stats["wishlist"],
        loyalty=loyalty_summary(user),
    )


@account_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()[:32]
        marketing = bool(request.form.get("marketing_opt_in"))
        new_email = (request.form.get("email") or "").strip().lower()

        errors = []
        if len(full_name) < 2:
            errors.append("Please enter your full name.")
        if not ("@" in new_email and "." in new_email.split("@")[-1]):
            errors.append("Enter a valid email address.")

        if not errors and new_email != user.email:
            taken = db.session.scalar(
                db.select(User).filter(
                    func.lower(User.email) == new_email, User.id != user.id
                )
            )
            if taken:
                errors.append("That email is already registered to another account.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("account/profile.html", user=user), 400

        user.full_name = full_name
        user.phone = phone
        user.email = new_email
        user.marketing_opt_in = marketing
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("account.profile"))

    return render_template("account/profile.html", user=user)


@account_bp.route("/security", methods=["GET", "POST"])
@login_required
def security():
    user = current_user()

    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        errors = []
        if not user.check_password(current):
            errors.append("Your current password is incorrect.")
        if len(new) < 8:
            errors.append("New password must be at least 8 characters.")
        elif not any(c.isupper() for c in new) or not any(c.isdigit() for c in new):
            errors.append("New password needs an uppercase letter and a number.")
        if new != confirm:
            errors.append("Passwords do not match.")
        if new and new == current:
            errors.append("Choose a password different from your current one.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("account/security.html", user=user), 400

        user.set_password(new)
        user.remember = True
        db.session.commit()
        session.clear()
        session["user_id"] = user.id
        session["auth_fp"] = auth_fingerprint(user)
        session.permanent = True
        flash("Password changed — other sessions were signed out.", "success")
        return redirect(url_for("account.security"))

    return render_template("account/security.html", user=user)


@account_bp.route("/addresses", methods=["GET", "POST"])
@login_required
def addresses():
    user = current_user()

    if request.method == "POST":
        data = {f: (request.form.get(f) or "").strip() for f in ADDRESS_FIELDS}
        errors = []
        if not data["label"]:
            data["label"] = "Address"
        for field, label in (("full_name", "Recipient"), ("line1", "Street address"),
                             ("city", "City"), ("postal", "Postal code")):
            if not data[field]:
                errors.append(f"{label} is required.")
        if not data["country"]:
            data["country"] = "United States"

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("account/addresses.html", user=user), 400

        wants_default = bool(request.form.get("is_default"))
        if wants_default or not user.addresses:
            for addr in user.addresses:
                addr.is_default = False

        db.session.add(Address(user_id=user.id, is_default=wants_default, **data))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Could not save that address. Check the fields and try again.", "error")
            return redirect(url_for("account.addresses"))
        flash("Address saved.", "success")
        return redirect(url_for("account.addresses"))

    return render_template("account/addresses.html", user=user)


@account_bp.post("/addresses/<int:address_id>/delete")
@login_required
def delete_address(address_id):
    user = current_user()
    address = db.session.get(Address, address_id)
    if address is None or address.user_id != user.id:
        abort(404)

    was_default = address.is_default
    db.session.delete(address)
    db.session.commit()

    if was_default:
        remaining = db.session.scalars(
            db.select(Address).where(Address.user_id == user.id).limit(1)
        ).first()
        if remaining:
            remaining.is_default = True
            db.session.commit()

    flash("Address removed.", "info")
    return redirect(url_for("account.addresses"))


@account_bp.post("/addresses/<int:address_id>/default")
@login_required
def make_default_address(address_id):
    user = current_user()
    address = db.session.get(Address, address_id)
    if address is None or address.user_id != user.id:
        abort(404)

    for addr in user.addresses:
        addr.is_default = addr.id == address_id
    db.session.commit()
    flash("Default delivery address updated.", "success")
    return redirect(url_for("account.addresses"))


@account_bp.get("/activity")
@login_required
def activity():
    user = current_user()

    events = db.session.execute(
        db.select(Order.reference, Order.status, Order.total, Order.created_at)
        .where(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
    ).all()

    reviews = db.session.scalars(
        db.select(Review).where(Review.user_id == user.id).order_by(Review.created_at.desc())
    ).all()

    return render_template(
        "account/activity.html", user=user, order_events=events, reviews=reviews
    )


@account_bp.get("/loyalty")
@login_required
def loyalty():
    user = current_user()
    events = db.session.scalars(
        db.select(LoyaltyEvent)
        .where(LoyaltyEvent.user_id == user.id)
        .order_by(LoyaltyEvent.created_at.desc())
    ).all()
    return render_template(
        "account/loyalty.html", user=user, events=events, summary=loyalty_summary(user)
    )


@account_bp.route("/2fa", methods=["GET", "POST"])
@login_required
def two_factor():
    """Enrol, confirm or disable TOTP. The secret lives in the session until proven."""
    user = current_user()

    if request.method == "POST":
        action = request.form.get("action", "enable")

        if action == "enable":
            secret = (request.form.get("secret") or "").strip() or session.get("totp_enrol_secret")
            if not secret:
                flash("Start the enrolment again.", "error")
                return redirect(url_for("account.two_factor"))

            code = (request.form.get("code") or "").strip()
            if not totp.verify(secret, code):
                flash("That code did not match. Check your authenticator app and retry.", "error")
                session["totp_enrol_secret"] = secret
                return render_template(
                    "account/two_factor.html",
                    user=user,
                    secret=secret,
                    uri=totp.otpauth_uri(secret, user.email),
                ), 400

            user.totp_secret = secret
            user.totp_enabled = True
            user.totp_enrolled_at = utcnow()
            user.recovery_codes = totp.store_recovery_codes(totp.new_recovery_codes())
            session.pop("totp_enrol_secret", None)
            db.session.commit()
            flash("Two-factor authentication is now active on your account.", "success")
            return redirect(url_for("account.two_factor"))

        # Disabling 2FA or reissuing recovery codes both hand over the account, so
        # they need the password even though the session is already authenticated.
        if action in ("disable", "regenerate"):
            password = request.form.get("password") or ""
            if not password:
                flash("Enter your password to confirm this change.", "error")
                return redirect(url_for("account.two_factor"))
            if not user.check_password(password):
                flash("Password incorrect — nothing was changed.", "error")
                return redirect(url_for("account.two_factor"))

        if action == "disable":
            user.totp_enabled = False
            user.totp_secret = ""
            user.totp_enrolled_at = None
            user.recovery_codes = ""
            db.session.commit()
            flash("Two-factor authentication disabled.", "info")
            return redirect(url_for("account.two_factor"))

        if action == "regenerate":
            user.recovery_codes = totp.store_recovery_codes(totp.new_recovery_codes())
            db.session.commit()
            flash("New recovery codes generated.", "success")
            return redirect(url_for("account.two_factor"))

    if not user.totp_enabled:
        secret = session.get("totp_enrol_secret") or totp.generate_secret()
        session["totp_enrol_secret"] = secret
        return render_template(
            "account/two_factor.html",
            user=user,
            secret=secret,
            uri=totp.otpauth_uri(secret, user.email),
        )

    return render_template("account/two_factor.html", user=user)


@account_bp.get("/alerts")
@login_required
def alerts():
    user = current_user()
    rows = db.session.scalars(
        db.select(StockAlert)
        .where(StockAlert.user_id == user.id)
        .order_by(StockAlert.created_at.desc())
    ).all()
    return render_template(
        "account/alerts.html", user=user, alerts=[a for a in rows if a.product]
    )


@account_bp.get("/gift-cards")
@login_required
def gift_cards():
    user = current_user()
    cards = db.session.scalars(
        db.select(GiftCard).where(GiftCard.user_id == user.id).order_by(GiftCard.created_at.desc())
    ).all()
    return render_template("account/gift_cards.html", user=user, cards=cards)


@account_bp.get("/support")
@login_required
def support():
    user = current_user()
    tickets = db.session.scalars(
        db.select(SupportTicket)
        .where(SupportTicket.user_id == user.id)
        .order_by(SupportTicket.updated_at.desc())
    ).all()
    return render_template(
        "account/support.html", user=user, tickets=tickets, orders=user.orders[:10]
    )


@account_bp.post("/support/new")
@login_required
def create_ticket():
    user = current_user()
    subject = (request.form.get("subject") or "").strip()
    category = (request.form.get("category") or "Order").strip()[:32]
    message = (request.form.get("message") or "").strip()
    order_id = request.form.get("order_id", type=int)

    if len(subject) < 4 or len(message) < 10:
        flash("Add a subject and a message of at least 10 characters.", "error")
        return redirect(url_for("account.support"))

    ticket = SupportTicket(
        user_id=user.id,
        reference=f"TCK-{secrets.token_hex(4).upper()}",
        subject=subject[:180],
        category=category,
        order_id=order_id if order_id and order_id in {o.id for o in user.orders} else None,
        status="open",
        priority="normal",
    )
    db.session.add(ticket)
    db.session.flush()
    db.session.add(
        TicketMessage(
            ticket_id=ticket.id, author_id=user.id, is_staff=False, body=message[:4000]
        )
    )
    db.session.commit()
    flash(f"Ticket {ticket.reference} opened. We reply within one business day.", "success")
    return redirect(url_for("account.ticket_detail", reference=ticket.reference))


@account_bp.get("/support/<reference>")
@login_required
def ticket_detail(reference):
    user = current_user()
    ticket = db.session.scalar(db.select(SupportTicket).filter_by(reference=reference))
    if ticket is None:
        abort(404)
    if ticket.user_id != user.id:
        # 404 rather than 403: a 403 would confirm that the reference exists.
        # This matches the reply and close routes below.
        abort(404)
    return render_template("account/ticket.html", user=user, ticket=ticket)


@account_bp.post("/support/<int:ticket_id>/reply")
@login_required
def reply_ticket(ticket_id):
    user = current_user()
    ticket = db.session.get(SupportTicket, ticket_id)
    if ticket is None or ticket.user_id != user.id:
        abort(404)

    body = (request.form.get("message") or "").strip()
    if len(body) < 2:
        flash("Write a message before sending.", "error")
        return redirect(url_for("account.ticket_detail", reference=ticket.reference))

    db.session.add(
        TicketMessage(
            ticket_id=ticket.id, author_id=user.id, is_staff=False, body=body[:4000]
        )
    )
    ticket.status = "open"
    ticket.updated_at = utcnow()
    db.session.commit()
    flash("Reply sent to the Indian Store support team.", "success")
    return redirect(url_for("account.ticket_detail", reference=ticket.reference))


@account_bp.post("/support/<int:ticket_id>/close")
@login_required
def close_ticket(ticket_id):
    user = current_user()
    ticket = db.session.get(SupportTicket, ticket_id)
    if ticket is None or ticket.user_id != user.id:
        abort(404)
    ticket.status = "closed"
    ticket.updated_at = utcnow()
    db.session.commit()
    flash("Ticket closed. You can reopen it by replying.", "info")
    return redirect(url_for("account.ticket_detail", reference=ticket.reference))
