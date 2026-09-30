import secrets
from decimal import Decimal

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
    Response,
)
from sqlalchemy import select

from extensions import db
from models import (
    CartItem,
    Coupon,
    GiftCard,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    ORDER_FLOW,
    CANCELLED,
    utcnow,
)
from helpers import (
    current_user,
    login_required,
    quote_cart,
    cart_items,
    saved_items,
    active_coupon,
    audit,
    award_points,
    currency_code,
    log_email,
    points_for_spend,
    safe_next,
    FREE_SHIPPING_THRESHOLD,
)

cart_bp = Blueprint("cart", __name__, url_prefix="/cart")


@cart_bp.get("/")
@login_required
def view():
    user = current_user()
    items = sorted(cart_items(user), key=lambda i: -i.added_at.timestamp())
    saved = sorted(saved_items(user), key=lambda i: -i.added_at.timestamp())
    totals = quote_cart(items, user)
    totals["free_threshold"] = FREE_SHIPPING_THRESHOLD
    totals["points_hint"] = points_for_spend(totals["subtotal"] - totals["discount"])

    return render_template(
        "cart/view.html", items=items, saved=saved, user=user, **totals
    )


@cart_bp.post("/add/<int:product_id>")
@login_required
def add(product_id):
    user = current_user()
    product = db.session.get(Product, product_id)
    if product is None or not product.is_active:
        flash("That product is no longer available.", "error")
        return redirect(safe_next(request.referrer) or url_for("catalog.index"))

    qty = request.form.get("quantity", type=int) or 1
    qty = max(1, min(qty, 20))

    variant = None
    variant_id = request.form.get("variant_id", type=int)
    if variant_id:
        variant = db.session.get(ProductVariant, variant_id)
        if variant is None or variant.product_id != product.id or variant.stock < 1:
            flash("That variant is unavailable.", "error")
            return redirect(url_for("catalog.product_detail", slug=product.slug))

    # Saving for later moves the line out of the active cart instead of duplicating it.
    for_later = request.form.get("save_for_later") == "1"
    available = variant.stock if variant else product.stock

    item = db.session.scalar(
        select(CartItem).where(
            CartItem.user_id == user.id,
            CartItem.product_id == product.id,
            CartItem.variant_id == variant_id,
        )
    )

    if item:
        new_qty = min(item.quantity + qty, available)
        if new_qty < 1:
            flash("There is not enough stock left for that variant.", "error")
            return redirect(url_for("catalog.product_detail", slug=product.slug))
        item.quantity = new_qty
        item.is_saved = for_later
    else:
        if available < 1:
            flash("This product is out of stock.", "error")
            return redirect(url_for("catalog.product_detail", slug=product.slug))
        db.session.add(
            CartItem(
                user_id=user.id,
                product_id=product.id,
                variant_id=variant_id,
                quantity=min(qty, available),
                is_saved=for_later,
            )
        )

    db.session.commit()
    flash(
        f"{product.name} {'saved for later' if for_later else 'added to your cart'}.",
        "success",
    )
    return redirect(safe_next(request.referrer) or url_for("catalog.index"))


@cart_bp.post("/save/<int:item_id>")
@login_required
def toggle_save(item_id):
    """Move a cart line between the active basket and the saved-for-later list."""
    user = current_user()
    item = db.session.scalar(
        select(CartItem).where(CartItem.id == item_id, CartItem.user_id == user.id)
    )
    if item is None:
        abort(404)

    item.is_saved = not item.is_saved
    db.session.commit()
    flash(
        f"{item.product.name} moved to {'your cart' if not item.is_saved else 'saved items'}.",
        "info",
    )
    return redirect(safe_next(request.referrer) or url_for("cart.view"))


@cart_bp.post("/update/<int:item_id>")
@login_required
def update(item_id):
    user = current_user()
    item = db.session.scalar(
        select(CartItem).where(CartItem.id == item_id, CartItem.user_id == user.id)
    )
    if item is None:
        abort(404)

    action = request.form.get("action", "set")
    if action == "remove":
        db.session.delete(item)
    else:
        qty = request.form.get("quantity", type=int)
        stock = item.variant.stock if item.variant else item.product.stock
        if qty is not None:
            item.quantity = max(0, min(qty, stock))
        if item.quantity == 0:
            db.session.delete(item)

    db.session.commit()
    return redirect(safe_next(request.referrer) or url_for("cart.view"))


