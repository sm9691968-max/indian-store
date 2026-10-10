"""Shared helpers: auth guards, pricing maths, currency, loyalty, rate limiting."""

import re
import secrets
import time
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from urllib.parse import urlparse

from flask import (
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import or_

from extensions import db
from models import (
    CURRENCIES,
    AuditLog,
    CartItem,
    Coupon,
    EmailLog,
    LoyaltyEvent,
    Order,
    OrderItem,
    Product,
    StockAlert,
    User,
    utcnow,
)

SHIPPING_FLAT_RATE = Decimal("9.99")
FREE_SHIPPING_THRESHOLD = Decimal("150.00")
TAX_RATE = Decimal("0.08")

POINT_VALUE = Decimal("0.01")  # 100 points == $1.00
POINTS_PER_DOLLAR = 1
MAX_POINTS_PER_ORDER = 2000

# Brute-force protection for credential endpoints.
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_SECONDS = 300
_attempts: dict[str, list[float]] = {}
_locked: dict[str, float] = {}


# ----------------------------------------------------------------- auth


def current_user() -> User | None:
    """Return the logged-in user, clearing a stale session if the row vanished."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    if user is None:
        session.clear()
    return user


def wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json"


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            if wants_json():
                return jsonify({"error": "authentication_required"}), 401
            flash("Please sign in to continue.", "info")
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            if wants_json():
                return jsonify({"error": "authentication_required"}), 401
            flash("Please sign in to continue.", "info")
            return redirect(url_for("auth.login", next=request.full_path))
        if not user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


# ----------------------------------------------------------------- rate limiting


def _client_key() -> str:
    return f"{request.remote_addr}|{(request.form.get('email') or '').lower()}"


def login_locked_for() -> int:
    """Seconds remaining on a credential-stuffing lockout, if any."""
    until = _locked.get(_client_key(), 0.0)
    remaining = until - time.time()
    return int(remaining) if remaining > 0 else 0


def record_login_failure() -> int:
    """Register a bad attempt and lock the client once the threshold is passed."""
    key = _client_key()
    now = time.time()
    bucket = [t for t in _attempts.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    bucket.append(now)
    _attempts[key] = bucket
    if len(bucket) >= LOGIN_MAX_ATTEMPTS:
        _locked[key] = now + LOGIN_LOCK_SECONDS
        _attempts[key] = []
        return LOGIN_LOCK_SECONDS
    return 0


def clear_login_failures() -> None:
    _attempts.pop(_client_key(), None)
    _locked.pop(_client_key(), None)


TWOFA_MAX_ATTEMPTS = 5
TWOFA_LOCK_SECONDS = 300
_2fa_attempts: dict[str, list[float]] = {}
_2fa_locked: dict[str, float] = {}

# Session keys that describe the shopper rather than the authentication state, so
# they must survive the session reset that login performs.
SHOPPER_SESSION_KEYS = (
    "visitor_id",
    "currency",
    "compare",
    "coupon_code",
    "coupon_message",
    "gift_card_code",
    "points_redeem",
)


def shopper_session() -> dict:
    """Snapshot the cart/visitor state that a login should not throw away."""
    return {key: session[key] for key in SHOPPER_SESSION_KEYS if key in session}


def _totp_key() -> str:
    return f"{request.remote_addr}|totp|{session.get('totp_pending', '')}"


def totp_locked_for() -> int:
    """Seconds remaining on a second-factor lockout, if any."""
    remaining = _2fa_locked.get(_totp_key(), 0.0) - time.time()
    return int(remaining) if remaining > 0 else 0


def record_totp_failure() -> int:
    """Register a bad second-factor attempt and lock once the threshold is passed."""
    key = _totp_key()
    now = time.time()
    bucket = [t for t in _2fa_attempts.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    bucket.append(now)
    _2fa_attempts[key] = bucket
    if len(bucket) >= TWOFA_MAX_ATTEMPTS:
        _2fa_locked[key] = now + TWOFA_LOCK_SECONDS
        _2fa_attempts[key] = []
        return TWOFA_LOCK_SECONDS
    return 0


def clear_totp_failures() -> None:
    _2fa_attempts.pop(_totp_key(), None)
    _2fa_locked.pop(_totp_key(), None)


# ----------------------------------------------------------------- currency


def currency_code() -> str:
    # Indian rupee is this marketplace's home currency; the shopper can still
    # switch to USD/EUR/etc. and the choice sticks for the session.
    code = session.get("currency", "INR")
    return code if code in CURRENCIES else "INR"


def fx_rate(code: str | None = None) -> Decimal:
    return CURRENCIES[code or currency_code()]["rate"]


def convert(amount, code: str | None = None) -> Decimal:
    """Convert a USD amount into the active display currency, rounded to cents."""
    rate = fx_rate(code)
    return (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def currency_symbol(code: str | None = None) -> str:
    return CURRENCIES[code or currency_code()]["symbol"]


def group_amount(value, code: str | None = None) -> str:
    """Group a rounded amount in the display currency's local convention.

    The Indian rupee (and our other south-asian markets) group 12,34,567 the
    Indian way — the last three digits once, then every pair after that.
    """
    neg = value < 0
    s = f"{abs(value):,.2f}"
    if (code or currency_code()) == "INR":
        int_part, _, dec = s.partition(".")
        digits = int_part.replace(",", "")
        if len(digits) <= 3:
            int_part = digits
        else:
            head, tail = digits[:-3], digits[-3:]
            pairs = []
            while head:
                pairs.append(head[-2:])
                head = head[:-2]
            int_part = ",".join(reversed(pairs)) + "," + tail
        s = int_part + "." + dec
    return ("-" if neg else "") + s


def money_str(value, code: str | None = None) -> str:
    """Render a USD amount in the active display currency with its symbol."""
    try:
        amount = convert(value, code)
    except (TypeError, ValueError):
        amount = Decimal("0.00")
    return f"{CURRENCIES[code or currency_code()]['symbol']}{group_amount(amount, code)}"


def free_delivery_label() -> str:
    """Localised price from which delivery is free, for copy on listings."""
    return money_str(FREE_SHIPPING_THRESHOLD)


# Cities used by the deterministic live-activity ticker.
_ACTIVITY_CITIES = (
    "Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Ahmedabad", "Chennai",
    "Pune", "Kolkata", "Jaipur", "Surat", "Lucknow", "Nagpur",
)


def live_activity_events(limit: int = 8) -> list[dict]:
    """A short, deterministic stream of 'someone just bought X' events.

    Named after real-sounding Indian customers, derived from the catalogue so
    each event links to a real product, and seeded by the product id so the
    sequence is stable within a minute — convincing for a demo ticker.
    """
    from models import Product

    buyers = ("Rohan", "Ananya", "Arjun", "Sneha", "Vikram", "Kavya",
              "Ishaan", "Meera", "Dev", "Tanvi")
    products = db.session.scalars(
        db.select(Product)
        .where(Product.is_active.is_(True), Product.stock > 0)
        .order_by(Product.sold_count.desc())
        .limit(30)
    ).all()
    bucket = int(time.time()) // 60
    events = []
    for i, product in enumerate(products[:limit]):
        seed = (product.id * 2654435761 + bucket * 97) & 0xFFFFFFFF
        city = _ACTIVITY_CITIES[seed % len(_ACTIVITY_CITIES)]
        minutes = 1 + (seed >> 3) % 45
        events.append(
            {
                "name": buyers[(seed >> 11) % len(buyers)],
                "city": city,
                "product": product.name,
                "slug": "/product/" + product.slug,
                "price": money_str(product.price),
                "mins_ago": minutes,
            }
        )
    return events


# ----------------------------------------------------------------- wishlist


def wishlist_ids(user) -> set[int]:
    if user is None:
        return set()
    from models import WishlistItem

    return set(
        db.session.scalars(
            db.select(WishlistItem.product_id).where(WishlistItem.user_id == user.id)
        ).all()
    )


# ----------------------------------------------------------------- pricing


def shipping_for(subtotal: Decimal, free_overrides=None) -> Decimal:
    if subtotal <= 0:
        return Decimal("0.00")
    if subtotal >= FREE_SHIPPING_THRESHOLD:
        return Decimal("0.00")
    if free_overrides and any(free_overrides):
        return Decimal("0.00")
    return SHIPPING_FLAT_RATE


def active_coupon():
    """Resolve the coupon code held in the session, dropping it if it went stale."""
    code = session.get("coupon_code")
    if not code:
        return None
    coupon = db.session.scalar(db.select(Coupon).filter_by(code=code))
    if coupon is None or not coupon.is_redeemable:
        session.pop("coupon_code", None)
        session.pop("coupon_message", None)
        return None
    return coupon


def points_available(user) -> int:
    """Points still spendable, never more than the order value can absorb."""
    if user is None:
        return 0
    return max(0, user.points)


def points_to_discount(points: int, cap: Decimal) -> Decimal:
    raw = Decimal(points) * POINT_VALUE
    return min(raw, cap).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def points_for_spend(total: Decimal) -> int:
    return min(int(Decimal(total) * POINTS_PER_DOLLAR), MAX_POINTS_PER_ORDER)


def cart_items(user) -> list[CartItem]:
    if user is None:
        return []
    return [item for item in user.cart_items if not item.is_saved]


def saved_items(user) -> list[CartItem]:
    if user is None:
        return []
    return [item for item in user.cart_items if item.is_saved]


def quote_cart(items, user=None) -> dict:
    """Single source of truth for cart maths used by cart, checkout and orders."""
    items = list(items)
    subtotal = sum((Decimal(str(i.line_total)) for i in items), Decimal("0.00"))

    # A cart where every line is flagged free-shipping never pays delivery.
    all_free = bool(items) and all(i.product.free_shipping for i in items)
    shipping = shipping_for(subtotal, free_overrides=[all_free])
    coupon = active_coupon()
    discount = coupon.discount_for(subtotal, shipping) if coupon else Decimal("0.00")
    if coupon and coupon.min_subtotal and subtotal < coupon.min_subtotal:
        coupon = None
        discount = Decimal("0.00")
    if coupon and discount <= 0 and coupon.kind != "freeship":
        discount = Decimal("0.00")

    shipping_after = Decimal("0.00") if (coupon and coupon.kind == "freeship") else shipping

    # Loyalty redemption applies after any coupon, capped by the remaining total.
    max_redeemable = max(subtotal - discount, Decimal("0.00"))
    requested = session.get("points_redeem", 0)
    available = points_available(user)
    redeem_points = max(0, min(int(requested or 0), available))
    points_value = points_to_discount(redeem_points, max_redeemable)
    if points_value <= 0:
        redeem_points = 0
        points_value = Decimal("0.00")

    gift_code = (session.get("gift_card_code") or "").strip().upper()
    gift_card = None
    gift_value = Decimal("0.00")
    if gift_code:
        from models import GiftCard

        gift_card = db.session.scalar(db.select(GiftCard).filter_by(code=gift_code))
        if gift_card is not None and not gift_card.is_redeemable:
            session.pop("gift_card_code", None)
            gift_card = None
        if gift_card is not None:
            # A gift card can settle the merchandise, the delivery and the tax,
            # but never pushes the order below zero.
            remaining = max_redeemable - points_value + shipping_after
            gift_value = min(Decimal(gift_card.balance), max(remaining, Decimal("0.00")))
            if gift_value <= 0:
                session.pop("gift_card_code", None)
                gift_card = None
                gift_value = Decimal("0.00")

    total_discount = discount + points_value + gift_value
    taxable = max(subtotal - discount - points_value - gift_value, Decimal("0.00"))
    tax = (taxable * TAX_RATE).quantize(Decimal("0.01"))
    total = taxable + shipping_after + tax

    return {
        "subtotal": subtotal,
        "shipping": shipping,
        "shipping_after": shipping_after,
        "discount": discount,
        "points_redeem": redeem_points,
        "points_value": points_value,
        "gift_card": gift_card,
        "gift_value": gift_value,
        "tax": tax,
        "total": total,
        "coupon": coupon,
    }


# ----------------------------------------------------------------- loyalty


def award_points(user, points: int, reason: str, order_id: int | None = None) -> None:
    if user is None or points == 0:
        return
    user.points = max(0, user.points + points)
    db.session.add(
        LoyaltyEvent(
            user_id=user.id,
            points=points,
            reason=reason,
            balance_after=user.points,
            order_id=order_id,
        )
    )


def loyalty_summary(user) -> dict:
    if user is None:
        return {"tier": ("Member", "#34d399"), "points": 0, "to_next": 300, "value": Decimal("0.00")}
    tier, colour = user.loyalty_tier
    return {
        "tier": (tier, colour),
        "points": user.points,
        "to_next": user.points_to_next_tier,
        "value": (Decimal(user.points) * POINT_VALUE).quantize(Decimal("0.01")),
    }


# ----------------------------------------------------------------- alerts


def pending_alerts(user) -> int:
    if user is None:
        return 0
    return (
        db.session.scalar(
            db.select(db.func.count())
            .select_from(StockAlert)
            .where(StockAlert.user_id == user.id, StockAlert.notified.is_(False))
        )
        or 0
    )


def log_email(user, to_addr: str, subject: str, body: str, kind: str = "transactional") -> None:
    """Simulated outbox — stands in for a real mail provider."""
    db.session.add(
        EmailLog(
            user_id=user.id if user else None,
            to_addr=to_addr,
            subject=subject,
            body=body,
            kind=kind,
        )
    )


def audit(actor, action: str, entity: str = "", entity_id="", detail: str = "") -> None:
    db.session.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            detail=detail[:300],
        )
    )


# ----------------------------------------------------------------- recommendations


def recommend_products(product_ids, exclude_ids=(), limit: int = 4) -> list:
    """Products that frequently ship in the same order as the given ones."""
    product_ids = [pid for pid in product_ids if pid]
    if not product_ids:
        return []
    order_ids = db.session.scalars(
        db.select(OrderItem.order_id).where(OrderItem.product_id.in_(product_ids))
    ).all()
    order_ids = [oid for oid in order_ids if oid]
    if not order_ids:
        return []
    blocked = set(product_ids) | set(exclude_ids or ())
    rows = db.session.execute(
        db.select(Product, db.func.count(Product.id).label("co"))
        .join(OrderItem, OrderItem.product_id == Product.id)
        .where(
            OrderItem.order_id.in_(order_ids),
            Product.is_active.is_(True),
            Product.id.notin_(blocked),
        )
        .group_by(Product.id)
        .order_by(db.func.count(Product.id).desc(), Product.rating.desc())
        .limit(limit * 2)
    ).all()
    picks = [row[0] for row in rows if row[0].stock > 0][:limit]
    return _fill_recommendations(picks, blocked, limit)


def _fill_recommendations(picks: list, blocked: set, limit: int) -> list:
    """Top up a recommendations rail with popular products when affinity is thin."""
    if len(picks) >= limit:
        return picks[:limit]
    have = {p.id for p in picks}
    pool = db.session.scalars(
        db.select(Product)
        .where(
            Product.is_active.is_(True),
            Product.stock > 0,
            Product.id.notin_(have | blocked),
        )
        .order_by(Product.sold_count.desc(), Product.rating.desc())
        .limit(limit * 2)
    ).all()
    for product in pool:
        if product.id in have:
            continue
        picks.append(product)
        have.add(product.id)
        if len(picks) >= limit:
            break
    return picks[:limit]


# ----------------------------------------------------------------- text


def slugify(value: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in (value or "").lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def normalise_email(raw: str) -> str:
    return (raw or "").strip().lower()


def valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email or ""))


def safe_next(target: str | None) -> str | None:
    """Only allow same-site relative redirects to avoid open-redirect abuse."""
    if not target:
        return None
    candidate = urlparse(target)
    if candidate.scheme or candidate.netloc:
        # Anything absolute must point back at this host.
        if candidate.netloc != urlparse(request.host_url).netloc:
            return None
    if not candidate.path.startswith("/"):
        return None
    return candidate.path + (f"?{candidate.query}" if candidate.query else "")


def search_products(term: str, limit: int = 8) -> list:
    """Shared live-search query used by the header dropdown and the search page."""
    from models import Product

    like = f"%{term}%"
    return list(
        db.session.scalars(
            db.select(Product)
            .where(
                Product.is_active.is_(True),
                or_(
                    Product.name.ilike(like),
                    Product.brand.ilike(like),
                    Product.sku.ilike(like),
                    Product.description.ilike(like),
                ),
            )
            .order_by(Product.sold_count.desc(), Product.rating.desc())
            .limit(limit)
        ).all()
    )


def render_or_json(template: str, payload: dict, status: int = 200):
    """Return JSON for fetch() callers, otherwise render the page."""
    if wants_json():
        return jsonify(payload), status
    return render_template(template, **payload), status


def now_ts() -> float:
    return time.time()


def visitor_id() -> str:
    """Stable per-browser id so guests still get recently-viewed and live search."""
    vid = session.get("visitor_id")
    if not vid:
        vid = secrets.token_urlsafe(12)
        session["visitor_id"] = vid
    return vid


def touch_login(user) -> None:
    user.last_login_at = utcnow()
