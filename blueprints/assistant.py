"""AI shopping assistant + natural-language search.

The assistant is powered by the store's own in-house engine (``ai.py``): pure
Python retrieval over the *real* catalogue, intent reading and templated
answers, so it needs no external service and is never "offline". It grounds
every answer strictly in products that exist and never invents a price or stock
level.

Natural-language search is a deterministic NLU pass (budgets, star ratings,
brands, categories -> real filters) so the listing page stays instant and the
exact filter contract never depends on anything remote.
"""

import re
import time

from flask import Blueprint, current_app, jsonify, request

import ai
from extensions import db
from helpers import cart_items, current_user, money_str, recommend_products
from models import Category, Product

assistant_bp = Blueprint("assistant", __name__)

_RATE_BUCKETS: dict[str, list[float]] = {}

# Where a service answer's "read more" link should go.
_TOPIC_LINKS = {
    "returns": ("/policies#returns", "Read the returns policy"),
    "warranty": ("/policies#warranty", "See warranty details"),
    "payments": ("/policies#payments", "Payment options"),
    "shipping": ("/policies#shipping", "Delivery details"),
    "privacy": ("/policies#privacy", "Privacy policy"),
    "tracking": ("/cart/track", "Track your order"),
    "support": ("/account/support", "Open a support ticket"),
}

# How many active products to load as the assistant's working set per request.
_POOL_LIMIT = 4000


def _rate_limited() -> bool:
    key = request.remote_addr or "?"
    now = time.time()
    window = current_app.config.get("AI_RATE_WINDOW", 15)
    limit = current_app.config.get("AI_RATE_LIMIT", 8)
    bucket = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    _RATE_BUCKETS[key] = bucket
    return False


# ------------------------------------------------------------------ context


def _catalogue_pool(product_slug: str | None):
    """Load the products the engine may answer from, plus shopper context."""
    page = None
    if product_slug:
        page = db.session.scalar(db.select(Product).filter_by(slug=product_slug))
        if page is not None and not page.is_active:
            page = None

    pool = db.session.scalars(
        db.select(Product).where(Product.is_active.is_(True)).limit(_POOL_LIMIT)
    ).all()

    user = current_user()
    cart_names = []
    user_name = None
    if user:
        cart_names = [i.product.name for i in cart_items(user) if i.product]
        if user.full_name:
            user_name = user.full_name.split()[0]

    return list(pool), page, cart_names, user_name


def _ask(question: str, product_slug: str | None) -> dict:
    pool, page, cart_names, user_name = _catalogue_pool(product_slug)
    result = ai.compose_answer(
        question, pool, page_product=page, cart_names=cart_names,
        user_name=user_name, fmt=money_str,
    )
    topic = result.pop("topic", None)
    if topic and topic in _TOPIC_LINKS:
        href, label = _TOPIC_LINKS[topic]
        result["href"] = href
        result["link_text"] = label
    return result


@assistant_bp.post("/assistant/chat")
def assistant_chat():
    """JSON chat endpoint used by the floating assistant widget."""
    if _rate_limited():
        return jsonify({"error": "slow_down",
                        "reply": "You're asking a lot at once — give me a few seconds."}), 429
    payload = request.get_json(silent=True) or {}
    messages = payload.get("messages") or []
    if not messages:
        return jsonify({"error": "empty", "reply": "Type a question to get started."}), 400
    question = messages[-1].get("content", "").strip() if isinstance(messages[-1], dict) else ""
    if not question:
        return jsonify({"error": "empty", "reply": "That message came through empty."}), 400

    product_slug = (payload.get("product_slug") or "").strip() or None
    result = _ask(question[:400], product_slug)
    result["slug"] = result.pop("product_slug", None)
    result["model"] = ai.ENGINE_NAME
    result.setdefault("sources", "catalogue")
    return jsonify(result)


@assistant_bp.get("/assistant/status")
def assistant_status():
    """Health check for the widget: the in-house engine is always online."""
    return jsonify(ai.status())


@assistant_bp.get("/assistant/recommend")
def assistant_recommend():
    """Explainable 'you might also like' picks for a product page."""
    slug = (request.args.get("slug") or "").strip()
    if not slug and request.is_json:
        slug = str((request.get_json(silent=True) or {}).get("slug") or "").strip()
    if not slug:
        return jsonify({"engine": ai.ENGINE_NAME, "items": []})

    anchor = db.session.scalar(db.select(Product).filter_by(slug=slug))
    if anchor is None:
        return jsonify({"engine": ai.ENGINE_NAME, "items": []}), 404

    picks = recommend_products([anchor.id], exclude_ids=[anchor.id], limit=8)
    items = [
        {
            "slug": p.slug,
            "name": p.name,
            "price": money_str(p.price),
            "reason": ai.recommend_reason(anchor, p),
        }
        for p in picks[:3]
    ]
    return jsonify({
        "engine": ai.ENGINE_NAME,
        "anchor": anchor.name,
        "why": f"Because you're viewing {anchor.name}",
        "items": items,
    })