@cart_bp.post("/clear")
@login_required
def clear():
    user = current_user()
    for item in list(user.cart_items):
        db.session.delete(item)
    db.session.commit()
    flash("Cart emptied.", "info")
    return redirect(url_for("cart.view"))


@cart_bp.route("/coupon", methods=["POST"])
@login_required
def apply_coupon():
    code = (request.form.get("code") or "").strip().upper()
    if not code:
        session.pop("coupon_code", None)
        session.pop("coupon_message", None)
        flash("Coupon removed.", "info")
        return redirect(safe_next(request.referrer) or url_for("cart.view"))

    coupon = db.session.scalar(db.select(Coupon).filter(db.func.upper(Coupon.code) == code))
    if coupon is None or not coupon.is_redeemable:
        session.pop("coupon_code", None)
        session.pop("coupon_message", None)
        flash("That coupon is not valid.", "error")
        return redirect(safe_next(request.referrer) or url_for("cart.view"))

    user = current_user()
    subtotal = sum(
        (Decimal(str(i.line_total)) for i in cart_items(user)), Decimal("0.00")
    )
    if subtotal < coupon.min_subtotal:
        session.pop("coupon_code", None)
        session.pop("coupon_message", None)
        flash(
            f"{coupon.code} needs a minimum subtotal of ${coupon.min_subtotal:,.2f}.",
            "error",
        )
        return redirect(safe_next(request.referrer) or url_for("cart.view"))

    session["coupon_code"] = coupon.code
    session["coupon_message"] = f"{coupon.code} applied — {coupon.label}."
    flash(session["coupon_message"], "success")
    return redirect(safe_next(request.referrer) or url_for("cart.view"))


@cart_bp.post("/coupon/remove")
@login_required
def remove_coupon():
    session.pop("coupon_code", None)
    session.pop("coupon_message", None)
    flash("Coupon removed.", "info")
    return redirect(safe_next(request.referrer) or url_for("cart.view"))


@cart_bp.post("/gift-card")
@login_required
def apply_gift_card():
    code = (request.form.get("code") or "").strip().upper()
    if not code:
        session.pop("gift_card_code", None)
        flash("Gift card removed.", "info")
        return redirect(safe_next(request.referrer) or url_for("cart.view"))

    card = db.session.scalar(db.select(GiftCard).filter(db.func.upper(GiftCard.code) == code))
    if card is None or not card.is_redeemable:
        session.pop("gift_card_code", None)
        flash("That gift card code is not valid or has already been spent.", "error")
        return redirect(safe_next(request.referrer) or url_for("cart.view"))

    session["gift_card_code"] = card.code
    flash(f"Gift card {card.code} applied.", "success")
    return redirect(safe_next(request.referrer) or url_for("cart.view"))


@cart_bp.post("/points")
@login_required
def apply_points():
    points = request.form.get("points", type=int) or 0
    user = current_user()
    if points <= 0:
        session.pop("points_redeem", None)
        flash("Loyalty redemption removed.", "info")
    elif points > user.points:
        session.pop("points_redeem", None)
        flash(f"You only have {user.points} points available.", "error")
    else:
        session["points_redeem"] = points
        flash(f"{points} points will be redeemed at checkout.", "success")
    return redirect(safe_next(request.referrer) or url_for("cart.view"))


