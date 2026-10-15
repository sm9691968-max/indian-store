"""End-to-end smoke test for the expanded Indian Store.

Covers auth, catalog filters, reviews, wishlist, cart, coupons, checkout,
order tracking, cancellation, account pages and the admin console.
"""

import re
import sys
from decimal import Decimal

from app import create_app
from extensions import db
from helpers import money_str

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

    from models import Coupon, Product, User  # noqa: F401

    client = app.test_client()

    # ------------------------------------------------------------ auth
    print("\n[1] Auth pages render")
    r = client.get("/auth/login")
    check("login page 200", r.status_code == 200)
    check("login form posts to /auth/login", b'action="/auth/login"' in r.data)
    check("showcase panel present", b"showcase-headline" in r.data)
    check("ambient canvas present", b'id="ambient-canvas"' in r.data)
    check("next hidden field present", b'name="next"' in r.data)

    r = client.get("/auth/register")
    check("register page 200", r.status_code == 200)
    check("register form action", b'action="/auth/register"' in r.data)
    check(
        "strength meter visible in signup",
        b'id="strengthMeter" style="display: block;"' in r.data,
    )

    print("\n[2] Registration validation")
    r = client.post(
        "/auth/register",
        data={
            "full_name": "A",
            "email": "bad",
            "password": "short",
            "confirm_password": "short",
            "accept_terms": "on",
        },
        follow_redirects=True,
    )
    check("weak signup rejected", r.status_code == 400)
    check("name error shown", b"Please enter your full name" in r.data)
    check("email error shown", b"valid email address" in r.data)
    check("password length error shown", b"at least 8 characters" in r.data)

    r = client.post(
        "/auth/register",
        data={
            "full_name": "A",
            "email": "sam@indianstore.id",
            "password": "IndianStore2026",
            "confirm_password": "IndianStore2026",
        },
        follow_redirects=True,
    )
    check("terms must be accepted", b"accept the terms" in r.data)

    r = client.post(
        "/auth/register",
        data={
            "full_name": "Alex Morgan",
            "email": "demo@indianstore.id",
            "password": "IndianStore2026",
            "confirm_password": "IndianStore2026",
            "accept_terms": "on",
        },
    )
    check("duplicate email rejected", r.status_code == 400)
    check("duplicate message", b"already exists" in r.data)

    print("\n[3] Registration success + next redirect")
    r = client.post(
        "/auth/register",
        data={
            "full_name": "Sam Rivera",
            "email": "sam@indianstore.id",
            "password": "IndianStore2026",
            "confirm_password": "IndianStore2026",
            "accept_terms": "on",
            "next": "/account/",
        },
    )
    check("signup honours next", r.headers.get("Location", "").endswith("/account/"),
          r.headers.get("Location", ""))
    client.post("/auth/logout", follow_redirects=True)

    print("\n[4] Login")
    r = client.get("/cart/", follow_redirects=False)
    check("signed-out cart redirects to login", r.status_code == 302)
    check("login redirect keeps next", b"next=/cart/" in r.headers.get("Location", "").encode())

    r = client.post("/auth/login", data={"email": "demo@indianstore.id", "password": "wrongpass"})
    check("bad password 401", r.status_code == 401)
    check("bad password message", b"do not match an account" in r.data)

    r = login(client, "DEMO@Indianstore.ID")
    check("login works (case-insensitive email)", r.status_code == 200)
    check("welcome back flash", b"Welcome back, Alex" in r.data)
    check("session status json", client.get("/auth/session/status").json["authenticated"] is True)

    # ------------------------------------------------------------ catalog
    print("\n[5] Catalog and filters")

    def as_text(page_html: bytes) -> str:
        return page_html.decode("utf-8", "replace")

    def cards(page_html: bytes) -> list[tuple[str, str]]:
        """Brand and category for every product card on the page."""
        return [
            (brand.strip(), category.strip())
            for brand, category in re.findall(
                r'class="product-cat">([^<]*)&middot; ([^<]*)<', as_text(page_html)
            )
        ]

    def prices(page_html: bytes) -> list[float]:
        # The value sits on its own line inside the span, so allow whitespace
        # and any supported currency symbol. A stricter pattern silently matched
        # nothing, which made the sort check compare [] == [] and always pass.
        return [
            float(m.replace(",", ""))
            for m in re.findall(
                r'class="price">\s*[$\u00a3\u20ac\u20b9\u00a5]([\d,]+\.\d\d)',
                as_text(page_html),
            )
        ]

    r = client.get("/")
    check("catalog 200", r.status_code == 200)
    listed = cards(r.data)
    check("storefront shows a full page", len(listed) == 12, f"got {len(listed)}")
    check("catalogue paginates", b'class="pagination"' in r.data)
    check("category chips rendered", b"Keyboards" in r.data and b"Displays" in r.data)

    r = client.get("/?category=audio")
    audio = cards(r.data)
    check("category filter works", bool(audio), "no results")
    check(
        "every result is in the filtered category",
        all(cat == "Audio" for _, cat in audio),
        str(sorted({cat for _, cat in audio})),
    )

    r = client.get("/?q=nvme")
    found = cards(r.data)
    check("search returns 1", len(found) == 1, f"got {len(found)}")

    r = client.get("/?stock=1")
    check("in-stock filter hides sold-out item", b"Prism 32 Curved Display" not in r.data)

    r = client.get("/?price=over-400")
    band = prices(r.data)
    check("price band filter", bool(band), "no results")
    check(
        "every result is over the band",
        all(p > 400 for p in band),
        str(band[:5]),
    )

    r = client.get("/?sort=price_desc")
    prices_desc = prices(r.data)
    check(
        "price_desc sorted",
        prices_desc == sorted(prices_desc, reverse=True),
        str(prices_desc[:5]),
    )

    r = client.get("/?brand=Resonant")
    resonant = cards(r.data)
    check("brand filter works", bool(resonant), "no results")
    check(
        "every result carries the filtered brand",
        all(brand == "Resonant" for brand, _ in resonant),
        str(sorted({b for b, _ in resonant})),
    )

    # ------------------------------------------------------------ product page
    print("\n[6] Product page and reviews")
    r = client.get("/product/nebula-75-mechanical-keyboard")
    check("product detail 200", r.status_code == 200)
    check("detail price shown", money_str(Decimal("189"), "INR").encode() in r.data)
    check("compare-at struck through", money_str(Decimal("229"), "INR").encode() in r.data)
    check("discount badge rendered", b"OFF" in r.data)
    check("highlights listed", b"Hot-swap sockets" in r.data)
    check("review section present", b"Customer reviews" in r.data)
    check("star breakdown rendered", b"rating-bars" in r.data)
    check("review form shown to members", b"Write a review" in r.data or b"Update your review" in r.data)
    check("related products shown", b"You might also like" in r.data)

    r = client.post(
        "/product/nebula-75-mechanical-keyboard/review",
        data={"rating": "5", "title": "Smoke test review", "body": "Written by the automated test suite."},
        follow_redirects=True,
    )
    check("review submitted", b"your review is live" in r.data or b"review was updated" in r.data)
    check("new review rendered", b"Smoke test review" in r.data)

    r = client.post(
        "/product/nebula-75-mechanical-keyboard/review",
        data={"rating": "5", "body": "too short"},
        follow_redirects=True,
    )
    check("short review rejected", b"at least 10 characters" in r.data)
    # One review card plus the prefilled edit form means a single row was stored.
    check("review stored once, not duplicated", r.data.count(b"Smoke test review") == 2,
          f"got {r.data.count(b'Smoke test review')}")
    check("form switches to update mode", b"Update your review" in r.data)

    with app.app_context():
        from models import Review

        row = db.session.scalar(
            db.select(Review).filter_by(title="Smoke test review")
        )
        review_id = row.id
    r = client.post(
        f"/product/nebula-75-mechanical-keyboard/review/{review_id}/delete",
        follow_redirects=True,
    )
    check("review deleted", b"Review deleted" in r.data)
    check("review gone from page", b"Smoke test review" not in r.data)

    r = client.get("/product/prism-32-curved-display")
    check("sold out product 404s when hidden?", r.status_code == 200)
    check("sold out messaging", b"out of stock" in r.data)

    r = client.get("/product/does-not-exist")
    check("404 on missing product", r.status_code == 404)

    # ------------------------------------------------------------ wishlist
    print("\n[7] Wishlist")
    r = client.get("/wishlist")
    check("wishlist page 200", r.status_code == 200)
    check("seeded wishlist items shown", r.data.count(b"product-card") == 3,
          f"got {r.data.count(b'product-card')}")

    with app.app_context():
        from models import Product as _P

        keyboards = db.session.scalars(
            db.select(_P).filter_by(slug="nebula-75-mechanical-keyboard")
        ).first()
        nebula_id = keyboards.id

    r = client.post(
        f"/wishlist/{nebula_id}/toggle",
        headers={"Accept": "application/json"},
    )
    check("wishlist toggle returns json", r.json.get("saved") is True, str(r.json))
    r = client.get("/wishlist")
    check("wishlist grew to 4", r.data.count(b"product-card") == 4,
          f"got {r.data.count(b'product-card')}")
    client.post(f"/wishlist/{nebula_id}/toggle")

    # ------------------------------------------------------------ cart
    print("\n[8] Cart")
    r = client.post(f"/cart/add/{nebula_id}", data={"quantity": "1"}, follow_redirects=True)
    check("add to cart works", b"added to your cart" in r.data)

    r = client.get("/cart/")
    check("cart page 200", r.status_code == 200)
    check("cart line rendered", b"lines</span>" in r.data or b"line</span>" in r.data)
    check("free shipping meter", b"unlocked free delivery" in r.data or b"more for free delivery" in r.data)

    with app.app_context():
        from models import CartItem

        line_id = db.session.scalar(db.select(CartItem.id).limit(1))
    r = client.post(f"/cart/update/{line_id}", data={"action": "set", "quantity": "2"},
                    follow_redirects=True)
    check("update quantity works", b'value="2"' in r.data)
    r = client.post(f"/cart/update/{line_id}", data={"action": "remove"}, follow_redirects=True)
    check("remove works", b"Your cart is empty" in r.data)

    # ------------------------------------------------------------ coupons
    print("\n[9] Coupons")
    with app.app_context():
        from models import Product as _P

        monitor_id = db.session.scalar(
            db.select(_P.id).filter_by(slug="orbit-27-4k-monitor")
        )
        numpad_id = db.session.scalar(
            db.select(_P.id).filter_by(slug="gaia-mechanical-numpad")
        )

    # LAUNCH50 needs a $400 minimum, so check it fails on a $79 cart.
    client.post(f"/cart/add/{numpad_id}", data={"quantity": "1"})
    r = client.post("/cart/coupon", data={"code": "LAUNCH50"}, follow_redirects=True)
    check("coupon needs a minimum subtotal", b"minimum subtotal" in r.data)

    r = client.post("/cart/coupon", data={"code": "NOPE"}, follow_redirects=True)
    check("invalid coupon rejected", b"coupon is not valid" in r.data)

    # Swap the cheap line for the $649 monitor and the same code must apply.
    with app.app_context():
        from models import CartItem as _CI

        small_line = db.session.scalar(
            db.select(_CI.id).where(_CI.product_id == numpad_id)
        )
    client.post(f"/cart/update/{small_line}", data={"action": "remove"})
    client.post(f"/cart/add/{monitor_id}", data={"quantity": "1"})
    r = client.post("/cart/coupon", data={"code": "LAUNCH50"}, follow_redirects=True)
    check("coupon accepted above the minimum", b"LAUNCH50" in r.data)
    check("fixed discount rendered", b"Discount (LAUNCH50)" in r.data)
    client.post("/cart/coupon/remove")

    r = client.post("/cart/coupon", data={"code": "INDIANSTORE20"}, follow_redirects=True)
    check("percent coupon applied", b"INDIANSTORE20" in r.data)
    check("discount line rendered", b"Discount (INDIANSTORE20)" in r.data)
    check("discount shown as negative", b"&minus;" + money_str(Decimal("129.80"), "INR").encode() in r.data)

    r = client.post("/cart/coupon/remove", follow_redirects=True)
    check("coupon removed", b"Coupon removed" in r.data)

    r = client.post("/cart/coupon", data={"code": "FREESHIP"}, follow_redirects=True)
    check("free shipping coupon applied", b"Free shipping" in r.data)
    check("shipping shown as FREE", b">FREE<" in r.data)
    client.post("/cart/coupon/remove")

    # ------------------------------------------------------------ checkout
    print("\n[10] Checkout")
    r = client.get("/cart/checkout")
    check("checkout 200", r.status_code == 200)
    check("order summary present", b"Place order" in r.data)
    check("saved addresses offered", b"Use a saved address" in r.data)

    r = client.post(
        "/cart/checkout",
        data={"shipping_name": "", "shipping_address": "", "shipping_city": "", "shipping_postal": ""},
        follow_redirects=True,
    )
    check("empty shipping rejected", b"Missing required field" in r.data)

    r = client.post(
        "/cart/checkout",
        data={
            "shipping_name": "Alex Morgan",
            "shipping_address": "42 Grid Street",
            "shipping_city": "Night City",
            "shipping_postal": "2077",
            "payment_method": "Card",
            "note": "Leave at the concierge.",
        },
        follow_redirects=True,
    )
    check("order placed", b"confirmed" in r.data)
    ref_match = re.search(rb"IS-[0-9A-F]{8}", r.data)
    check("order reference shown", ref_match is not None)
    reference = ref_match.group(0).decode()

    r = client.get("/cart/")
    check("cart emptied after checkout", b"Your cart is empty" in r.data)

    print("\n[11] Order tracking")
    r = client.get("/cart/orders")
    check("orders page 200", r.status_code == 200)
    check("order listed", reference.encode() in r.data)
    check("status pill rendered", b"status-processing" in r.data)
    check("progress bar rendered", b"Out for delivery" in r.data)

    r = client.get(f"/cart/orders/{reference}")
    check("order detail 200", r.status_code == 200)
    check("tracking timeline present", b"Tracking" in r.data)
    check("first event logged", b"Order received" in r.data)
    check("payment summary shown", b"Payment summary" in r.data)
    check("address block shown", b"Night City" in r.data)
    check("cancel button offered", b"Cancel order" in r.data)

    r = client.post(f"/cart/orders/{reference}/cancel", follow_redirects=True)
    check("order cancelled", b"cancelled" in r.data)
    check("cancelled timeline", b"status-cancelled" in r.data)
    r = client.post(f"/cart/orders/{reference}/cancel", follow_redirects=True)
    check("cannot cancel twice", b"no longer be cancelled" in r.data)

    with app.app_context():
        from models import Order
        from models import Product as _P

        order = db.session.scalar(db.select(Order).filter_by(reference=reference))
        stock_after_cancel = db.session.scalar(
            db.select(_P.stock).filter_by(slug="orbit-27-4k-monitor")
        )
    check("stock restored on cancel", stock_after_cancel == 12, f"got {stock_after_cancel}")
    check("cancelled status stored", order.status == "Cancelled")

    print("\n[12] Account pages")
    for path, needle in [
        ("/account/", b"Lifetime spend"),
        ("/account/profile", b"Marketing emails"),
        ("/account/addresses", b"Saved addresses"),
        ("/account/security", b"Change password"),
        ("/account/activity", b"Order activity"),
    ]:
        r = client.get(path)
        check(f"{path} renders", r.status_code == 200 and needle in r.data)

    r = client.post(
        "/account/profile",
        data={"full_name": "Alex Morgan", "email": "demo@indianstore.id", "phone": "+1 555 0142"},
        follow_redirects=True,
    )
    check("profile saved", b"Profile updated" in r.data)

    r = client.post(
        "/account/profile",
        data={"full_name": "Alex Morgan", "email": "priya@indianstore.id", "phone": ""},
        follow_redirects=True,
    )
    check("duplicate email blocked", b"already registered" in r.data)

    r = client.post(
        "/account/addresses",
        data={
            "label": "Workshop",
            "full_name": "Alex Morgan",
            "line1": "9 Foundry Lane",
            "line2": "",
            "city": "Seattle",
            "postal": "98101",
            "country": "United States",
            "is_default": "1",
        },
        follow_redirects=True,
    )
    check("address saved", b"Address saved" in r.data)
    check("new address listed", b"Foundry Lane" in r.data)
    with app.app_context():
        from models import Address

        new_address = db.session.scalar(db.select(Address).filter_by(label="Workshop"))
        address_id = new_address.id
        check("new address is default", new_address.is_default is True)

    r = client.post(f"/account/addresses/{address_id}/default", follow_redirects=True)
    check("default address toggled", r.status_code == 200)
    r = client.post(f"/account/addresses/{address_id}/delete", follow_redirects=True)
    check("address deleted", b"Address removed" in r.data)

    r = client.post(
        "/account/security",
        data={"current_password": "wrong", "new_password": "Newpass123",
              "confirm_password": "Newpass123"},
        follow_redirects=True,
    )
    check("wrong current password rejected", b"current password is incorrect" in r.data)

    r = client.post(
        "/account/security",
        data={"current_password": "IndianStore2026", "new_password": "Newpass123",
              "confirm_password": "Newpass123"},
        follow_redirects=True,
    )
    check("password changed", b"Password changed" in r.data)
    client.post("/auth/logout", follow_redirects=True)
    r = login(client, password="Newpass123")
    check("can sign in with new password", b"Welcome back, Alex" in r.data)

    # ------------------------------------------------------------ admin
    print("\n[13] Admin console")
    r = client.get("/admin/")
    check("non-admin blocked with 403", r.status_code == 403)
    check("403 page rendered", b"clearance required" in r.data)

    client.post("/auth/logout", follow_redirects=True)
    login(client, "admin@indianstore.id", "IndianStoreAdmin2026")

    for path, needle in [
        ("/admin/", b"Revenue"),
        ("/admin/products", b"New product"),
        ("/admin/orders", b"Update status"),
        ("/admin/coupons", b"Create a coupon"),
        ("/admin/users", b"Lifetime spend"),
        ("/admin/reviews", b"Reviews"),
    ]:
        r = client.get(path)
        check(f"{path} renders", r.status_code == 200 and needle in r.data)

    r = client.post(
        "/admin/products",
        data={"name": "Test Bench Mat XL", "price": "34.50", "stock": "12",
              "sku": "IS-TEST-1", "brand": "Indian Store", "description": "Created by the smoke test."},
        follow_redirects=True,
    )
    check("admin created product", b"created" in r.data)
    with app.app_context():
        from models import Product as _P

        created = db.session.scalar(
            db.select(_P).filter_by(sku="IS-TEST-1")
        )
        created_id = created.id
        created_slug = created.slug
    r = client.get(f"/product/{created_slug}")
    check("new product is live on the storefront", r.status_code == 200)

    r = client.get(f"/admin/products/{created_id}/edit")
    check("edit form loads", r.status_code == 200 and b"Test Bench Mat XL" in r.data)
    r = client.post(
        f"/admin/products/{created_id}/edit",
        data={"name": "Test Bench Mat XL", "price": "39.00", "stock": "5", "sku": "IS-TEST-1",
              "brand": "Indian Store", "description": "Updated by the smoke test.", "is_active": "1"},
        follow_redirects=True,
    )
    check("product updated", b"saved" in r.data)
    r = client.post(f"/admin/products/{created_id}/toggle", follow_redirects=True)
    check("product hidden", b"hidden" in r.data)
    check("hidden product 404s", client.get(f"/product/{created_slug}").status_code == 404)
    r = client.post(f"/admin/products/{created_id}/delete", follow_redirects=True)
    check("product deleted", b"deleted" in r.data)

    r = client.post(
        "/admin/coupons",
        data={"code": "TEST15", "description": "Smoke test coupon", "kind": "percent",
              "value": "15", "min_subtotal": "0", "max_uses": "0", "is_active": "1"},
        follow_redirects=True,
    )
    check("coupon created", b"TEST15" in r.data)
    with app.app_context():
        coupon = db.session.scalar(db.select(Coupon).filter_by(code="TEST15"))
        coupon_id = coupon.id
    r = client.post(f"/admin/coupons/{coupon_id}/toggle", follow_redirects=True)
    check("coupon paused", b"paused" in r.data)
    r = client.post(f"/admin/coupons/{coupon_id}/delete", follow_redirects=True)
    check("coupon deleted", b"deleted" in r.data)

    print("\n[14] Order fulfilment from admin")
    with app.app_context():
        from models import Order

        order = db.session.scalars(
            db.select(Order).where(Order.reference != reference)
            .order_by(Order.created_at.asc())
        ).first()
        target_id, target_ref = order.id, order.reference

    r = client.post(
        f"/admin/orders/{target_id}/status",
        data={"status": "Shipped", "note": "Moved by the smoke test."},
        follow_redirects=True,
    )
    check("status advanced to Shipped", b"now \xe2\x80\x9cShipped\xe2\x80\x9d" in r.data or b"Shipped" in r.data)
    with app.app_context():
        from models import Order as _O

        updated = db.session.get(_O, target_id)
        check("tracking number generated", bool(updated.tracking_number))
        check(
            "event note stored",
            any("Moved by the smoke test." in e.note for e in updated.events),
            str([e.note for e in updated.events]),
        )

    r = client.post(
        f"/admin/orders/{target_id}/status",
        data={"status": "Delivered", "note": ""},
        follow_redirects=True,
    )
    check("status advanced to Delivered", b"Delivered" in r.data)

    r = client.get("/admin/orders?status=Delivered")
    check("status filter works", target_ref.encode() in r.data)

    print("\n[15] Misc endpoints")
    check("cart count json", client.get("/cart/count").json == {"count": 0})
    status = client.get("/auth/session/status").json
    check("session status reports admin", status["is_admin"] is True)
    check("404 handler", client.get("/nope/nope").status_code == 404)

    client.post("/auth/logout", follow_redirects=True)
    r = client.get("/account/", follow_redirects=False)
    check("logout restores guards", r.status_code == 302)

    print(f"\nAll {len(PASSED)} checks passed.")


if __name__ == "__main__":
    main()
