import secrets
from datetime import timedelta
from decimal import Decimal

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    Response,
    stream_with_context,
)
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    AuditLog,
    CartItem,
    Category,
    Coupon,
    EmailLog,
    GiftCard,
    LoyaltyEvent,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    ProductImage,
    ProductQuestion,
    Review,
    StockAlert,
    SupportTicket,
    TicketMessage,
    User,
    ViewEvent,
    ORDER_FLOW,
    CANCELLED,
    utcnow,
)
from helpers import current_user, admin_required, audit, log_email, safe_next, slugify

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

STATUS_SLUG = {s: s.lower().replace(" ", "-") for s in ORDER_FLOW + [CANCELLED]}


def _slugify(value: str) -> str:
    return slugify(value)


# ---------------------------------------------------------------- dashboard

@admin_bp.get("/")
@admin_required
def dashboard():
    revenue = db.session.scalar(
        db.select(func.coalesce(func.sum(Order.total), 0)).where(Order.status != CANCELLED)
    )
    order_count = db.session.scalar(db.select(func.count()).select_from(Order))
    product_count = db.session.scalar(
        db.select(func.count()).select_from(Product).where(Product.is_active.is_(True))
    )
    user_count = db.session.scalar(db.select(func.count()).select_from(User))
    low_stock = db.session.scalars(
        db.select(Product)
        .where(Product.is_active.is_(True), Product.stock <= Product.low_stock_threshold)
        .order_by(Product.stock.asc())
        .limit(6)
    ).all()
    recent_orders = db.session.scalars(
        db.select(Order).order_by(Order.created_at.desc()).limit(6)
    ).all()

    per_status = dict(
        db.session.execute(
            db.select(Order.status, func.count(Order.id)).group_by(Order.status)
        ).all()
    )

    top_products = db.session.execute(
        db.select(
            OrderItem.product_name,
            func.sum(OrderItem.quantity).label("units"),
            func.sum(OrderItem.unit_price * OrderItem.quantity).label("revenue"),
        )
        .group_by(OrderItem.product_name)
        .order_by(func.sum(OrderItem.unit_price * OrderItem.quantity).desc())
        .limit(5)
    ).all()

    open_tickets = db.session.scalar(
        db.select(func.count())
        .select_from(SupportTicket)
        .where(SupportTicket.status.in_(("open", "pending")))
    )
    unanswered_questions = db.session.scalar(
        db.select(func.count())
        .select_from(ProductQuestion)
        .where(ProductQuestion.is_answered.is_(False))
    )

    return render_template(
        "admin/dashboard.html",
        revenue=revenue,
        order_count=order_count,
        product_count=product_count,
        user_count=user_count,
        low_stock=low_stock,
        recent_orders=recent_orders,
        per_status=per_status,
        top_products=top_products,
        status_slug=STATUS_SLUG,
        open_tickets=open_tickets or 0,
        unanswered_questions=unanswered_questions or 0,
    )


# ---------------------------------------------------------------- products

@admin_bp.route("/products", methods=["GET", "POST"])
@admin_required
def products():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        price = request.form.get("price", type=float)

        errors = []
        if len(name) < 3:
            errors.append("Product name must be at least 3 characters.")
        if price is None or price <= 0:
            errors.append("Price must be greater than zero.")
        if errors:
            for err in errors:
                flash(err, "error")
            return redirect(url_for("admin.products"))

        slug = _slugify(name)
        if db.session.scalar(db.select(Product.id).where(Product.slug == slug)):
            slug = f"{slug}-{secrets.token_hex(2)}"

        db.session.add(
            Product(
                name=name,
                slug=slug,
                sku=(request.form.get("sku") or "").strip()[:40],
                brand=(request.form.get("brand") or "Indian Store").strip()[:80],
                description=(request.form.get("description") or "").strip(),
                price=Decimal(str(price)),
                stock=request.form.get("stock", type=int) or 0,
                image_url=(request.form.get("image_url") or "").strip()[:400],
                category_id=request.form.get("category_id", type=int),
                is_featured=bool(request.form.get("is_featured")),
            )
        )
        audit(current_user(), "product_create", "Product", name, f"${price:.2f}")
        db.session.commit()
        flash(f"“{name}” created.", "success")
        return redirect(url_for("admin.products"))

    search = (request.args.get("q") or "").strip()
    stmt = db.select(Product)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like)))
    rows = db.session.scalars(stmt.order_by(Product.created_at.desc())).all()
    categories = db.session.scalars(db.select(Category).order_by(Category.name)).all()
    return render_template(
        "admin/products.html", products=rows, categories=categories, search=search
    )


@admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    categories = db.session.scalars(db.select(Category).order_by(Category.name)).all()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        price = request.form.get("price", type=float)

        if len(name) < 3:
            flash("Product name must be at least 3 characters.", "error")
            return redirect(url_for("admin.edit_product", product_id=product_id))
        if price is None or price <= 0:
            flash("Price must be greater than zero.", "error")
            return redirect(url_for("admin.edit_product", product_id=product_id))

        compare_at = request.form.get("compare_at", type=float)
        cost = request.form.get("cost", type=float) or 0.0

        product.name = name
        product.brand = (request.form.get("brand") or "Indian Store").strip()[:80]
        product.sku = (request.form.get("sku") or "").strip()[:40]
        product.description = (request.form.get("description") or "").strip()
        product.highlights = (request.form.get("highlights") or "").strip()
        product.price = Decimal(str(price))
        product.compare_at = Decimal(str(compare_at)) if compare_at else None
        product.cost = Decimal(str(cost))
        product.stock = request.form.get("stock", type=int) or 0
        product.image_url = (request.form.get("image_url") or "").strip()[:400]
        product.badge = (request.form.get("badge") or "").strip()[:40]
        product.category_id = request.form.get("category_id", type=int)
        product.is_active = bool(request.form.get("is_active"))
        product.is_featured = bool(request.form.get("is_featured"))

        audit(current_user(), "product_update", "Product", product.id, f"${product.price:.2f}, stock {product.stock}")
        db.session.commit()
        flash(f"“{product.name}” saved.", "success")
        return redirect(url_for("admin.products"))

    return render_template(
        "admin/product_edit.html", product=product, categories=categories
    )