@cart_bp.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    user = current_user()
    items = cart_items(user)
    if not items:
        flash("Your cart is empty.", "info")
        return redirect(url_for("catalog.index"))

    totals = quote_cart(items, user)
    default = user.default_address

    form = {
        "shipping_name": user.full_name,
        "shipping_address": default.line1 if default else "",
        "shipping_address2": default.line2 if default else "",
        "shipping_city": default.city if default else "",
        "shipping_postal": default.postal if default else "",
        "shipping_phone": user.phone or (default.phone if default else ""),
        "payment_method": "Card",
        "note": "",
    }
    address_id = request.args.get("address", type=int)

    if request.method == "POST":
        form = {key: (request.form.get(key) or "").strip() for key in form}
        form["payment_method"] = request.form.get("payment_method", "Card")

        required = [
            ("Full name", form["shipping_name"]),
            ("Street address", form["shipping_address"]),
            ("City", form["shipping_city"]),
            ("Postal code", form["shipping_postal"]),
        ]
        missing = [label for label, value in required if not value]
        if missing:
            flash(f"Missing required field(s): {', '.join(missing)}.", "error")
            return render_template("cart/checkout.html", form=form, **totals)

        coupon = totals["coupon"]
        order = Order(
            reference=f"NX-{secrets.token_hex(4).upper()}",
            subtotal=totals["subtotal"],
            discount=totals["discount"],
            shipping_cost=totals["shipping_after"],
            tax=totals["tax"],
            total=totals["total"],
            coupon_code=coupon.code if coupon else "",
            currency=currency_code(),
            points_redeemed=totals["points_redeem"],
            points_earned=points_for_spend(totals["total"]),
            gift_card_code=totals["gift_card"].code if totals["gift_card"] else "",
            payment_method=form["payment_method"],
            note=form["note"][:500],
            shipping_name=form["shipping_name"],
            shipping_address=", ".join(
                b for b in [form["shipping_address"], form["shipping_address2"]] if b
            ),
            shipping_city=form["shipping_city"],
            shipping_postal=form["shipping_postal"],
            shipping_phone=form["shipping_phone"],
            user_id=user.id,
        )
        order.add_event("Processing", "Order received and awaiting payment confirmation.")

        for item in items:
            order.items.append(
                OrderItem(
                    product_id=item.product_id,
                    variant_id=item.variant_id,
                    variant_name=item.variant.name if item.variant else "",
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    product_name=item.product.name,
                )
            )
            if item.variant:
                item.variant.stock = max(0, item.variant.stock - item.quantity)
            item.product.stock = max(0, item.product.stock - item.quantity)
            item.product.sold_count += item.quantity
            db.session.delete(item)

        if coupon:
            coupon.used_count += 1

        # Settle any applied gift card balance and debit the loyalty points.
        if totals["gift_card"] and totals["gift_value"] > 0:
            totals["gift_card"].balance = max(
                0, Decimal(totals["gift_card"].balance) - totals["gift_value"]
            )
            totals["gift_card"].redeemed_count += 1
        if totals["points_redeem"]:
            award_points(user, -totals["points_redeem"], "Redeemed at checkout", None)

        db.session.add(order)
        db.session.flush()

        if order.points_earned:
            award_points(user, order.points_earned, "Earned on order", order.id)
            order.points_earned = order.points_earned

        session.pop("coupon_code", None)
        session.pop("coupon_message", None)
        session.pop("gift_card_code", None)
        session.pop("points_redeem", None)
        audit(
            user,
            "order_placed",
            "Order",
            order.reference,
            f"${order.total:.2f} · {len(order.items)} line(s) · {order.points_earned} pts",
        )
        db.session.commit()

        log_email(
            user,
            user.email,
            f"Order {order.reference} confirmed",
            (
                f"Hi {user.full_name.split()[0]},\n\n"
                f"Order {order.reference} is confirmed. Total ${order.total:,.2f}.\n"
                f"You earned {order.points_earned} Indian Store points.\n\n"
                "— The Indian Store team"
            ),
        )
        db.session.commit()

        flash(f"Order {order.reference} confirmed.", "success")
        return redirect(url_for("cart.order_detail", reference=order.reference))

    if address_id:
        chosen = next((a for a in user.addresses if a.id == address_id), None)
        if chosen:
            form.update(
                {
                    "shipping_name": chosen.full_name,
                    "shipping_address": chosen.line1,
                    "shipping_address2": chosen.line2,
                    "shipping_city": chosen.city,
                    "shipping_postal": chosen.postal,
                    "shipping_phone": chosen.phone,
                }
            )

    return render_template("cart/checkout.html", form=form, user=user, **totals)


