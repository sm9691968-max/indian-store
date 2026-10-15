import secrets
import time
from decimal import Decimal

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, or_

from extensions import db
from helpers import (
    current_user,
    like_pattern,
    log_email,
    login_required,
    money_str,
    recommend_products,
    safe_next,
    search_products,
    visitor_id,
    wants_json,
    wishlist_ids,
)
from models import (
    CartItem,
    Category,
    Coupon,
    Order,
    OrderItem,
    Product,
    ProductQuestion,
    Review,
    StockAlert,
    User,
    ViewEvent,
    WishlistItem,
    utcnow,
)

catalog_bp = Blueprint("catalog", __name__)

PER_PAGE = 12
COMPARE_LIMIT = 4
DEALS_STRIP = 8
RECENT_LIMIT = 12
ALERT_KINDS = ("restock", "price")
PRICE_BANDS = [
    ("under-50", "Under", None, Decimal("50")),
    ("50-150", "50 – 150", Decimal("50"), Decimal("150")),
    ("150-400", "150 - 400", Decimal("150"), Decimal("400")),
    ("over-400", "Over", Decimal("400"), None),
]


def _build_query(extra: dict | None = None):
    search = (request.args.get("q") or "").strip()
    slug = (request.args.get("category") or "").strip()
    sort = request.args.get("sort", "featured")
    band = (request.args.get("price") or "").strip()
    brand = (request.args.get("brand") or "").strip()
    min_rating = request.args.get("rating", type=int) or 0
    in_stock_only = request.args.get("stock") == "1"

    # Natural-language search overrides: parsed filters win over the raw query,
    # so "gaming laptop under 50,000" behaves like the filters it implies.
    if extra:
        search = (extra.get("q") if extra.get("q") is not None else search) or ""
        slug = extra.get("category", slug) or ""
        band = extra.get("price", band) or ""
        brand = extra.get("brand", brand) or ""
        min_rating = int(extra.get("rating", min_rating) or 0)
        if extra.get("stock") is not None:
            in_stock_only = bool(extra["stock"])

    stmt = db.select(Product).where(Product.is_active.is_(True))

    if search:
        like = like_pattern(search)
        stmt = stmt.where(
            or_(
                Product.name.ilike(like, escape="\\"),
                Product.description.ilike(like, escape="\\"),
                Product.sku.ilike(like, escape="\\"),
            )
        )

    if slug:
        category = db.session.scalar(db.select(Category).filter_by(slug=slug))
        if category is None:
            abort(404)
        stmt = stmt.where(Product.category_id == category.id)

    for key, _, low, high in PRICE_BANDS:
        if band == key:
            if low is not None:
                stmt = stmt.where(Product.price >= low)
            if high is not None:
                stmt = stmt.where(Product.price < high)
            break

    if brand:
        stmt = stmt.where(Product.brand == brand)

    if min_rating:
        stmt = stmt.where(Product.rating >= min_rating)

    if in_stock_only:
        stmt = stmt.where(Product.stock > 0)

    if sort == "price_asc":
        stmt = stmt.order_by(Product.price.asc())
    elif sort == "price_desc":
        stmt = stmt.order_by(Product.price.desc())
    elif sort == "rating":
        stmt = stmt.order_by(Product.rating.desc(), Product.review_count.desc())
    elif sort == "newest":
        stmt = stmt.order_by(Product.created_at.desc())
    elif sort == "popular":
        stmt = stmt.order_by(Product.sold_count.desc())
    elif sort == "name":
        stmt = stmt.order_by(Product.name.asc())
    else:
        stmt = stmt.order_by(
            Product.is_featured.desc(), Product.rating.desc(), Product.sold_count.desc()
        )

    return stmt, {
        "q": search,
        "category": slug,
        "sort": sort,
        "price": band,
        "brand": brand,
        "rating": min_rating,
        "stock": in_stock_only,
    }


def _deal_stmt():
    """Active products currently marked down (compare_at > price)."""
    return db.select(Product).where(
        Product.is_active.is_(True),
        Product.compare_at.isnot(None),
        Product.compare_at > Product.price,
    )


