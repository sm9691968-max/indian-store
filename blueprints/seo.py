"""SEO surface: sitemap, robots.txt, JSON-LD and the social metadata shim."""

import json

from flask import Blueprint, Response, render_template, request, url_for, abort
from sqlalchemy import func

from extensions import db
from models import Category, Product, ORDER_FLOW, CANCELLED

seo_bp = Blueprint("seo", __name__)

SITE_NAME = "Indian Store"
SITE_DESCRIPTION = (
    "Indian Store is a curated marketplace for premium keyboards, audio, displays and desk gear, "
    "with verified reviews, real-time stock and free delivery over $150."
)


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

    urls = [(url_for("catalog.index", _external=True), "daily", "1.0")]
    urls += [
        (url_for("catalog.recently_viewed", _external=True), "daily", "0.4"),
        (url_for("catalog.compare", _external=True), "weekly", "0.3"),
    ]
    urls += [
        (url_for("catalog.index", category=c.slug, _external=True), "weekly", "0.7")
        for c in categories
    ]
    urls += [
        (url_for("catalog.product_detail", slug=p.slug, _external=True), "weekly", "0.8")
        for p in products
    ]

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, freq, priority in urls:
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
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
    }


@seo_bp.app_template_filter("json_ld")
def json_ld(payload: dict) -> str:
    """Serialise schema.org data safely for a <script type="application/ld+json">."""
    return json.dumps(payload, separators=(",", ":"), default=str).replace("<", "\\u003c")


@seo_bp.app_template_filter("money_display")
def money_display(value) -> str:
    from helpers import convert, currency_symbol, currency_code

    amount = convert(value)
    return f"{currency_symbol()}{amount:,.2f}"