@cart_bp.get("/orders")
@login_required
def orders():
    user = current_user()
    status_filter = (request.args.get("status") or "").strip()

    stmt = db.select(Order).where(Order.user_id == user.id)
    if status_filter in ORDER_FLOW + [CANCELLED]:
        stmt = stmt.where(Order.status == status_filter)

    rows = db.session.scalars(stmt.order_by(Order.created_at.desc())).all()

    counts = {}
    for row in db.session.scalars(
        db.select(Order).where(Order.user_id == user.id)
    ).all():
        counts[row.status] = counts.get(row.status, 0) + 1

    return render_template(
        "cart/orders.html", orders=rows, status_filter=status_filter, counts=counts
    )


@cart_bp.get("/orders/<reference>")
@login_required
def order_detail(reference):
    user = current_user()
    order = db.session.scalar(db.select(Order).filter_by(reference=reference))
    if order is None:
        abort(404)
    if order.user_id != user.id and not user.is_admin:
        abort(403)

    return render_template("cart/order_detail.html", order=order, flow=ORDER_FLOW)


@cart_bp.post("/orders/<reference>/cancel")
@login_required
def cancel_order(reference):
    user = current_user()
    order = db.session.scalar(db.select(Order).filter_by(reference=reference))
    if order is None:
        abort(404)
    if order.user_id != user.id and not user.is_admin:
        abort(403)
    if not order.is_cancellable:
        flash("This order has already shipped and can no longer be cancelled.", "error")
        return redirect(url_for("cart.order_detail", reference=reference))

    for item in order.items:
        if item.product:
            item.product.stock += item.quantity
            item.product.sold_count = max(0, item.product.sold_count - item.quantity)

    if order.coupon_code:
        coupon = db.session.scalar(
            db.select(Coupon).filter(db.func.upper(Coupon.code) == order.coupon_code)
        )
        if coupon:
            coupon.used_count = max(0, coupon.used_count - 1)

    order.add_event(CANCELLED, "Cancelled by customer. Stock returned to inventory.")
    audit(user, "order_cancel", "Order", order.reference, f"${order.total:.2f} refunded")
    db.session.commit()
    flash(f"Order {order.reference} cancelled.", "info")
    return redirect(url_for("cart.order_detail", reference=reference))


@cart_bp.get("/count")
def count():
    user = current_user()
    return jsonify({"count": user.cart_count if user else 0})


@cart_bp.get("/orders/<reference>/invoice")
@login_required
def invoice(reference):
    """A printer-friendly invoice that opens in any browser's print dialog."""
    user = current_user()
    order = db.session.scalar(db.select(Order).filter_by(reference=reference))
    if order is None:
        abort(404)
    if order.user_id != user.id and not user.is_admin:
        abort(403)

    if not order.invoice_no:
        order.invoice_no = f"INV-{secrets.token_hex(5).upper()}"
        order.invoice_issued_at = utcnow()
        db.session.commit()

    return render_template("cart/invoice.html", order=order)


@cart_bp.get("/orders/<reference>/invoice.csv")
@login_required
def invoice_csv(reference):
    user = current_user()
    order = db.session.scalar(db.select(Order).filter_by(reference=reference))
    if order is None:
        abort(404)
    if order.user_id != user.id and not user.is_admin:
        abort(403)

    lines = ["Item,Variant,Qty,Unit price,Line total"]
    for item in order.items:
        lines.append(
            f'"{item.product_name}","{item.variant_name or ""}",{item.quantity},'
            f'{item.unit_price:.2f},{item.line_total:.2f}'
        )
    lines.append("")
    lines.append(f"Subtotal,,,{order.subtotal:.2f}")
    if order.discount:
        lines.append(f"Discount ({order.coupon_code or 'applied'}),,,-{order.discount:.2f}")
    lines.append(f"Shipping,,,{order.shipping_cost:.2f}")
    lines.append(f"Tax,,,{order.tax:.2f}")
    lines.append(f"Total ({order.currency}),,,{order.total:.2f}")
    lines.append(f"Points earned,,,{order.points_earned}")
    lines.append(f"Points redeemed,,,{order.points_redeemed}")

    return Response(
        "\n".join(lines),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={order.reference}.csv"},
    )
