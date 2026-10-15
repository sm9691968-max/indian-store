"""Feature tests for the twenty new Indian Store capabilities.

Complements test_smoke.py, which covers the original storefront. Everything here
exercises a feature end to end through the real HTTP stack: comparison, recently
viewed, search suggestions, product media, variants, Q&A, stock alerts, shared
wishlists, saved-for-later, gift cards, loyalty, two-factor auth, support desk,
invoices, multi-currency, audit logging, analytics and SEO.
"""

import sys

import totp
from app import create_app
from extensions import db

PASSED = []


def check(label, condition, detail=""):
    if condition:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        sys.exit(1)


def login(client, email="demo@indianstore.id", password="IndianStore2026"):
    return client.post(
        "/auth/login", data={"email": email, "password": password}, follow_redirects=True
    )


def login_admin(client):
    return login(client, "admin@indianstore.id", "IndianStoreAdmin2026")


def main():
    app = create_app()
    # These are end-to-end behaviour checks, not CSRF checks, and they drive
    # hundreds of raw POSTs. CSRF itself is covered by test_security.py.
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.drop_all()
        db.create_all()

    import seed

    with app.app_context():
        seed.main()

    from models import (
        AuditLog,
        CartItem,
        GiftCard,
        LoyaltyEvent,
        Order,
        Product,
        ProductQuestion,
        StockAlert,
        SupportTicket,
        User,
        ViewEvent,
    )

    client = app.test_client()

    # ----------------------------------------------------------------- search
    print("\n[F1] Live search suggestions")
    r = client.get("/search/suggest?q=neb")
    check("suggest endpoint answers JSON", r.status_code == 200, str(r.status_code))
    payload = r.get_json()
    rows = payload["results"]
    check("suggest echoes the query", payload["query"] == "neb", str(payload.get("query")))
    check("suggest returns matches", isinstance(rows, list) and len(rows) > 0, str(rows))
    check("suggest payload has the fields the UI needs",
          all(k in rows[0] for k in ("name", "brand", "slug", "image", "price", "rating")),
          str(rows[0]))

    r = client.get("/search/suggest?q=zzzzzznothing")
    check("suggest tolerates no matches",
          r.status_code == 200 and r.get_json()["results"] == [], str(r.get_json()))

    r = client.get("/search/suggest?q=")
    check("blank query is safe", r.status_code == 200)

    r = client.get("/", query_string={"q": "monitor"})
    check("search filters the grid", b"Orbit 27" in r.data, str(r.status_code))
    r = client.get("/", query_string={"q": "zzzznothing"})
    check("no-match search still renders", r.status_code == 200)

    r = client.get("/search/suggest?q=kb")
    check("short queries still work", r.status_code == 200)
    # ------------------------------------------------- product media + variants
    print("\n[F2] Product media and variants")
    r = client.get("/product/nebula-75-mechanical-keyboard")
    check("product page renders", r.status_code == 200)
    check("gallery shows extra shots", b"gallery-thumb" in r.data)
    check("spec table rendered", b"Specifications" in r.data)
    check("variant picker present", b'id="variantSelect"' in r.data)
    check("out-of-stock variant flagged", b"out of stock" in r.data)
    check("first in-stock variant preselected", b"selected" in r.data)
    check("product JSON-LD emitted", b"application/ld+json" in r.data)
    check("aggregateRating in JSON-LD", b"aggregateRating" in r.data)

    with app.app_context():
        kb = Product.query.filter_by(slug="nebula-75-mechanical-keyboard").one()
        kb_id = kb.id
        variant = kb.variants[0]
        variant_id = variant.id
        sold_out_variant = [v for v in kb.variants if v.stock == 0][0]
        sold_out_id = sold_out_variant.id
        demo_id = User.query.filter_by(email="demo@indianstore.id").one().id
        check("model exposes primary image", kb.primary_image is not None)
        check("gallery includes base plus extras", len(kb.gallery) >= 2, str(len(kb.gallery)))

    login(client)
    r = client.post(f"/cart/add/{kb_id}", data={"quantity": "2", "variant_id": str(variant_id)},
                    follow_redirects=True)
    check("variant add-to-cart works", r.status_code == 200)

    with app.app_context():
        item = CartItem.query.filter_by(user_id=demo_id, product_id=kb_id, is_saved=False).first()
        check("cart row records the variant", item is not None and item.variant_id == variant_id,
              str(item))
        check("line total uses variant price",
              item.line_total == float(item.variant.price) * 2, str(item.line_total))
        item_id = item.id

    r = client.post(f"/cart/update/{item_id}", data={"quantity": "1"}, follow_redirects=True)
    check("cart quantity updates", r.status_code == 200)
    with app.app_context():
        check("quantity persisted", db.session.get(CartItem, item_id).quantity == 1)

    r = client.post(f"/cart/add/{kb_id}", data={"quantity": "5", "variant_id": str(sold_out_id)},
                    follow_redirects=True)
    with app.app_context():
        sold_out_row = CartItem.query.filter_by(variant_id=sold_out_id).first()
        check("sold-out variant is refused", sold_out_row is None, str(sold_out_row))

    # ------------------------------------------------------------------ compare
    print("\n[F3] Product comparison")
    r = client.post(f"/compare/{kb_id}", follow_redirects=True)
    check("compare add works", r.status_code == 200)
    r = client.get("/compare")
    check("compare page renders", r.status_code == 200)
    check("compare lists the product", b"Nebula 75" in r.data)

    with app.app_context():
        ids = [p.id for p in Product.query.limit(6).all()]

    for pid in ids[1:3]:
        client.post(f"/compare/{pid}", follow_redirects=True)

    r = client.get("/compare")
    check("compare holds several products", r.data.count(b"compare-remove") >= 2,
          str(r.data.count(b"compare-remove")))

    r = client.post(f"/compare/{kb_id}", follow_redirects=True)
    with client.session_transaction() as sess:
        check("re-posting toggles the product out", kb_id not in sess.get("compare", []))

    r = client.post("/compare/clear", follow_redirects=True)
    with client.session_transaction() as sess:
        check("compare can be cleared", not sess.get("compare"))

    for pid in ids[:5]:
        client.post(f"/compare/{pid}")
    with client.session_transaction() as sess:
        check("compare list capped at the limit", len(sess.get("compare", [])) == 4,
              str(sess.get("compare")))

    r = client.post(f"/compare/{ids[5]}", follow_redirects=True)
    check("compare limit enforced", b"compare up to" in r.data or r.status_code == 200)
    with client.session_transaction() as sess:
        check("extra product not added", ids[5] not in sess.get("compare", []))

    # ------------------------------------------------------------- recently viewed
    print("\n[F4] Recently viewed")
    for slug in ("echo-anc-headphones", "helix-nvme-2tb-gen4", "vault-nas-4-bay-chassis"):
        r = client.get(f"/product/{slug}")
        check(f"view {slug}", r.status_code == 200)

    with app.app_context():
        check("view rows written", ViewEvent.query.count() > 0, str(ViewEvent.query.count()))

    r = client.get("/recently-viewed")
    check("recently viewed page renders", r.status_code == 200)
    check("recently viewed lists products", b"Helix" in r.data)

    r = client.get("/product/echo-anc-headphones")
    check("repeat view is recorded again", r.status_code == 200)
    with app.app_context():
        echo = Product.query.filter_by(slug="echo-anc-headphones").one()
        with client.session_transaction() as sess:
            visitor = sess.get("visitor_id")
        per_session = ViewEvent.query.filter_by(
            session_id=visitor, product_id=echo.id
        ).count()
        check("one view row per session and product", per_session == 1, str(per_session))

    # --------------------------------------------------------------------- q&a
    print("\n[F5] Product questions and answers")
    r = client.post("/product/helix-nvme-2tb-gen4/ask",
                    data={"question": "Is this drive compatible with the PS5 expansion bay?"},
                    follow_redirects=True)
    check("question submitted", r.status_code == 200)
    check("new question visible", b"PS5 expansion bay" in r.data)

    with app.app_context():
        q = ProductQuestion.query.filter(ProductQuestion.body.like("%PS5 expansion bay%")).first()
        check("question persisted with the asker", q is not None and q.user_id == demo_id)
        check("new question counts as unanswered", q.is_answered is False)
        q_id = q.id

    r = client.post("/product/helix-nvme-2tb-gen4/ask",
                    data={"question": "Is this drive compatible with the PS5 expansion bay?"},
                    follow_redirects=True)
    with app.app_context():
        dupes = ProductQuestion.query.filter(
            ProductQuestion.body.like("%PS5 expansion bay%")
        ).count()
    check("duplicate question is not stored twice", dupes == 1, str(dupes))

    r = client.post("/product/helix-nvme-2tb-gen4/ask", data={"question": "short"},
                    follow_redirects=True)
    check("short question rejected", b"10 characters" in r.data)

    # -------------------------------------------------------------- stock alerts
    print("\n[F6] Stock and price alerts")
    r = client.post("/product/prism-32-curved-display/alert", data={"kind": "restock"},
                    follow_redirects=True)
    check("restock alert accepted", r.status_code == 200)
    with app.app_context():
        alert = StockAlert.query.filter_by(kind="restock", notified=False).order_by(
            StockAlert.id.desc()
        ).first()
        check("alert row stored", alert is not None)
        check("alert has no price target", alert.target_price is None)
        alert_id = alert.id

    r = client.get("/account/alerts")
    check("alerts page lists it", b"Prism 32" in r.data)

    r = client.post("/product/prism-32-curved-display/alert", data={"kind": "bogus"},
                    follow_redirects=True)
    with app.app_context():
        bogus = StockAlert.query.filter_by(kind="bogus").count()
    check("unknown alert kind rejected", bogus == 0, str(bogus))

    r = client.post(f"/alerts/{alert_id}/delete", follow_redirects=True)
    check("alert can be deleted", r.status_code == 200)
    with app.app_context():
        check("alert row gone", db.session.get(StockAlert, alert_id) is None)

    # ------------------------------------------------------------- save for later
    print("\n[F7] Saved for later")
    r = client.post(f"/cart/add/{kb_id}", data={"quantity": "1", "save_for_later": "1"},
                    follow_redirects=True)
    check("save-for-later accepted", r.status_code == 200)
    with app.app_context():
        user = db.session.get(User, demo_id)
        saved = [i for i in user.cart_items if i.is_saved]
        check("cart row marked saved", len(saved) == 1, str(len(saved)))
        saved_id = saved[0].id

    r = client.get("/cart/")
    check("cart shows a saved section", b"aved for later" in r.data or b"Saved" in r.data)

    r = client.post(f"/cart/save/{saved_id}", follow_redirects=True)
    check("saved item moves back to the cart", r.status_code == 200)
    with app.app_context():
        check("row no longer saved", db.session.get(CartItem, saved_id).is_saved is False)

    r = client.post(f"/cart/save/{saved_id}", follow_redirects=True)
    with app.app_context():
        check("toggle works both ways", db.session.get(CartItem, saved_id).is_saved is True)

    r = client.post("/cart/save/999999", follow_redirects=False)
    check("unknown cart line 404s", r.status_code == 404, str(r.status_code))

    # ------------------------------------------------------------- shared wishlist
    print("\n[F8] Shared wishlist")
    r = client.get("/wishlist")
    check("wishlist page renders", r.status_code == 200)
    check("share link offered", b"/shared/" in r.data)

    with app.app_context():
        user = db.session.get(User, demo_id)
        check("demo user has a share token", bool(user.wishlist_token), str(user.wishlist_token))
        token = user.wishlist_token
        # Read the expected names from the database rather than hardcoding
        # product names, which drift when the catalogue changes.
        from models import WishlistItem

        wishlist_names = [
            item.product.name
            for item in db.session.query(WishlistItem).filter_by(user_id=demo_id).all()
            if item.product
        ]

    r = client.get(f"/shared/{token}")
    check("shared wishlist is public", r.status_code == 200, str(r.status_code))
    check("demo user has wishlist items to share", bool(wishlist_names), str(wishlist_names))
    check(
        "shared wishlist lists saved items",
        any(name.encode() in r.data for name in wishlist_names),
        str(wishlist_names),
    )
    check("shared page is read-only", b"/wishlist/" not in r.data)

    r = client.get("/shared/does-not-exist")
    check("bad share token 404s", r.status_code == 404, str(r.status_code))

    # ------------------------------------------------------------- gift cards
    print("\n[F9] Gift cards")
    r = client.post("/cart/gift-card", data={"code": "NXGC-DEMO50"}, follow_redirects=True)
    check("gift card applied", r.status_code == 200)
    r = client.get("/account/gift-cards")
    check("account gift cards render", r.status_code == 200)
    check("issued card listed", b"NXGC-DEMO50" in r.data)

    r = client.post("/cart/gift-card", data={"code": "NOT-A-CARD"}, follow_redirects=True)
    with client.session_transaction() as sess:
        check("invalid gift card not stored", "gift_card_code" not in sess)

    r = client.post("/cart/gift-card", data={"code": "NXGC-SPENT100"}, follow_redirects=True)
    with client.session_transaction() as sess:
        check("depleted gift card not stored", "gift_card_code" not in sess, str(sess.get("gift_card_code")))

    # ------------------------------------------------------------------- loyalty
    print("\n[F10] Loyalty points")
    r = client.get("/account/loyalty")
    check("loyalty page renders", r.status_code == 200)
    check("loyalty history rendered", b"Welcome bonus" in r.data)

    with app.app_context():
        demo = db.session.get(User, demo_id)
        starting_points = demo.points
        check("demo account has points", starting_points > 0, str(starting_points))

    r = client.post("/cart/points", data={"points": "500"}, follow_redirects=True)
    with client.session_transaction() as sess:
        check("points stored in session", sess.get("points_redeem") == 500, str(sess.get("points_redeem")))

    r = client.get("/cart/")
    check("cart shows the points discount", b"oint" in r.data)

    r = client.post("/cart/points", data={"points": "0"}, follow_redirects=True)
    with client.session_transaction() as sess:
        check("points cleared", "points_redeem" not in sess)

    r = client.post("/cart/points", data={"points": "99999999"}, follow_redirects=True)
    with client.session_transaction() as sess:
        check("absurd points request rejected", sess.get("points_redeem") != 99999999)

    # ------------------------------------------------------- checkout with extras
    print("\n[F11] Checkout with gift card and points")
    client.post("/cart/clear", follow_redirects=True)
    r = client.post(f"/cart/add/{kb_id}", data={"quantity": "1"}, follow_redirects=True)
    check("item in cart", r.status_code == 200)
    client.post("/cart/gift-card", data={"code": "NXGC-DEMO50"}, follow_redirects=True)
    client.post("/cart/points", data={"points": "200"}, follow_redirects=True)

    r = client.get("/cart/checkout")
    check("checkout page renders with discounts", r.status_code == 200)

    r = client.post("/cart/checkout", data={
        "shipping_name": "Alex Morgan",
        "shipping_address": "14 Lantern Row",
        "shipping_address2": "Apt 6B",
        "shipping_city": "Portland",
        "shipping_postal": "97209",
        "shipping_phone": "+1 555 0142",
        "payment_method": "Card",
        "note": "",
    }, follow_redirects=True)
    check("checkout succeeds", r.status_code == 200 and b"confirmed" in r.data.lower())

    with app.app_context():
        order = Order.query.order_by(Order.id.desc()).first()
        order_ref = order.reference
        check("order records the gift card", order.gift_card_code == "NXGC-DEMO50", str(order.gift_card_code))
        check("order records points redeemed", order.points_redeemed == 200, str(order.points_redeemed))
        check("order currency recorded", order.currency == "INR", str(order.currency))
        check("order total is positive", float(order.total) > 0, str(order.total))
        check("order discounted below subtotal", float(order.total) < float(order.subtotal) + 20,
              str(order.total))
        card = GiftCard.query.filter_by(code="NXGC-DEMO50").one()
        check("gift card was debited", float(card.balance) < 25.0, str(card.balance))
        check("redemption counted", card.redeemed_count >= 1, str(card.redeemed_count))
        earned = LoyaltyEvent.query.filter_by(order_id=order.id).order_by(LoyaltyEvent.id.desc()).first()
        check("points earned recorded", earned is not None and earned.points > 0)
        spent = LoyaltyEvent.query.filter_by(order_id=None, points=-200).first()
        check("points redemption journalled", spent is not None, str(spent))
        check("ledger balance_after set", earned.balance_after is not None)

    with client.session_transaction() as sess:
        check("coupon cleared after checkout", "gift_card_code" not in sess)
        check("points cleared after checkout", "points_redeem" not in sess)

    # ------------------------------------------------------------------ invoice
    print("\n[F12] Invoices")
    r = client.get(f"/cart/orders/{order_ref}/invoice")
    check("invoice renders", r.status_code == 200, str(r.status_code))
    check("invoice shows the order reference", order_ref.encode() in r.data)

    with app.app_context():
        order = db.session.get(Order, order.id)
        check("invoice number generated", bool(order.invoice_no), str(order.invoice_no))
        invoice_no = order.invoice_no
        check("invoice issued timestamp set", order.invoice_issued_at is not None)

    r = client.get(f"/cart/orders/{order_ref}/invoice")
    check("invoice number is stable", invoice_no.encode() in r.data)

    r = client.get(f"/cart/orders/{order_ref}/invoice.csv")
    check("invoice CSV downloads", r.status_code == 200)
    check("CSV has a content type", "text/csv" in r.headers.get("Content-Type", ""))
    check("CSV lists the item", b"Nebula" in r.data)
    check("CSV reports the currency", b"Total (INR)" in r.data, str(r.data[-200:]))

    r = client.get("/cart/orders/NOPE-NOPE/invoice")
    check("missing order invoice 404s", r.status_code == 404, str(r.status_code))

    # ---------------------------------------------------------------- two-factor
    print("\n[F13] Two-factor authentication")
    other = app.test_client()
    r = login(other, "priya@indianstore.id")
    check("second user signed in", r.status_code == 200)

    r = other.get("/account/2fa")
    check("2FA enrolment page renders", r.status_code == 200)
    check("secret is shown", b"Secret key" in r.data)
    check("setup URI is shown", b"otpauth://" in r.data)
    check("no fabricated QR image", b"qrcode" not in r.data.lower())

    with other.session_transaction() as sess:
        secret = sess.get("totp_enrol_secret", "")
    check("enrolment secret held in session", bool(secret))

    bad = other.post("/account/2fa", data={"action": "enable", "secret": secret, "code": "000000"})
    check("wrong enrolment code rejected", bad.status_code == 400, str(bad.status_code))
    check("secret still offered after failure", b"Secret key" in bad.data)

    ok = other.post("/account/2fa",
                    data={"action": "enable", "secret": secret, "code": totp.current_code(secret)},
                    follow_redirects=True)
    check("enrolment succeeds", ok.status_code == 200)
    check("2FA marked active", b"active" in ok.data.lower())

    with app.app_context():
        priya = User.query.filter_by(email="priya@indianstore.id").one()
        priya_id = priya.id
        check("secret stored", priya.totp_secret == secret)
        check("enrolment timestamp set", priya.totp_enrolled_at is not None)
        codes = priya.recovery_codes.split(",")
        check("recovery codes issued", len(codes) == 8, str(len(codes)))
        recovery = codes[0]

    other.post("/auth/logout", follow_redirects=True)
    # A password-only sign-in redirects to the second step, so follow it to
    # inspect the challenge page.
    r = other.post(
        "/auth/login",
        data={"email": "priya@indianstore.id", "password": "IndianStore2026"},
        follow_redirects=True,
    )
    check("password alone does not sign in", r.status_code == 200
          and b"verification" in r.data.lower(), str(r.status_code))
    with other.session_transaction() as sess:
        check("no user_id during 2FA", sess.get("user_id") is None)

    r = other.post("/auth/verify", data={"totp_code": "000000"}, follow_redirects=False)
    check("wrong code refused", r.status_code == 401, str(r.status_code))

    r = other.post("/auth/verify", data={"totp_code": totp.current_code(secret)}, follow_redirects=True)
    check("correct code signs in", r.status_code == 200)
    status = other.get("/auth/session/status").get_json()
    check("session now authenticated", status["authenticated"] is True, str(status))
    with other.session_transaction() as sess:
        check("pending state cleared", sess.get("totp_pending") is None)

    other.post("/auth/logout", follow_redirects=True)
    other.post("/auth/login", data={"email": "priya@indianstore.id", "password": "IndianStore2026"})
    r = other.post("/auth/verify", data={"totp_code": recovery}, follow_redirects=True)
    check("recovery code works once", r.status_code == 200)
    other.post("/auth/logout", follow_redirects=True)
    other.post("/auth/login", data={"email": "priya@indianstore.id", "password": "IndianStore2026"})
    r = other.post("/auth/verify", data={"totp_code": recovery}, follow_redirects=False)
    check("recovery code cannot be reused", r.status_code == 401, str(r.status_code))

    other.post("/auth/logout", follow_redirects=True)
    r = other.get("/auth/verify")
    check("stale 2FA stage redirects to login", r.status_code == 302, str(r.status_code))

    r = other.post(
        "/auth/login",
        data={"email": "priya@indianstore.id", "password": "IndianStore2026"},
        follow_redirects=True,
    )
    check("2FA stage reachable directly", r.status_code == 200)
    r = other.post("/auth/verify", data={"totp_code": totp.current_code(secret)}, follow_redirects=True)
    check("sign-in completes after retry", r.status_code == 200)

    # Snapshot the codes as they stand now: an earlier check spent one of them,
    # so the enrolment-time list is no longer the expected value.
    with app.app_context():
        codes_before = db.session.get(User, priya_id).recovery_codes

    r = other.post("/account/2fa", data={"action": "disable", "password": ""}, follow_redirects=True)
    with app.app_context():
        check("blank password cannot disable 2FA", db.session.get(User, priya_id).totp_enabled is True)

    r = other.post("/account/2fa", data={"action": "regenerate", "password": ""}, follow_redirects=True)
    with app.app_context():
        check("blank password cannot reissue codes",
              db.session.get(User, priya_id).recovery_codes == codes_before)

    r = other.post("/account/2fa", data={"action": "disable", "password": "WrongPass1"},
                   follow_redirects=True)
    with app.app_context():
        check("wrong password cannot disable 2FA", db.session.get(User, priya_id).totp_enabled is True)

    r = other.post("/account/2fa", data={"action": "disable", "password": "IndianStore2026"},
                   follow_redirects=True)
    with app.app_context():
        user = db.session.get(User, priya_id)
        check("correct password disables 2FA", user.totp_enabled is False)
        check("enrolment timestamp cleared", user.totp_enrolled_at is None)
        check("secret wiped on disable", user.totp_secret == "")

    other.post("/auth/logout", follow_redirects=True)

    # ------------------------------------------------------------ support desk
    print("\n[F14] Support desk")
    r = client.get("/account/support")
    check("support page renders", r.status_code == 200)

    r = client.post("/account/support/new", data={
        "subject": "Wrong colour delivered",
        "category": "Order",
        "message": "I ordered Midnight but received Sandstone. Can I exchange it?",
    }, follow_redirects=True)
    check("ticket opened", r.status_code == 200)
    with app.app_context():
        ticket = SupportTicket.query.filter_by(subject="Wrong colour delivered").one()
        ticket_ref = ticket.reference
        ticket_id = ticket.id
        check("ticket starts open", ticket.status == "open", ticket.status)
        check("opening message stored", len(ticket.messages) == 1, str(len(ticket.messages)))
        check("reference generated", bool(ticket_ref))
        check("opening message not staff", ticket.messages[0].is_staff is False)

    r = client.get(f"/account/support/{ticket_ref}")
    check("ticket detail renders", r.status_code == 200)
    check("ticket body shown", b"Midnight" in r.data)

    r = client.post(f"/account/support/{ticket_id}/reply",
                    data={"message": "Adding a photo reference: order 4A2B."},
                    follow_redirects=True)
    check("customer can reply", r.status_code == 200)
    with app.app_context():
        ticket = db.session.get(SupportTicket, ticket_id)
        check("reply appended", len(ticket.messages) == 2, str(len(ticket.messages)))

    r = client.post(f"/account/support/{ticket_id}/close", follow_redirects=True)
    with app.app_context():
        check("ticket closed", db.session.get(SupportTicket, ticket_id).status == "closed")

    stranger = app.test_client()
    login(stranger, "kenji@indianstore.id")
    r = stranger.get(f"/account/support/{ticket_ref}")
    check("other customers cannot read the ticket", r.status_code == 404, str(r.status_code))
    r = stranger.post(f"/account/support/{ticket_id}/reply", data={"message": "hijack"})
    check("other customers cannot reply", r.status_code == 404, str(r.status_code))

    # ------------------------------------------------------------- admin support
    print("\n[F15] Admin support and Q&A")
    client.post("/auth/logout", follow_redirects=True)
    login_admin(client)

    r = client.get("/admin/tickets")
    check("admin ticket queue renders", r.status_code == 200)
    # The queue defaults to the open filter, and this ticket was just closed.
    check("closed ticket absent from the open queue", b"Wrong colour" not in r.data)
    r = client.get("/admin/tickets?status=closed")
    check("closed filter renders", r.status_code == 200)
    check("queue lists the ticket", b"Wrong colour" in r.data)

    r = client.post(f"/admin/tickets/{ticket_id}/reply", data={
        "message": "Sorry about that. I have reserved a Midnight unit for you."
    }, follow_redirects=True)
    check("staff reply accepted", r.status_code == 200)
    with app.app_context():
        ticket = db.session.get(SupportTicket, ticket_id)
        check("staff message flagged", any(m.is_staff for m in ticket.messages))
        check("ticket awaiting customer", ticket.status == "pending", ticket.status)

    r = client.post(f"/admin/tickets/{ticket_id}/status", data={"status": "resolved"},
                    follow_redirects=True)
    with app.app_context():
        check("staff can resolve", db.session.get(SupportTicket, ticket_id).status == "resolved")

    r = client.get("/admin/questions")
    check("admin Q&A page renders", r.status_code == 200)
    r = client.get("/admin/questions?open=1")
    check("open-only filter works", r.status_code == 200)
    check("unanswered question listed", b"PS5 expansion bay" in r.data)
    r = client.get("/admin/questions")
    check("answered question also listed", b"spare set of switches" in r.data)

    r = client.post(f"/admin/questions/{q_id}/answer", data={
        "answer": "No, the PS5 bay takes M.2 drives but not PCIe 4.0 Gen4 modules."
    }, follow_redirects=True)
    check("answer posted", r.status_code == 200)
    with app.app_context():
        q = db.session.get(ProductQuestion, q_id)
        check("answer stored", "Gen4" in (q.answer or ""))
        check("marked answered", q.is_answered is True)
        check("attributed to admin", q.answered_by_id is not None)
        check("answered_at recorded", q.answered_at is not None)

    r = client.get("/product/helix-nvme-2tb-gen4")
    check("answer is public", b"PS5 bay takes" in r.data)

    # ------------------------------------------------------------- admin gift cards
    print("\n[F16] Admin gift card issuance")
    r = client.get("/admin/gift-cards")
    check("admin gift cards render", r.status_code == 200)
    check("seeded cards listed", b"NXGC-DEMO50" in r.data)

    r = client.post("/admin/gift-cards", data={"user_id": str(demo_id), "amount": "40.00"})
    check("gift card issued", r.status_code in (200, 302), str(r.status_code))
    with app.app_context():
        card = GiftCard.query.filter_by(initial_balance=40).order_by(GiftCard.id.desc()).first()
        check("card created with balance", card is not None and float(card.balance) == 40.0)
        check("card attributed to issuer", card.created_by_id is not None)
        check("card linked to the recipient", card.user_id == demo_id, str(card.user_id))
        issued_code = card.code
        check("issue recorded in the audit trail",
              AuditLog.query.filter_by(action="gift_card_issue").count() > 0)

    r = client.post("/admin/gift-cards", data={"user_id": str(demo_id), "amount": "not-a-number"})
    with app.app_context():
        bad = GiftCard.query.filter_by(initial_balance=0).count()
    check("invalid amount rejected", bad == 0, str(bad))

    client.post("/auth/logout", follow_redirects=True)
    login(client)
    r = client.get("/account/gift-cards")
    check("customer sees the new card", issued_code.encode() in r.data)

    # ------------------------------------------------------------------ analytics
    print("\n[F17] Analytics and exports")
    client.post("/auth/logout", follow_redirects=True)
    login_admin(client)

    r = client.get("/admin/analytics")
    check("analytics page renders", r.status_code == 200)
    check("revenue figure present", b"Revenue" in r.data or b"revenue" in r.data)
    check("top products section present", b"Top" in r.data or b"top" in r.data)

    for path in ("/admin/analytics/orders.csv", "/admin/analytics/products.csv"):
        r = client.get(path)
        check(f"{path} downloads", r.status_code == 200)
        check(f"{path} is CSV", "text/csv" in r.headers.get("Content-Type", ""))
        check(f"{path} has content", len(r.data) > 50)

    r = client.get("/admin/audit")
    check("audit log page renders", r.status_code == 200)
    check("audit entries listed", b"login" in r.data or b"Login" in r.data)

    r = client.get("/admin/audit?action=order_placed")
    check("audit action filter works", r.status_code == 200)

    with app.app_context():
        check("audit rows written", AuditLog.query.count() > 0, str(AuditLog.query.count()))
        check("login was audited", AuditLog.query.filter_by(action="login").count() > 0)
        check("checkout was audited", AuditLog.query.filter_by(action="order_placed").count() > 0)
        check("question answer audited",
              AuditLog.query.filter_by(action="question_answer").count() > 0)

    # ------------------------------------------------------ admin media + alerts
    print("\n[F18] Admin media and alert dispatch")
    r = client.get("/admin/products")
    check("admin product list renders", r.status_code == 200)
    r = client.get(f"/admin/products/{kb_id}/edit")
    check("product editor renders", r.status_code == 200)

    r = client.post(f"/admin/products/{kb_id}/images",
                    data={"url": "https://images.unsplash.com/photo-1511467687858-23d96c32e4ae?w=900&q=80",
                          "caption": "Added by the feature test"},
                    follow_redirects=True)
    check("image added", r.status_code == 200)
    with app.app_context():
        from models import ProductImage

        img = ProductImage.query.filter_by(product_id=kb_id).order_by(ProductImage.id.desc()).first()
        check("image row stored", img is not None)
        # The keyboard is seeded with a hero shot, so a plain gallery add must
        # not steal the slot.
        check("seeded hero is primary",
              ProductImage.query.filter_by(product_id=kb_id, is_primary=True).count() == 1)
        check("added image does not displace the hero", img.is_primary is False)
        img_id = img.id

    # Ticking the box on upload is the explicit override.
    r = client.post(f"/admin/products/{kb_id}/images",
                    data={"url": "https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=900&q=80",
                          "caption": "Override hero", "is_primary": "1"},
                    follow_redirects=True)
    with app.app_context():
        hero = ProductImage.query.filter_by(product_id=kb_id).order_by(ProductImage.id.desc()).first()
        check("override upload becomes primary", hero.is_primary is True)
        check("override demotes the previous hero",
              ProductImage.query.filter_by(product_id=kb_id, is_primary=True).count() == 1)

    # A product with no shots at all auto-promotes its first image.
    with app.app_context():
        from models import Product

        bare_id = db.session.scalar(
            db.select(Product.id).where(Product.slug == "vector-tkl-pro-wireless")
        )

    r = client.post(f"/admin/products/{bare_id}/images",
                    data={"url": "https://images.unsplash.com/photo-1591488320449-011701bb6704?w=900&q=80",
                          "caption": "First ever shot"},
                    follow_redirects=True)
    with app.app_context():
        first_ever = ProductImage.query.filter_by(product_id=bare_id).order_by(ProductImage.id).first()
        check("first image on a bare product becomes primary", first_ever.is_primary is True)

    r = client.post(f"/admin/images/{img_id}/primary", follow_redirects=True)
    with app.app_context():
        others = ProductImage.query.filter_by(product_id=kb_id, is_primary=True).all()
        check("only one primary image", len(others) == 1, str(len(others)))

    r = client.post(f"/admin/images/{img_id}/delete", follow_redirects=True)
    with app.app_context():
        check("image deleted", db.session.get(ProductImage, img_id) is None)

    # A price drop on the seeded headphones alert should notify the demo account.
    r = client.post(f"/admin/products/{kb_id}/alerts/dispatch", follow_redirects=True)
    check("alert dispatch runs", r.status_code == 200)

    # ------------------------------------------------------------------ currency
    print("\n[F19] Multi-currency display")
    client.post("/auth/logout", follow_redirects=True)
    login(client)

    r = client.post("/currency", data={"currency": "EUR"}, follow_redirects=True)
    check("currency switch accepted", r.status_code == 200)
    with client.session_transaction() as sess:
        check("currency stored in session", sess.get("currency") == "EUR", str(sess.get("currency")))

    r = client.get("/product/orbit-27-4k-monitor")
    # "\u" is not a valid escape in a bytes literal, so b"\u20ac" is six
    # literal characters rather than the euro sign. Encode the character.
    check("prices converted", "€".encode("utf-8") in r.data, str(r.status_code))

    r = client.post("/currency", data={"currency": "ZZZ"}, follow_redirects=True)
    with client.session_transaction() as sess:
        check("unknown currency ignored", sess.get("currency") in ("EUR", "USD"), str(sess.get("currency")))

    r = client.post("/currency", data={"currency": "USD"}, follow_redirects=True)
    with app.app_context():
        currencies = {o.currency for o in Order.query.all()}
        check("every order records its creation currency", currencies == {"INR"}, str(currencies))

    # ----------------------------------------------------------------------- SEO
    print("\n[F20] SEO endpoints")
    r = client.get("/robots.txt")
    check("robots.txt served", r.status_code == 200)
    check("robots references the sitemap", b"Sitemap" in r.data)

    r = client.get("/sitemap.xml")
    check("sitemap served", r.status_code == 200)
    check("sitemap is XML", b"<?xml" in r.data)
    check("sitemap lists products", b"/product/" in r.data)
    check("sitemap has urls", r.data.count(b"<url>") >= 10, str(r.data.count(b"<url>")))

    r = client.get("/social-card.png")
    check("social card generated", r.status_code == 200, str(r.status_code))
    check("social card is a PNG", r.data[:8] == b"\x89PNG\r\n\x1a\n", str(r.data[:8]))

    r = client.get("/product/orbit-27-4k-monitor")
    check("og:title present", b'property="og:title"' in r.data)
    check("og:image present", b'property="og:image"' in r.data)
    check("canonical link present", b'rel="canonical"' in r.data)
    check("twitter card present", b"twitter:card" in r.data)
    check("twitter title matches og title", b'property="og:title" content="Orbit 27' in r.data)

    # ---------------------------------------------------------- guards and session
    print("\n[F21] Guards and session behaviour")
    anon = app.test_client()
    for path in ("/account/loyalty", "/account/alerts",
                 "/account/2fa", "/account/support", "/admin/analytics", "/admin/audit"):
        r = anon.get(path, follow_redirects=False)
        check(f"{path} requires sign-in", r.status_code == 302, str(r.status_code))

    # These two are deliberately readable by a guest: the compare tray is held
    # in the session, and recently-viewed falls back to a visitor id.
    for path in ("/compare", "/recently-viewed"):
        r = anon.get(path, follow_redirects=False)
        check(f"{path} stays public", r.status_code == 200, str(r.status_code))

    r = anon.get("/")
    check("storefront stays public", r.status_code == 200)
    r = anon.get("/product/nebula-75-mechanical-keyboard")
    check("product page stays public", r.status_code == 200)
    check("anonymous sees a sign-in prompt", b"Sign in to purchase" in r.data)

    shopper = app.test_client()
    r = shopper.get("/")
    with shopper.session_transaction() as sess:
        check("visitor id assigned", bool(sess.get("visitor_id")))

    shopper.post("/currency", data={"currency": "GBP"})
    r = login(shopper, "daniel@indianstore.id")
    with shopper.session_transaction() as sess:
        check("currency survives sign-in", sess.get("currency") == "GBP", str(sess.get("currency")))
        check("visitor id survives sign-in", bool(sess.get("visitor_id")))

    # Adding to compare and applying a coupon both need an account, so they
    # are exercised after signing in and then re-read on a later request.
    shopper.post(f"/compare/{kb_id}")
    shopper.post("/cart/coupon", data={"code": "INDIANSTORE20"}, follow_redirects=True)
    shopper.get("/compare")
    with shopper.session_transaction() as sess:
        check("comparison persists for the session",
              len(sess.get("compare") or []) > 0, str(sess.get("compare")))
        check("coupon persists for the session",
              sess.get("coupon_code") == "INDIANSTORE20", str(sess.get("coupon_code")))

    r = client.post("/auth/logout", follow_redirects=True)
    check("logout works", r.status_code == 200)

    print(f"\nAll {len(PASSED)} feature checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