# ----------------------------------------------------- natural-language search


_BUDGET_RE = re.compile(
    r"(?:under|below|within|less\s*than|up\s*to|budget\s*(?:of)?|max(?:imum)?\s*budget)"
    r"\s*([₹$€£]?\s*[\d][\d,]*(?:\.\d+)?\s*(?:lakh|lac|crore|cr|k)?)",
    re.I,
)
_RATING_RE = re.compile(r"(?:(\d)(?:\.(\d))?\s*\+?\s*star(?:s)?|rated\s+(\d)(?:\.(\d))?)", re.I)
_CUR_HINT_RE = re.compile(r"(?:₹|inr|rupees|\brs\.?|\brs\b)", re.I)
_STOP = {
    "under", "below", "within", "less", "than", "up", "to", "budget", "of",
    "max", "maximum", "best", "good", "with", "for", "and", "the", "a", "an",
    "star", "stars", "starred", "rated", "rating", "rupees", "rs", "inr", "usd",
    "show", "find", "looking", "want", "get", "buy", "affordable",
}
_INR_PER_USD = 83.20


def _price_band_for(max_usd: float):
    """Map a ceiling (USD) onto the catalogue's price-band codes."""
    if max_usd is None:
        return None
    for key, low, high in (("under-50", 0, 50), ("50-150", 50, 150),
                           ("150-400", 150, 400), ("over-400", 400, None)):
        if high is None:
            return key if max_usd >= low else None
        if max_usd < high:
            return key
    return None


def parse_search(query: str) -> dict | None:
    """Turn 'gaming laptop under 50,000 with RGB' into real catalogue filters.

    Returns None for plain single-word or unfiltered queries so regular search
    behaviour (and its tests) never changes.
    """
    q = query.strip()
    words = [w for w in re.findall(r"[\w₹$€£.]+", q.lower()) if w]
    if len(words) < 2:
        return None

    out: dict = {}
    leftover = " " + q + " "

    # --- budget ceiling ---------------------------------------------------
    m = _BUDGET_RE.search(leftover)
    if m:
        raw = m.group(1).strip()
        amount = float(re.sub(r"[^\d.]", "", raw) or 0.0)
        if re.search(r"lakh|lac", raw, re.I):
            amount *= 100_000
        elif re.search(r"crore|cr\b", raw, re.I):
            amount *= 10_000_000
        elif re.search(r"k", raw, re.I):
            amount *= 1_000
        if "$" in leftover:
            currency = "USD"
        elif _CUR_HINT_RE.search(leftover):
            currency = "INR"
        elif amount >= 1000:
            currency = "INR"  # bare large figures read as rupees on this store
        else:
            currency = "USD"
        if currency == "INR":
            amount /= _INR_PER_USD
        band = _price_band_for(amount)
        if band:
            out["price"] = band
        leftover = _BUDGET_RE.sub(" ", leftover)

    # --- minimum star rating ---------------------------------------------
    m = _RATING_RE.search(leftover)
    if m:
        whole, frac, whole2, frac2 = m.groups()
        value = float(f"{whole2 or whole}.{frac2 or frac or '0'}")
        out["rating"] = max(1, min(5, int(round(value))))
        leftover = _RATING_RE.sub(" ", leftover)

    # --- category / brand names ------------------------------------------
    cat_map = {
        c.slug: c.name.lower()
        for c in db.session.scalars(db.select(Category)).all()
    }
    brand_map = [
        row[0]
        for row in db.session.execute(
            db.select(Product.brand).where(Product.is_active.is_(True)).distinct()
        ).all()
    ]
    for slug, label in cat_map.items():
        if label in leftover.lower():
            out["category"] = slug
            leftover = leftover.lower().replace(label, " ")
    for brand in brand_map:
        if brand.lower() in leftover.lower():
            out["brand"] = brand
            leftover = leftover.lower().replace(brand.lower(), " ")

    if not out:
        return None

    # --- clean remaining terms -------------------------------------------
    term_words = []
    for token in re.findall(r"[\w₹$€£.]+", leftover.lower()):
        if token in _STOP or token.isdigit() or re.fullmatch(r"[\d.,]+", token):
            continue
        term_words.append(token)
    if term_words:
        out["q"] = " ".join(term_words)[:80]
    return out