@admin_bp.post("/products/<int:product_id>/toggle")
@admin_required
def toggle_product(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    product.is_active = not product.is_active
    audit(current_user(), "product_toggle", "Product", product.id,
          f"is_active={product.is_active}")
    db.session.commit()
    flash(
        f"“{product.name}” is now {'visible' if product.is_active else 'hidden'}.",
        "info",
    )
    return redirect(safe_next(request.referrer) or url_for("admin.products"))


@admin_bp.post("/products/<int:product_id>/delete")
@admin_required
def delete_product(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)

    in_orders = db.session.scalar(
        db.select(func.count()).select_from(OrderItem).where(OrderItem.product_id == product_id)
    )
    if in_orders:
        # Preserve order history — archive instead of hard-deleting.
        product.is_active = False
        db.session.commit()
        flash(
            "That product is referenced by past orders, so it was archived (hidden) instead of deleted.",
            "info",
        )
        return redirect(url_for("admin.products"))

    name = product.name
    db.session.delete(product)
    audit(current_user(), "product_delete", "Product", name)
    db.session.commit()
    flash(f"“{name}” deleted.", "info")
    return redirect(url_for("admin.products"))


# ---------------------------------------------------------------- orders

@admin_bp.get("/orders")
@admin_required
def orders():
    status_filter = (request.args.get("status") or "").strip()
    search = (request.args.get("q") or "").strip()

    stmt = db.select(Order)
    if status_filter in ORDER_FLOW + [CANCELLED]:
        stmt = stmt.where(Order.status == status_filter)
    if search:
        like = f"%{search}%"
        stmt = stmt.outerjoin(User, User.id == Order.user_id).where(
            or_(
                Order.reference.ilike(like),
                Order.shipping_name.ilike(like),
                User.email.ilike(like),
            )
        )

    rows = db.session.scalars(stmt.order_by(Order.created_at.desc())).all()
    counts = dict(
        db.session.execute(
            db.select(Order.status, func.count(Order.id)).group_by(Order.status)
        ).all()
    )
    return render_template(
        "admin/orders.html",
        orders=rows,
        status_filter=status_filter,
        counts=counts,
        search=search,
        status_slug=STATUS_SLUG,
    )


@admin_bp.post("/orders/<int:order_id>/status")
@admin_required
def update_order_status(order_id):
    order = db.session.get(Order, order_id)
    if order is None:
        abort(404)

    status = request.form.get("status") or ""
    if status not in ORDER_FLOW + [CANCELLED]:
        flash("Unknown order status.", "error")
        return redirect(url_for("admin.orders"))

    previous = order.status
    if status == CANCELLED and order.status != CANCELLED:
        for item in order.items:
            if item.product:
                item.product.stock += item.quantity
                item.product.sold_count = max(0, item.product.sold_count - item.quantity)

    note = (request.form.get("note") or "").strip()[:200]

    if status == "Shipped" and not order.tracking_number:
        order.tracking_number = "NX" + secrets.token_hex(5).upper()
        generated = f"Handed to {order.carrier}. Tracking: {order.tracking_number}"
        order.add_event("Shipped", f"{note} {generated}".strip() if note else generated)
    else:
        order.add_event(status, note)

    audit(current_user(), "status_change", "Order", order.reference, f"{previous} -> {status}")
    db.session.commit()
    flash(f"{order.reference} is now “{status}”.", "success")
    return redirect(url_for("admin.orders", status=order.status))


# ---------------------------------------------------------------- coupons

@admin_bp.route("/coupons", methods=["GET", "POST"])
@admin_required
def coupons():
    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()
        kind = request.form.get("kind", "percent")
        value = request.form.get("value", type=float) or 0

        errors = []
        if not code:
            errors.append("Coupon code is required.")
        elif db.session.scalar(db.select(Coupon.id).where(db.func.upper(Coupon.code) == code)):
            errors.append("That code already exists.")
        if kind not in ("percent", "fixed", "freeship"):
            errors.append("Unknown discount type.")
        elif kind == "percent" and not (0 < value <= 100):
            errors.append("Percentage must be between 1 and 100.")
        elif kind == "fixed" and value <= 0:
            errors.append("Fixed amount must be greater than zero.")

        if errors:
            for err in errors:
                flash(err, "error")
            return redirect(url_for("admin.coupons"))

        db.session.add(
            Coupon(
                code=code,
                description=(request.form.get("description") or "").strip()[:160],
                kind=kind,
                value=Decimal(str(value)),
                min_subtotal=Decimal(str(request.form.get("min_subtotal", type=float) or 0)),
                max_uses=request.form.get("max_uses", type=int) or 0,
                is_active=bool(request.form.get("is_active", "on")),
            )
        )
        audit(current_user(), "coupon_create", "Coupon", code, f"{kind} {value}")
        db.session.commit()
        flash(f"Coupon {code} created.", "success")
        return redirect(url_for("admin.coupons"))

    rows = db.session.scalars(db.select(Coupon).order_by(Coupon.created_at.desc())).all()
    return render_template("admin/coupons.html", coupons=rows)


@admin_bp.post("/coupons/<int:coupon_id>/toggle")
@admin_required
def toggle_coupon(coupon_id):
    coupon = db.session.get(Coupon, coupon_id)
    if coupon is None:
        abort(404)
    coupon.is_active = not coupon.is_active
    db.session.commit()
    flash(f"Coupon {coupon.code} {'activated' if coupon.is_active else 'paused'}.", "info")
    return redirect(url_for("admin.coupons"))


@admin_bp.post("/coupons/<int:coupon_id>/delete")
@admin_required
def delete_coupon(coupon_id):
    coupon = db.session.get(Coupon, coupon_id)
    if coupon is None:
        abort(404)
    code = coupon.code
    db.session.delete(coupon)
    audit(current_user(), "coupon_delete", "Coupon", code)
    db.session.commit()
    flash(f"Coupon {code} deleted.", "info")
    return redirect(url_for("admin.coupons"))


# ---------------------------------------------------------------- users

@admin_bp.get("/users")
@admin_required
def users():
    search = (request.args.get("q") or "").strip()
    stmt = db.select(User)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(User.full_name.ilike(like), User.email.ilike(like))
        )
    rows = db.session.scalars(stmt.order_by(User.created_at.desc())).all()
    return render_template("admin/users.html", users=rows, search=search)


