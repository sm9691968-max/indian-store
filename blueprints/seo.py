"""SEO surface: sitemap, robots.txt, JSON-LD and the social metadata shim."""

import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from flask import Blueprint, Response, render_template, request, url_for, abort
from markupsafe import Markup
from sqlalchemy import func

from extensions import db
from models import Category, Product, ORDER_FLOW, CANCELLED

seo_bp = Blueprint("seo", __name__)

SITE_NAME = "Indian Store"
SITE_DESCRIPTION = (
    "Indian Store is a curated marketplace for premium keyboards, audio, displays and desk gear, "
    "with verified reviews, real-time stock and free delivery over $150."
)

# Query parameters that change what is on the page. A filtered or sorted
# listing is the same document as the unfiltered one, so it must not be
# indexed as a separate URL.
_FILTER_PARAMS = frozenset({"category", "brand", "q", "stock", "price", "sort", "rating"})

# "page" is handled separately: page 1 is canonical, deeper pages are not, so
# the 168 pages of a 2,010-item catalogue do not compete with each other.
_PAGE = "page"


def canonical_url() -> str:
    """The one URL that should represent this page.

    Uses the full request path, unlike request.base_url, which drops the path
    entirely and made every product page claim to be the homepage. Filter and
    sort parameters are dropped so the 400+ filter permutations of one category
    collapse to a single canonical URL.
    """
    args = {key: value for key, value in request.args.items() if key not in _FILTER_PARAMS}

    # Drop ?page=1 but keep it for page 2+, so every page is reachable and the
    # first page does not duplicate the bare URL.
    page = args.get(_PAGE)
    if page in (None, "", "1"):
        args.pop(_PAGE, None)

    # request.endpoint is the rule name ("catalog.product_detail"), not the
    # view function's __name__. Path parameters (slug, category) come from
    # request.view_args, which url_for does not supply on its own.
    #
    # It is None on a 404 and inside the context processor of an error page,
    # so fall back to the literal path rather than letting url_for raise and
    # turn a missing page into a 500.
    if not request.endpoint:
        base = request.url_root.rstrip("/") + request.path
        return f"{base}?{urlencode(args)}" if args else base

    return url_for(request.endpoint, _external=True, **(request.view_args or {}), **args)


@seo_bp.get("/robots.txt")
def robots():
    body = "\n".join(
        [
            "User-agent: *",
            "Disallow: /account/",
            "Disallow: /cart/",
            "Disallow: /admin/",
            "Disallow: /auth/",
            "Allow: /",
            "",
            f"Sitemap: {url_for('seo.sitemap', _external=True)}",
            "",
        ]
    )
    return Response(body, mimetype="text/plain")


@seo_bp.get("/sitemap.xml")
def sitemap():
    products = db.session.scalars(
        db.select(Product).where(Product.is_active.is_(True))
    ).all()
    categories = db.session.scalars(db.select(Category).order_by(Category.slug)).all()

    urls = [
        (url_for("catalog.index", _external=True), "daily", "1.0"),
        (url_for("catalog.deals", _external=True), "daily", "0.7"),
        (url_for("catalog.recently_viewed", _external=True), "daily", "0.4"),
        (url_for("catalog.compare", _external=True), "weekly", "0.3"),
    ]
    # Only the bare category URL, not its filter permutations.
    urls += [
        (url_for("catalog.index", category=c.slug, _external=True), "weekly", "0.7")
        for c in categories
    ]
    urls += [
        (url_for("catalog.product_detail", slug=p.slug, _external=True), "weekly", "0.8")
        for p in products
    ]

    from flask import current_app
    from xml.sax.saxutils import escape

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, freq, priority in urls:
        parts.append("  <url>")
        # Ampersands in a query string must be escaped or the XML is invalid.
        parts.append(f"    <loc>{escape(loc)}</loc>")
        parts.append(f"    <lastmod>{now}</lastmod>")
        parts.append(f"    <changefreq>{freq}</changefreq>")
        parts.append(f"    <priority>{priority}</priority>")
        parts.append("  </url>")
    parts.append("</urlset>")

    return Response("\n".join(parts), mimetype="application/xml")


@seo_bp.get("/social-card.png")
def social_card():
    """A tiny generated PNG so /og:image never 404s during link previews."""
    import base64
    import zlib
    import struct

    width, height = 1200, 630
    # A dark slate card with a teal diagonal accent band.
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type: none
        for x in range(width):
            if 0 <= (x - y) < 40:
                r, g, b = 52, 211, 153
            elif 0 <= (x - y) < 46:
                r, g, b = 30, 41, 59
            else:
                r, g, b = 10, 14, 22
            rows += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )
    return Response(png, mimetype="image/png")


@seo_bp.app_context_processor
def seo_meta():
    """Every template can call json_ld() / og_image() for its page."""
    return {
        "site_name": SITE_NAME,
        "site_description": SITE_DESCRIPTION,
        "og_image": url_for("seo.social_card", _external=True),
        "canonical_url": canonical_url(),
    }


@seo_bp.app_template_filter("json_ld")
def json_ld(payload: dict):
    """Serialise schema.org data for a <script type="application/ld+json"> block.

    Marked safe so Jinja does not HTML-escape the quotes: an escaped
    "&quot;@context&quot;" is not valid JSON-LD and every crawler rejects it.
    The "<" replacement still guards against a "</script>" breakout, and
    U+2028/2029 are escaped because they are literal newlines in JavaScript.
    """
    text = json.dumps(payload, separators=(",", ":"), default=str)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return Markup(text)


@seo_bp.app_template_filter("money_display")
def money_display(value) -> str:
    from helpers import convert, currency_symbol, currency_code

    amount = convert(value)
    return f"{currency_symbol()}{amount:,.2f}"
