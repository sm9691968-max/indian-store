"""AI shopping assistant + natural-language search.

The assistant answers from the *real* catalogue: it is given the products,
categories, cart and current page as context and is told never to invent
products or prices. The model talks to any OpenAI-wire-compatible local server
(Ollama by default) so the demo runs with no API keys. When the model is
unreachable it degrades to a deterministic catalogue match so the widget is
never dead.

Natural-language search is a deterministic NLU pass (budgets, star ratings,
brands, categories -> real filters) so the listing page stays instant and the
exact filter contract never depends on a model being online.
"""

import json
import re
import time
import urllib.error
import urllib.request

from flask import Blueprint, current_app, jsonify, request, url_for

from extensions import db
from models import Category, OrderItem, Product
from helpers import cart_items, current_user, money_str

assistant_bp = Blueprint("assistant", __name__)

# Known local models, tried in order (fast enough to answer demo questions).
DEFAULT_MODELS = ("dolphin-qwen:latest", "dolphin-phi:2.7b-v2.6", "llama3.2:3b")

_INFLIGHT: dict[str, list[float]] = {}

SYSTEM_PROMPT = (
    "You are the shopping assistant for 'Indian Store', an Indian online "
    "marketplace selling electronics. Answer ONLY from the catalogue context "
    "supplied with each request. Never invent a product, brand, price, stock "
    "level, discount or deal that is not in the context. If you do not know or "
    "the item is not listed, say so and suggest asking for one of the products "
    "in stock. Keep answers under 120 words, warm and practical, and mention "
    "the price with the symbol already shown in the context. If one of the "
    "linked products fits the question, say which one. Do not recommend "
    "anything out of stock."
)


def _llm_models():
    configured = (current_app.config.get("AI_MODEL") or "").strip()
    if configured:
        return [configured, *DEFAULT_MODELS]
    return list(DEFAULT_MODELS)


def _ollama_chat(messages: list[dict], timeout: float) -> str:
    """One streaming-off chat completion against the local AI server."""
    base = (current_app.config.get("AI_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    errors = []
    for model in _llm_models():
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.3, "num_ctx": 4096},
        }
        req = urllib.request.Request(
            f"{base}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = (body.get("message") or {}).get("content", "").strip()
            if content:
                return content
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                json.JSONDecodeError, OSError) as exc:
            errors.append(f"{model}: {exc}")
    raise RuntimeError("; ".join(errors) or "no AI backend reachable")


def _rate_limited() -> bool:
    key = request.remote_addr or "?"
    now = time.time()
    window = current_app.config.get("AI_RATE_WINDOW", 15)
    limit = current_app.config.get("AI_RATE_LIMIT", 8)
    bucket = [t for t in _INFLIGHT.get(key, []) if now - t < window]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    _INFLIGHT[key] = bucket
    return False


# ------------------------------------------------------------------ context


def _catalogue_context(product_slug: str | None = None) -> tuple[str, list[dict]]:
    """Build a compact, factual description of what the shopper can buy."""
    lines: list[str] = []
    links: list[dict] = []

    if product_slug:
        product = db.session.scalar(db.select(Product).filter_by(slug=product_slug))
        if product and product.is_active:
            lines.append(
                f"Currently viewing: {product.name} by {product.brand} — "
                f"{money_str(product.price)} — rating {product.rating}/5 from "
                f"{product.review_count} reviews — {'in stock' if product.stock else 'OUT OF STOCK'}."
            )
            if product.highlight_list:
                lines.append("Highlights: " + "; ".join(product.highlight_list[:5]))
            lines.append(f"Warranty {product.warranty_years}y, 30-day returns.")
            links.append({"name": product.name, "price": money_str(product.price),
                          "slug": product.slug})

    deals = db.session.scalars(
        db.select(Product)
        .where(Product.is_active.is_(True), Product.compare_at.isnot(None),
               Product.compare_at > Product.price, Product.stock > 0)
        .order_by(Product.sold_count.desc()).limit(4)
    ).all()
    sellers = db.session.scalars(
        db.select(Product)
        .where(Product.is_active.is_(True), Product.stock > 0)
        .order_by(Product.sold_count.desc()).limit(8)
    ).all()

    if deals:
        lines.append("Best sellers on sale:")
        for p in deals:
            lines.append(
                f"- {p.name} ({p.brand}) {money_str(p.price)}, was {money_str(p.compare_at)}, "
                f"{p.discount_pct}% off, rating {p.rating}."
            )
            links.append({"name": p.name, "price": money_str(p.price), "slug": p.slug})
    if sellers:
        lines.append("Popular right now:")
        for p in sellers:
            lines.append(
                f"- {p.name} ({p.brand}) {money_str(p.price)}, rating {p.rating}, "
                f"{'in stock' if p.stock else 'out of stock'}."
            )
            links.append({"name": p.name, "price": money_str(p.price), "slug": p.slug})

    cats = db.session.scalars(
        db.select(Category).order_by(Category.name)
    ).all()
    if cats:
        lines.append("Categories: " + ", ".join(c.name for c in cats) + ".")

    user = current_user()
    if user:
        cart = cart_items(user)
        if cart:
            names = [f"{i.product.name} x{i.quantity}" for i in cart if i.product]
            lines.append(f"The shopper has in cart: {', '.join(names)}.")
        lines.append(f"Shopper name: {user.full_name}.")

    # Deduplicate links, keep the first price seen.
    seen: dict[str, dict] = {}
    for link in links:
        seen.setdefault(link["slug"], link)
    return "\n".join(lines), list(seen.values())[:6]


def _catalogue_fallback(question: str) -> dict:
    """Deterministic reply when the AI backend is unavailable."""
    words = re.findall(r"[\w]{3,}", question.lower())[:4]
    if not words:
        return {"reply": "Tell me what you're after — a product, a category or a deal.",
                "product": None}
    matches = db.session.scalars(
        db.select(Product).where(Product.is_active.is_(True), Product.stock > 0)
    ).all()
    scored = []
    for p in matches:
        hay = f"{p.name} {p.brand} {p.description}".lower()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, p.sold_count, p))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    top = [row[2] for row in scored[:3]]
    if not top:
        return {
            "reply": ("I'm having trouble reaching my brain right now, but the "
                      "catalogue is fully loaded — try asking about best sellers "
                      "or what's on sale, or use the search bar above."),
            "product": None,
        }
    names = ", ".join(p.name for p in top)
    return {
        "reply": f"Right now these fit best: {names}. Prices are live on each "
                 f"product page — tap the links below.",
        "product": top[0],
    }


def _ask(question: str, product_slug: str | None) -> dict:
    context, links = _catalogue_context(product_slug)
    user_msg = f"Catalogue context:\n{context}\n\nShopper asks: {question}"
    try:
        reply = _ollama_chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            timeout=current_app.config.get("AI_TIMEOUT", 45),
        )
        return {"reply": reply, "product": links[0] if links else None,
                "context": links}
    except RuntimeError:
        fallback = _catalogue_fallback(question)
        fallback["context"] = links
        return fallback


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
    result["sources"] = "catalogue"
    prod = result.pop("product", None)
    result["slug"] = (
        prod.get("slug")
        if isinstance(prod, dict)
        else (prod.slug if prod is not None else None)
    )
    return jsonify(result)


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