@admin_bp.post("/users/<int:user_id>/toggle-admin")
@admin_required
def toggle_admin(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    me = current_user()
    if user.id == me.id:
        flash("You cannot remove your own admin access.", "error")
        return redirect(url_for("admin.users"))
    user.is_admin = not user.is_admin
    audit(current_user(), "user_role", "User", user.email, f"is_admin={user.is_admin}")
    db.session.commit()
    flash(f"{user.full_name} is {'now an admin' if user.is_admin else 'no longer an admin'}.", "info")
    return redirect(url_for("admin.users"))


@admin_bp.get("/reviews")
@admin_required
def reviews():
    rows = db.session.scalars(
        db.select(Review).order_by(Review.created_at.desc()).limit(50)
    ).all()
    return render_template("admin/reviews.html", reviews=rows)


@admin_bp.post("/reviews/<int:review_id>/delete")
@admin_required
def delete_review(review_id):
    review = db.session.get(Review, review_id)
    if review is None:
        abort(404)
    product = review.product
    db.session.delete(review)
    db.session.flush()
    if product:
        product.recalc_rating()
    db.session.commit()
    flash("Review removed.", "info")
    return redirect(url_for("admin.reviews"))


# ---------------------------------------------------------------- audit trail

AUDIT_ACTIONS = [
    "login",
    "logout",
    "register",
    "order_placed",
    "status_change",
    "order_cancel",
    "product_create",
    "product_update",
    "product_delete",
    "product_toggle",
    "coupon_create",
    "coupon_delete",
    "user_role",
    "question_answer",
    "ticket_reply",
    "ticket_close",
    "gift_card_issue",
    "points_adjust",
    "settings_update",
]


@admin_bp.get("/audit")
@admin_required
def audit_log():
    action = (request.args.get("action") or "").strip()
    search = (request.args.get("q") or "").strip()

    stmt = db.select(AuditLog).outerjoin(User, User.id == AuditLog.actor_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(AuditLog.detail.ilike(like), User.email.ilike(like), AuditLog.entity.ilike(like))
        )

    rows = db.session.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(300)).all()
    return render_template(
        "admin/audit.html",
        entries=rows,
        actions=AUDIT_ACTIONS,
        action_filter=action,
        search=search,
    )


# ---------------------------------------------------------------- analytics