def _local_bands() -> list[tuple]:
    """Price-band codes with labels rendered in the active currency."""
    return [
        (
            key,
            ("Under " + money_str(high)) if low is None else (
                "Over " + money_str(low) if high is None else f"{money_str(low)} – {money_str(high)}"
            ),
            low,
            high,
        )
        for key, _label, low, high in PRICE_BANDS
    ]


@catalog_bp.get("/deals")
def deals():
    sort = request.args.get("sort", "discount")
    stmt = _deal_stmt()

    if sort == "price_asc":
        stmt = stmt.order_by(Product.price.asc())
    elif sort == "price_desc":
        stmt = stmt.order_by(Product.price.desc())
    elif sort == "rating":
        stmt = stmt.order_by(Product.rating.desc(), Product.review_count.desc())
    elif sort == "popular":
        stmt = stmt.order_by(Product.sold_count.desc())
    elif sort == "newest":
        stmt = stmt.order_by(Product.created_at.desc())
    else:
        # Smallest ratio of sale price to original = deepest savings first.
        sort = "discount"
        stmt = stmt.order_by((Product.price / Product.compare_at).asc())

    page = max(1, request.args.get("page", type=int) or 1)
    total = db.session.scalar(
        db.select(func.count()).select_from(stmt.order_by(None).subquery())
    ) or 0
    pages = max(1, -(-total // PER_PAGE))
    page = min(page, pages)

    products = db.session.scalars(
        stmt.limit(PER_PAGE).offset((page - 1) * PER_PAGE)
    ).all()

    max_pct = db.session.scalar(
        db.select(func.max(100 - (Product.price * 100 / Product.compare_at)))
        .where(
            Product.is_active.is_(True),
            Product.compare_at.isnot(None),
            Product.compare_at > Product.price,
        )
    ) or 0

    return render_template(
        "catalog/deals.html",
        products=products,
        sort=sort,
        page=page,
        pages=pages,
        total=total,
        max_pct=int(round(float(max_pct))),
        wish_ids=wishlist_ids(current_user()),
    )


@catalog_bp.get("/")
def index():
    raw_q = (request.args.get("q") or "").strip()
    extra, nl_powered = None, False
    if raw_q:
        from blueprints.assistant import parse_search

        parsed = parse_search(raw_q)
        if parsed:
            extra = {key: value for key, value in parsed.items() if value is not None}
            if "q" not in parsed:
                extra["q"] = ""
            nl_powered = True

    stmt, filters = _build_query(extra)
    page = max(1, request.args.get("page", type=int) or 1)
    count_stmt = db.select(func.count()).select_from(stmt.order_by(None).subquery())
    total = db.session.scalar(count_stmt) or 0
    pages = max(1, -(-total // PER_PAGE))
    page = min(page, pages)

    products = db.session.scalars(
        stmt.limit(PER_PAGE).offset((page - 1) * PER_PAGE)
    ).all()

    categories = db.session.scalars(db.select(Category).order_by(Category.name)).all()
    counts = dict(
        db.session.execute(
            db.select(Product.category_id, func.count(Product.id))
            .where(Product.is_active.is_(True))
            .group_by(Product.category_id)
        ).all()
    )
    for category in categories:
        category.product_count = counts.get(category.id, 0)

    brands = [
        row[0]
        for row in db.session.execute(
            db.select(Product.brand)
            .where(Product.is_active.is_(True))
            .distinct()
            .order_by(Product.brand)
        ).all()
    ]

    deals = db.session.scalars(
        _deal_stmt()
        .order_by((Product.price / Product.compare_at).asc())
        .limit(DEALS_STRIP)
    ).all()

    # The promo strip and category rack only belong on the unfiltered landing
    # page: on a filtered/search result they are noise and they pollute the
    # price-band contract a listing page is expected to hold.
    show_deals = (
        page == 1
        and filters["sort"] == "featured"
        and not any(
            (
                filters["q"],
                filters["category"],
                filters["price"],
                filters["brand"],
                filters["rating"],
                filters["stock"],
            )
        )
    )

    return render_template(
        "catalog/index.html",
        products=products,
        categories=categories,
        brands=brands,
        deals=deals,
        show_deals=show_deals,
        price_bands=_local_bands(),
        filters=filters,
        page=page,
        pages=pages,
        total=total,
        wish_ids=wishlist_ids(current_user()),
        nl_powered=nl_powered,
    )


@catalog_bp.get("/product/<slug>")
def product_detail(slug):
    product = db.session.scalar(db.select(Product).filter_by(slug=slug))
    if product is None or not product.is_active:
        abort(404)

    related = (
        db.session.scalars(
            db.select(Product)
            .where(
                Product.category_id == product.category_id,
                Product.id != product.id,
                Product.is_active.is_(True),
            )
            .order_by(Product.rating.desc())
            .limit(4)
        ).all()
        if product.category_id
        else []
    )

    reviews = sorted(product.reviews, key=lambda r: -r.created_at.timestamp())
    breakdown = product.rating_breakdown
    # Only trust the star distribution when every counted review is a real row.
    if sum(breakdown.values()) != product.review_count:
        breakdown = None

    questions = sorted(product.questions, key=lambda q: (q.is_answered, -q.created_at.timestamp()))
    user = current_user()
    wish_ids = wishlist_ids(user)
    my_review = None
    if user:
        my_review = next((r for r in reviews if r.user_id == user.id), None)

    purchased = False
    if user:
        purchased = bool(
            db.session.scalar(
                db.select(func.count())
                .select_from(OrderItem)
                .join(Order, Order.id == OrderItem.order_id)
                .where(OrderItem.product_id == product.id, Order.user_id == user.id)
            )
        )

    _record_view(user, product)

    alerts = _active_alerts(user, product)
    compare_ids = set(session.get("compare", []))
    if user:
        compare_ids |= set(
            db.session.scalars(
                db.select(CartItem.product_id)
                .where(CartItem.user_id == user.id, CartItem.is_saved.is_(False))
            ).all()
        ) & {product.id}

    co_bought = recommend_products([product.id], exclude_ids=[product.id], limit=2)

    return render_template(
        "catalog/product.html",
        product=product,
        related=related,
        co_bought=co_bought,
        reviews=reviews,
        breakdown=breakdown,
        my_review=my_review,
        purchased=purchased,
        wish_ids=wish_ids,
        in_wishlist=product.id in wish_ids,
        questions=questions,
        alerts=alerts,
        in_compare=product.id in compare_ids,
        compare_count=len(compare_ids),
        emi_plans=_emi_plans(product.price),
        viewing_now=_viewing_now(product),
        helpful_ids=set(session.get("helpful", [])),
    )


def _emi_plans(price) -> list[dict]:
    """Add-on-interest EMI spread over the usual Indian tenures (11% p.a.)."""
    principal = Decimal(str(price))
    rate = Decimal("0.11")
    plans = []
    for months in (3, 6, 9, 12):
        per_month = (principal * (1 + rate * Decimal(months) / 12)) / Decimal(months)
        plans.append(
            {
                "months": months,
                "per": per_month.quantize(Decimal("0.01")),
                "total": (principal * (1 + rate * Decimal(months) / 12)).quantize(
                    Decimal("0.01")
                ),
            }
        )
    return plans


def _viewing_now(product) -> int:
    """Deterministic 'people viewing this right now' figure for a product."""
    bucket = int(time.time()) // 60
    seed = ((product.id or 1) * 2654435761 + bucket * 31) & 0xFFFFFFFF
    return 3 + seed % 46


def _record_view(user, product) -> None:
    """Track a product view for the 'recently viewed' rail and analytics.

    One row per session per product: re-visiting refreshes the timestamp instead
    of piling up duplicates, so the rail stays ordered by genuine recency.
    """
    visitor = visitor_id()
    existing = db.session.scalar(
        db.select(ViewEvent)
        .where(ViewEvent.session_id == visitor, ViewEvent.product_id == product.id)
        .order_by(ViewEvent.viewed_at.desc())
    )
    if existing is not None:
        existing.viewed_at = utcnow()
        if user and existing.user_id is None:
            # Anonymous first view, signed-in second: keep the account attached.
            existing.user_id = user.id
    else:
        db.session.add(
            ViewEvent(
                user_id=user.id if user else None,
                product_id=product.id,
                session_id=visitor,
                viewed_at=utcnow(),
            )
        )
    db.session.commit()


def _active_alerts(user, product) -> list[StockAlert]:
    if user is None:
        return []
    return db.session.scalars(
        db.select(StockAlert).where(
            StockAlert.user_id == user.id,
            StockAlert.product_id == product.id,
        )
    ).all()


@catalog_bp.route("/product/<slug>/review", methods=["POST"])
@login_required
def submit_review(slug):
    product = db.session.scalar(db.select(Product).filter_by(slug=slug))
    if product is None:
        abort(404)

    rating = request.form.get("rating", type=int) or 0
    title = (request.form.get("title") or "").strip()[:140]
    body = (request.form.get("body") or "").strip()

    if rating < 1 or rating > 5:
        flash("Pick a star rating between 1 and 5.", "error")
        return redirect(url_for("catalog.product_detail", slug=slug))
    if len(body) < 10:
        flash("Please write at least 10 characters in your review.", "error")
        return redirect(url_for("catalog.product_detail", slug=slug))

    user = current_user()
    review = db.session.scalar(
        db.select(Review).where(Review.user_id == user.id, Review.product_id == product.id)
    )
    if review:
        review.rating = rating
        review.title = title
        review.body = body
        review.updated_at = utcnow()
        flash("Your review was updated.", "success")
    else:
        review = Review(
            user_id=user.id,
            product_id=product.id,
            rating=rating,
            title=title,
            body=body,
            is_verified=True,
        )
        db.session.add(review)
        flash("Thanks — your review is live.", "success")

    db.session.flush()
    product.recalc_rating()
    db.session.commit()

    return redirect(url_for("catalog.product_detail", slug=slug) + "#reviews")


@catalog_bp.post("/product/<slug>/review/<int:review_id>/delete")
@login_required
def delete_review(slug, review_id):
    product = db.session.scalar(db.select(Product).filter_by(slug=slug))
    if product is None:
        abort(404)

    review = db.session.get(Review, review_id)
    if review is None or review.product_id != product.id:
        abort(404)
    if review.user_id != current_user().id:
        abort(403)

    db.session.delete(review)
    db.session.flush()
    product.recalc_rating()
    db.session.commit()
    flash("Review deleted.", "info")

    return redirect(url_for("catalog.product_detail", slug=slug) + "#reviews")


@catalog_bp.post("/wishlist/<int:product_id>/toggle")
@login_required
def toggle_wishlist(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)

    user = current_user()
    item = db.session.scalar(
        db.select(WishlistItem).where(
            WishlistItem.user_id == user.id, WishlistItem.product_id == product_id
        )
    )

    wants_json = request.accept_mimetypes.best == "application/json"

    if item:
        db.session.delete(item)
        message = f"{product.name} removed from your wishlist."
        saved = False
    else:
        db.session.add(WishlistItem(user_id=user.id, product_id=product_id))
        message = f"{product.name} saved to your wishlist."
        saved = True

    db.session.commit()

    if wants_json:
        return {
            "saved": saved,
            "count": len(wishlist_ids(user)),
            "message": message,
        }
    flash(message, "info" if not saved else "success")
    return redirect(safe_next(request.referrer) or url_for("catalog.index"))


@catalog_bp.get("/wishlist")
@login_required
def wishlist():
    user = current_user()
    items = db.session.scalars(
        db.select(WishlistItem)
        .where(WishlistItem.user_id == user.id)
        .order_by(WishlistItem.added_at.desc())
    ).all()
    products = [i.product for i in items if i.product and i.product.is_active]
    in_cart = set(
        db.session.scalars(
            db.select(CartItem.product_id).where(CartItem.user_id == user.id)
        ).all()
    )

    # Anyone with the token can read a shared wishlist; nobody can edit it.
    share_url = None
    if user.wishlist_token:
        share_url = url_for("catalog.wishlist_shared", token=user.wishlist_token, _external=True)
    else:
        user.wishlist_token = secrets.token_urlsafe(12)
        db.session.commit()
        share_url = url_for("catalog.wishlist_shared", token=user.wishlist_token, _external=True)

    return render_template(
        "catalog/wishlist.html",
        products=products,
        in_cart=in_cart,
        total=len(products),
        share_url=share_url,
    )


@catalog_bp.get("/shared/<token>")
def wishlist_shared(token):
    owner = db.session.scalar(db.select(User).filter_by(wishlist_token=token))
    if owner is None:
        abort(404)
    items = db.session.scalars(
        db.select(WishlistItem)
        .where(WishlistItem.user_id == owner.id)
        .order_by(WishlistItem.added_at.desc())
    ).all()
    products = [i.product for i in items if i.product and i.product.is_active]
    return render_template(
        "catalog/wishlist_shared.html",
        products=products,
        owner=owner,
        total=len(products),
    )


@catalog_bp.post("/compare/<int:product_id>")
@login_required
def add_to_compare(product_id):
    product = db.session.get(Product, product_id)
    if product is None or not product.is_active:
        abort(404)

    current = list(session.get("compare", []))
    if product_id in current:
        current.remove(product_id)
        added = False
    else:
        if len(current) >= COMPARE_LIMIT:
            flash(f"You can compare up to {COMPARE_LIMIT} products at a time.", "error")
            return redirect(safe_next(request.referrer) or url_for("catalog.index"))
        current.append(product_id)
        added = True

    session["compare"] = current

    if wants_json():
        return jsonify({"added": added, "count": len(current), "ids": current})
    flash(
        f"{product.name} {'added to' if added else 'removed from'} your comparison.",
        "success" if added else "info",
    )
    return redirect(safe_next(request.referrer) or url_for("catalog.index"))


@catalog_bp.get("/compare")
def compare():
    ids = session.get("compare", [])[:COMPARE_LIMIT]
    products = []
    if ids:
        rows = db.session.scalars(
            db.select(Product).where(Product.id.in_(ids), Product.is_active.is_(True))
        ).all()
        # Preserve the order the shopper added them in.
        products = sorted(rows, key=lambda p: ids.index(p.id))
    else:
        # A shared comparison link carries product slugs instead of session ids.
        slugs = [s.strip() for s in (request.args.get("items") or "").split(",") if s.strip()]
        slugs = slugs[:COMPARE_LIMIT]
        if slugs:
            rows = db.session.scalars(
                db.select(Product).where(
                    Product.slug.in_(slugs), Product.is_active.is_(True)
                )
            ).all()
            products = sorted(rows, key=lambda p: slugs.index(p.slug))
    return render_template("catalog/compare.html", products=products, limit=COMPARE_LIMIT)


@catalog_bp.post("/compare/clear")
def compare_clear():
    session.pop("compare", None)
    flash("Comparison cleared.", "info")
    return redirect(url_for("catalog.compare"))


@catalog_bp.get("/recently-viewed")
def recently_viewed():
    user = current_user()
    stmt = db.select(Product, ViewEvent.viewed_at).join(
        ViewEvent, ViewEvent.product_id == Product.id
    )
    if user is not None:
        stmt = stmt.where(ViewEvent.user_id == user.id)
    else:
        stmt = stmt.where(ViewEvent.session_id == visitor_id())

    rows = db.session.execute(
        stmt.where(Product.is_active.is_(True)).order_by(ViewEvent.viewed_at.desc()).limit(60)
    ).all()

    # Deduplicate to the most recent view of each product.
    seen, products = set(), []
    for product, viewed_at in rows:
        if product.id in seen:
            continue
        seen.add(product.id)
        products.append(product)
        if len(products) >= RECENT_LIMIT:
            break

    return render_template(
        "catalog/recently_viewed.html", products=products, total=len(products)
    )


@catalog_bp.get("/search/suggest")
def search_suggest():
    term = (request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify({"query": term, "results": []})
    results = search_products(term, limit=6)
    return jsonify(
        {
            "query": term,
            "results": [
                {
                    "id": p.id,
                    "name": p.name,
                    "brand": p.brand,
                    "slug": p.slug,
                    "price": f"{p.price:.2f}",
                    "image": p.image,
                    "rating": p.rating,
                }
                for p in results
            ],
        }
    )


@catalog_bp.post("/product/<slug>/ask")
@login_required
def ask_question(slug):
    product = db.session.scalar(db.select(Product).filter_by(slug=slug))
    if product is None:
        abort(404)

    body = (request.form.get("question") or "").strip()
    if len(body) < 10:
        flash("Please write at least 10 characters in your question.", "error")
        return redirect(url_for("catalog.product_detail", slug=slug) + "#questions")

    user = current_user()
    existing = db.session.scalar(
        db.select(ProductQuestion).where(
            ProductQuestion.product_id == product.id,
            ProductQuestion.user_id == user.id,
            func.lower(ProductQuestion.body) == body.lower(),
        )
    )
    if existing is None:
        db.session.add(
            ProductQuestion(user_id=user.id, product_id=product.id, body=body[:500])
        )
        db.session.commit()
        flash("Your question was sent to the Indian Store team.", "success")

    return redirect(url_for("catalog.product_detail", slug=slug) + "#questions")


@catalog_bp.route("/product/<slug>/alert", methods=["POST"])
@login_required
def create_alert(slug):
    product = db.session.scalar(db.select(Product).filter_by(slug=slug))
    if product is None:
        abort(404)

    kind = (request.form.get("kind") or "").strip()
    if kind not in ALERT_KINDS:
        flash("That alert type is not available.", "error")
        return redirect(url_for("catalog.product_detail", slug=slug))

    user = current_user()
    alert = db.session.scalar(
        db.select(StockAlert).where(
            StockAlert.user_id == user.id,
            StockAlert.product_id == product.id,
            StockAlert.kind == kind,
        )
    )
    if alert is None:
        db.session.add(
            StockAlert(
                user_id=user.id,
                product_id=product.id,
                kind=kind,
                target_price=product.price if kind == "price" else None,
            )
        )
        db.session.commit()
        if kind == "price":
            log_email(
                user,
                user.email,
                f"Price alert armed for {product.name}",
                f"We will email {user.email} the moment {product.name} drops below ${product.price:.2f}.",
                kind="alert",
            )
            db.session.commit()
        flash(
            f"You will be emailed when {product.name} "
            f"{'is back in stock' if kind == 'restock' else 'drops in price'}.",
            "success",
        )
    else:
        flash("You already have that alert on this product.", "info")

    return redirect(url_for("catalog.product_detail", slug=slug))


@catalog_bp.post("/alerts/<int:alert_id>/delete")
@login_required
def delete_alert(alert_id):
    alert = db.session.get(StockAlert, alert_id)
    if alert is None or alert.user_id != current_user().id:
        abort(404)
    db.session.delete(alert)
    db.session.commit()
    flash("Alert removed.", "info")
    return redirect(url_for("account.alerts"))


@catalog_bp.get("/product/<slug>/quick")
def quick_view(slug):
    """Compact JSON for the quick-view modal on product cards."""
    from flask_wtf.csrf import generate_csrf

    product = db.session.scalar(db.select(Product).filter_by(slug=slug))
    if product is None or not product.is_active:
        abort(404)
    return jsonify(
        {
            "id": product.id,
            "name": product.name,
            "url": url_for("catalog.product_detail", slug=product.slug),
            "add_url": url_for("cart.add", product_id=product.id),
            "image": product.image,
            "price": money_str(product.price),
            "blurb": product.description,
            "rating": product.rating,
            "review_count": product.review_count,
            "stock": product.stock,
            "brand": product.brand or "",
            "category": product.category.name if product.category else "",
            "csrf": generate_csrf(),
        }
    )


@catalog_bp.post("/review/<int:review_id>/helpful")
@login_required
def review_helpful(review_id):
    """Toggle a 'was this helpful?' vote. Votes are tracked per session."""
    review = db.session.get(Review, review_id)
    if review is None:
        abort(404)
    voted = set(session.get("helpful", []))
    if review_id in voted:
        voted.discard(review_id)
        review.helpful_count = max(0, review.helpful_count - 1)
        marked = False
    else:
        voted.add(review_id)
        review.helpful_count += 1
        marked = True
    session["helpful"] = sorted(voted)
    db.session.commit()

    if wants_json():
        return jsonify({"marked": marked, "count": review.helpful_count})
    return redirect(url_for("catalog.product_detail", slug=review.product.slug) + "#reviews")


@catalog_bp.post("/wishlist/move-all")
@login_required
def wishlist_move_all():
    """Move every in-stock wishlist item into the cart in one action."""
    user = current_user()
    rows = db.session.scalars(
        db.select(WishlistItem).where(WishlistItem.user_id == user.id)
    ).all()
    moved = 0
    for row in rows:
        product = db.session.get(Product, row.product_id)
        if product is None or not product.is_active or product.stock < 1:
            continue
        existing = db.session.scalar(
            db.select(CartItem).where(
                CartItem.user_id == user.id,
                CartItem.product_id == product.id,
                CartItem.variant_id.is_(None),
            )
        )
        if existing:
            existing.quantity = min(existing.quantity + 1, min(product.stock, 20))
            existing.is_saved = False
        else:
            db.session.add(
                CartItem(user_id=user.id, product_id=product.id, quantity=1)
            )
        db.session.delete(row)
        moved += 1
    db.session.commit()
    if moved:
        flash(f"Moved {moved} item{'s' if moved != 1 else ''} to your cart.", "success")
    else:
        flash("Nothing to move — those items are out of stock.", "info")
    return redirect(url_for("catalog.wishlist"))


@catalog_bp.get("/offers")
def offers():
    """Public list of redeemable coupons, so shoppers can self-serve codes."""
    rows = db.session.scalars(db.select(Coupon).order_by(Coupon.code)).all()
    coupons = [c for c in rows if c.is_redeemable]
    return render_template("catalog/offers.html", coupons=coupons)


FAQ_ITEMS = [
    (
        "How long does delivery take?",
        "Metro pincodes are usually delivered the next day. Most other addresses "
        "arrive within 2–5 working days. Orders over the free-delivery threshold "
        "ship at no extra cost.",
    ),
    (
        "How much does shipping cost?",
        "Shipping is calculated at checkout and shown as a separate line before you "
        "pay. Any active free-shipping coupon will zero it out automatically.",
    ),
    (
        "What is your returns policy?",
        "You have 30 days from delivery to return an unused item in its original "
        "packaging. Start the return from the order page and we'll issue a prepaid "
        "label where available.",
    ),
    (
        "Which payment methods do you accept?",
        "Card, UPI, net banking and cash on delivery. EMI is available on eligible "
        "cards and shown on the product page for orders over the EMI threshold.",
    ),
    (
        "How do refunds work?",
        "Once we receive the returned item, refunds are processed within 3–5 working "
        "days to the original payment method. Gift-card balances are restored "
        "immediately.",
    ),
    (
        "Do products carry a warranty?",
        "Yes. Warranty terms are listed on each product page under the spec sheet. "
        "Keep your invoice — it is your proof of purchase for any warranty claim.",
    ),
    (
        "Can I cancel an order?",
        "Orders can be cancelled from the order page until they are marked Shipped. "
        "After shipping, please start a return instead.",
    ),
    (
        "How do I track my order?",
        "Use the Track order page with your order reference and email, or sign in "
        "and open the order for a live timeline.",
    ),
]


@catalog_bp.get("/faq")
def faq():
    return render_template("catalog/faq.html", faqs=FAQ_ITEMS)


@catalog_bp.get("/policies")
def policies():
    """Shipping, returns, warranty, privacy and terms in one indexable page."""
    return render_template("catalog/policies.html")