@admin_bp.get("/analytics")
@admin_required
def analytics():
    days = max(7, min(request.args.get("days", type=int) or 30, 365))
    since = utcnow() - timedelta(days=days)

    orders = db.session.scalars(
        db.select(Order).where(Order.created_at >= since, Order.status != CANCELLED)
    ).all()

    # Bucket by day so the chart works even when a day has no orders.
    buckets: list[dict] = []
    index = {}
    for offset in range(days - 1, -1, -1):
        day = (utcnow() - timedelta(days=offset)).date().isoformat()
        index[day] = len(buckets)
        buckets.append({"day": day, "revenue": 0.0, "orders": 0, "units": 0})

    for order in orders:
        key = order.created_at.date().isoformat()
        if key in index:
            row = buckets[index[key]]
            row["revenue"] += float(order.total)
            row["orders"] += 1
            row["units"] += sum(i.quantity for i in order.items)

    peak = max((b["revenue"] for b in buckets), default=0.0) or 1.0

    by_category = db.session.execute(
        db.select(Category.name, func.coalesce(func.sum(OrderItem.unit_price * OrderItem.quantity), 0))
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .join(Product, Product.id == OrderItem.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(Order.created_at >= since, Order.status != CANCELLED)
        .group_by(Category.name)
        .order_by(func.sum(OrderItem.unit_price * OrderItem.quantity).desc())
    ).all()

    by_source = db.session.execute(
        db.select(Order.payment_method, func.count(Order.id), func.coalesce(func.sum(Order.total), 0))
        .where(Order.created_at >= since)
        .group_by(Order.payment_method)
    ).all()

    top_products = db.session.execute(
        db.select(
            Product.name,
            func.coalesce(func.sum(OrderItem.unit_price * OrderItem.quantity), 0),
            func.coalesce(func.sum(OrderItem.quantity), 0),
        )
        .select_from(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .join(Product, Product.id == OrderItem.product_id)
        .where(Order.created_at >= since, Order.status != CANCELLED)
        .group_by(Product.name)
        .order_by(func.sum(OrderItem.unit_price * OrderItem.quantity).desc())
        .limit(8)
    ).all()

    repeat_rate = 0.0
    buyer_counts = dict(
        db.session.execute(
            db.select(Order.user_id, func.count(Order.id))
            .where(Order.status != CANCELLED)
            .group_by(Order.user_id)
        ).all()
    )
    if buyer_counts:
        repeat_rate = sum(1 for c in buyer_counts.values() if c > 1) / len(buyer_counts) * 100

    return render_template(
        "admin/analytics.html",
        days=days,
        series=buckets,
        peak=peak,
           by_category=by_category,
           by_source=by_source,
           top_products=top_products,
        revenue=sum(b["revenue"] for b in buckets),
        orders=sum(b["orders"] for b in buckets),
        units=sum(b["units"] for b in buckets),
        repeat_rate=repeat_rate,
        views=db.session.scalar(db.select(func.count()).select_from(ViewEvent)) or 0,
    )


@admin_bp.get("/analytics/orders.csv")
@admin_required
def export_orders_csv():
    orders = db.session.scalars(db.select(Order).order_by(Order.created_at.desc())).all()
    out = [
        "Reference,Date,Status,Customer,Email,Items,Subtotal,Discount,Shipping,Tax,Total,"
        "Currency,Coupon,Points earned,Points redeemed,Gift card,Payment"
    ]
    for o in orders:
        customer = o.user.full_name if o.user else "Guest"
        email = o.user.email if o.user else ""
        out.append(
            ",".join(
                [
                    o.reference,
                    o.created_at.isoformat(timespec="seconds"),
                    f'"{o.status}"',
                    f'"{customer}"',
                    f'"{email}"',
                    str(sum(i.quantity for i in o.items)),
                    f"{o.subtotal:.2f}",
                    f"{o.discount:.2f}",
                    f"{o.shipping_cost:.2f}",
                    f"{o.tax:.2f}",
                    f"{o.total:.2f}",
                    o.currency,
                    o.coupon_code or "",
                    str(o.points_earned),
                    str(o.points_redeemed),
                    o.gift_card_code or "",
                    o.payment_method or "",
                ]
            )
        )
    return Response(
        "\n".join(out),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=indian-store-orders.csv"},
    )


@admin_bp.get("/analytics/products.csv")
@admin_required
def export_products_csv():
    rows = db.session.execute(
        db.select(
            Product.sku,
            Product.name,
            Product.brand,
            Category.name,
            Product.price,
            Product.stock,
            Product.sold_count,
            Product.rating,
        )
        .outerjoin(Category, Category.id == Product.category_id)
        .order_by(Product.sold_count.desc())
    ).all()
    out = ["SKU,Name,Brand,Category,Price,Stock,Units sold,Rating"]
    for r in rows:
        out.append(
            ",".join(
                [
                    f'"{r[0] or ""}"',
                    f'"{r[1]}"',
                    f'"{r[2]}"',
                    f'"{r[3] or ""}"',
                    f"{r[4]:.2f}",
                    str(r[5]),
                    str(r[6]),
                    f"{r[7]:.1f}",
                ]
            )
        )
    return Response(
        "\n".join(out),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=indian-store-products.csv"},
    )


# ---------------------------------------------------------------- Q&A

@admin_bp.get("/questions")
@admin_required
def questions():
    only_open = request.args.get("open") == "1"
    stmt = db.select(ProductQuestion).order_by(
        ProductQuestion.is_answered, ProductQuestion.created_at.desc()
    )
    if only_open:
        stmt = stmt.where(ProductQuestion.is_answered.is_(False))
    rows = db.session.scalars(stmt.limit(200)).all()
    return render_template("admin/questions.html", questions=rows, only_open=only_open)


@admin_bp.post("/questions/<int:question_id>/answer")
@admin_required
def answer_question(question_id):
    question = db.session.get(ProductQuestion, question_id)
    if question is None:
        abort(404)

    answer = (request.form.get("answer") or "").strip()
    if len(answer) < 5:
        flash("Write an answer of at least 5 characters.", "error")
        return redirect(url_for("admin.questions"))

    question.answer = answer[:2000]
    question.answered_by_id = current_user().id
    question.answered_at = utcnow()
    audit(current_user(), "question_answer", "ProductQuestion", question.id,
          f"Answered: {question.body[:60]}")
    if question.user:
        log_email(
            question.user,
            question.user.email,
            f"Your question about {question.product.name if question.product else 'a product'} was answered",
            answer,
            kind="support",
        )
    db.session.commit()
    flash("Answer published and the customer notified.", "success")
    return redirect(url_for("admin.questions"))


# ---------------------------------------------------------------- support desk

@admin_bp.get("/tickets")
@admin_required
def tickets():
    status = (request.args.get("status") or "open").strip()
    stmt = db.select(SupportTicket)
    if status in ("open", "pending", "resolved", "closed"):
        stmt = stmt.where(SupportTicket.status == status)
    rows = db.session.scalars(stmt.order_by(SupportTicket.updated_at.desc()).limit(100)).all()
    counts = dict(
        db.session.execute(
            db.select(SupportTicket.status, func.count(SupportTicket.id)).group_by(
                SupportTicket.status
            )
        ).all()
    )
    return render_template(
        "admin/tickets.html", tickets=rows, status_filter=status, counts=counts
    )


@admin_bp.get("/tickets/<reference>")
@admin_required
def ticket_detail(reference):
    ticket = db.session.scalar(db.select(SupportTicket).filter_by(reference=reference))
    if ticket is None:
        abort(404)
    return render_template("admin/ticket.html", ticket=ticket)


@admin_bp.post("/tickets/<int:ticket_id>/reply")
@admin_required
def reply_ticket(ticket_id):
    ticket = db.session.get(SupportTicket, ticket_id)
    if ticket is None:
        abort(404)

    body = (request.form.get("message") or "").strip()
    if len(body) < 2:
        flash("Write a reply before sending.", "error")
        return redirect(url_for("admin.ticket_detail", reference=ticket.reference))

    db.session.add(
        TicketMessage(
            ticket_id=ticket.id,
            author_id=current_user().id,
            is_staff=True,
            body=body[:4000],
        )
    )
    ticket.status = "pending"
    ticket.updated_at = utcnow()
    audit(current_user(), "ticket_reply", "SupportTicket", ticket.id, body[:80])
    if ticket.user:
        log_email(
            ticket.user,
            ticket.user.email,
            f"New reply on ticket {ticket.reference}",
            body,
            kind="support",
        )
    db.session.commit()
    flash("Reply sent.", "success")
    return redirect(url_for("admin.ticket_detail", reference=ticket.reference))


@admin_bp.post("/tickets/<int:ticket_id>/status")
@admin_required
def ticket_status(ticket_id):
    ticket = db.session.get(SupportTicket, ticket_id)
    if ticket is None:
        abort(404)

    status = (request.form.get("status") or "").strip()
    if status not in ("open", "pending", "resolved", "closed"):
        flash("Unknown ticket status.", "error")
        return redirect(url_for("admin.tickets"))

    ticket.status = status
    ticket.updated_at = utcnow()
    audit(current_user(), "ticket_close", "SupportTicket", ticket.id, f"status={status}")
    db.session.commit()
    flash(f"Ticket marked {status}.", "success")
    return redirect(url_for("admin.tickets", status=status))


# ---------------------------------------------------------------- gift cards

@admin_bp.route("/gift-cards", methods=["GET", "POST"])
@admin_required
def gift_cards():
    if request.method == "POST":
        user_id = request.form.get("user_id", type=int)
        amount = request.form.get("amount", type=float) or 0
        if not user_id or amount <= 0:
            flash("Choose a customer and enter an amount above zero.", "error")
            return redirect(url_for("admin.gift_cards"))

        user = db.session.get(User, user_id)
        if user is None:
            flash("That customer no longer exists.", "error")
            return redirect(url_for("admin.gift_cards"))

        code = f"NXGC-{secrets.token_hex(3).upper()}"
        value = Decimal(str(amount))
        db.session.add(
            GiftCard(
                code=code,
                user_id=user.id,
                # amount is NOT NULL and distinct from the running balance:
                # it records the face value, while balance is what is left.
                amount=value,
                initial_balance=value,
                balance=value,
                currency="USD",
                created_by_id=current_user().id,
            )
        )
        audit(current_user(), "gift_card_issue", "GiftCard", code, f"${amount:.2f} to {user.email}")
        log_email(
            user,
            user.email,
            f"Your Indian Store gift card {code}",
            f"A gift card for ${amount:.2f} has been added to your account. Code: {code}",
            kind="gift_card",
        )
        db.session.commit()
        flash(f"Gift card {code} issued to {user.email}.", "success")
        return redirect(url_for("admin.gift_cards"))

    cards = db.session.scalars(
        db.select(GiftCard).order_by(GiftCard.created_at.desc()).limit(100)
    ).all()
    customers = db.session.scalars(
        db.select(User).where(User.is_admin.is_(False)).order_by(User.full_name)
    ).all()
    outstanding = db.session.scalar(
        db.select(func.coalesce(func.sum(GiftCard.balance), 0))
    )
    return render_template(
        "admin/gift_cards.html",
        cards=cards,
        customers=customers,
        outstanding=outstanding or 0,
    )


# ---------------------------------------------------------------- stock & price alerts

@admin_bp.post("/products/<int:product_id>/alerts/dispatch")
@admin_required
def dispatch_alerts(product_id):
    """Fire any waiting alerts when stock or price changes."""
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)

    pending = db.session.scalars(
        db.select(StockAlert).where(
            StockAlert.product_id == product.id, StockAlert.notified.is_(False)
        )
    ).all()

    sent = 0
    for alert in pending:
        if alert.kind == "restock" and product.stock <= 0:
            continue
        if alert.kind == "price":
            target = alert.target_price or product.price
            if product.price >= target:
                continue
        if alert.user:
            headline = (
                f"{product.name} is back in stock"
                if alert.kind == "restock"
                else f"{product.name} dropped to ${product.price:.2f}"
            )
            log_email(
                alert.user,
                alert.user.email,
                headline,
                f"{headline}\n\nShop now: /product/{product.slug}",
                kind="alert",
            )
        alert.notified = True
        sent += 1

    db.session.commit()
    flash(f"{sent} alert(s) dispatched for “{product.name}”.", "success" if sent else "info")
    return redirect(safe_next(request.referrer) or url_for("admin.products"))


# ---------------------------------------------------------------- product media

@admin_bp.post("/products/<int:product_id>/images")
@admin_required
def add_image(product_id):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)

    url = (request.form.get("url") or "").strip()[:400]
    if not url.startswith(("http://", "https://")):
        flash("Image URL must start with http:// or https://.", "error")
        return redirect(safe_next(request.referrer) or url_for("admin.edit_product", product_id=product_id))

    # The first image on a product becomes the hero automatically. After that
    # an admin has to ask for it, which demotes whichever shot held the slot.
    wants_primary = bool(request.form.get("is_primary"))
    had_images = bool(product.images)
    if wants_primary and had_images:
        for other in product.images:
            other.is_primary = False
    is_primary = wants_primary or not had_images
    db.session.add(ProductImage(product_id=product.id, url=url, is_primary=is_primary))
    audit(current_user(), "product_update", "Product", product.id, f"image added: {url[:60]}")
    db.session.commit()
    flash("Image added to the gallery.", "success")
    return redirect(url_for("admin.edit_product", product_id=product_id))


@admin_bp.post("/images/<int:image_id>/primary")
@admin_required
def primary_image(image_id):
    image = db.session.get(ProductImage, image_id)
    if image is None:
        abort(404)
    for other in image.product.images:
        other.is_primary = other.id == image_id
    db.session.commit()
    flash("Primary image updated.", "success")
    return redirect(url_for("admin.edit_product", product_id=image.product_id))


@admin_bp.post("/images/<int:image_id>/delete")
@admin_required
def delete_image(image_id):
    image = db.session.get(ProductImage, image_id)
    if image is None:
        abort(404)
    product_id = image.product_id
    db.session.delete(image)
    db.session.commit()
    flash("Image removed.", "info")
    return redirect(url_for("admin.edit_product", product_id=product_id))
