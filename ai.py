"""Indian Store's own AI engine — self-contained and always on.

No external service, no API keys, no model download: this is the store's
in-house intelligence layer, written in pure Python. It performs retrieval over
the real catalogue, ranks results with a small TF-style relevance score plus a
synonym map, reads buying intent out of plain questions, and writes answers and
product copy with a templated generator grounded strictly in the products it is
given. Because it needs nothing but the catalogue it is deterministic, instant
and testable — and it can never be "offline".

The engine works on product-like objects (anything exposing name / brand /
price / rating / review_count / stock / sold_count / description / highlights /
slug and a category with a ``name``), so it stays DB-free and trivial to unit
test. Formatting money is the caller's job: pass ``fmt`` (e.g. ``money_str``).
"""

from __future__ import annotations

import re

ENGINE_NAME = "own-engine"
ENGINE_VERSION = "1.0"

# --------------------------------------------------------------------- text


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "and", "or", "with",
    "is", "are", "do", "does", "you", "your", "i", "me", "my", "me", "it",
    "this", "that", "these", "these", "have", "has", "have", "want", "need",
    "looking", "looking", "looking", "looking", "me", "please", "pls", "hi",
    "hey", "hello", "can", "could", "would", "show", "me", "some", "any",
    "good", "best", "good", "the", "a", "buy", "buying", "get",
}

# Groups of words that should be treated as the same idea when matching.
_SYNONYM_GROUPS = (
    ("laptop", "notebook", "ultrabook", "macbook", "chromebook"),
    ("phone", "smartphone", "mobile", "iphone", "android"),
    ("headphone", "headphones", "earphone", "earbuds", "headset", "tws", "earbud"),
    ("tv", "television", "smarttv", "television", "display", "screen"),
    ("monitor", "display", "screen", "monitor"),
    ("ssd", "nvme", "drive", "storage", "harddisk"),
    ("keyboard", "keypad", "keys"),
    ("mouse", "mice", "pointing"),
    ("speaker", "soundbar", "audio", "stereo"),
    ("watch", "smartwatch", "wearable", "fitness"),
    ("tablet", "ipad", "tab"),
    ("camera", "webcam", "dslr", "mirrorless", "lens"),
    ("charger", "adapter", "powerbank", "battery", "charging"),
    ("router", "wifi", "mesh", "networking", "ethernet"),
    ("printer", "scanner", "print"),
    ("gaming", "gamer", "console", "playstation", "xbox"),
    ("cable", "cables", "hdmi", "usb"),
    ("cooler", "cooling", "fan", "heatsink"),
    ("laptop", "notebook"),
    ("earbuds", "tws"),
)

_SYN: dict[str, set] = {}
for _group in _SYNONYM_GROUPS:
    for _word in _group:
        _SYN.setdefault(_word, set()).update(_group)


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and not word.endswith("ses"):
        return word[:-2]
    if len(word) > 2 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _norm_terms(*texts: str) -> set:
    """Stemmed, synonym-expanded token set for one or more strings."""
    out: set = set()
    for text in texts:
        for raw in _WORD_RE.findall((text or "").lower()):
            if len(raw) < 2 or raw in _STOP or raw.isdigit():
                continue
            for word in _SYN.get(raw, {raw}):
                stemmed = _stem(word)
                if len(stemmed) >= 2 and stemmed not in _STOP:
                    out.add(stemmed)
    return out


# ---------------------------------------------------------------- accessors


def _name(p) -> str:
    return (getattr(p, "name", "") or "").strip()


def _brand(p) -> str:
    return (getattr(p, "brand", "") or "").strip()


def _cat(p) -> str:
    cat = getattr(p, "category", None)
    if cat is None:
        return (getattr(p, "category_name", "") or "").strip()
    if isinstance(cat, str):
        return cat.strip()
    return (getattr(cat, "name", "") or "").strip()


def _price(p) -> float:
    try:
        return float(getattr(p, "price", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _float(p, attr: str, default: float = 0.0) -> float:
    try:
        return float(getattr(p, attr, default) or default)
    except (TypeError, ValueError):
        return default


def _highlights_text(p) -> str:
    hl = getattr(p, "highlight_list", None)
    if hl:
        return " ".join(str(h) for h in hl)
    return getattr(p, "highlights", "") or ""


def _slug(p) -> str:
    return getattr(p, "slug", "") or ""


def summarize(p, fmt=str) -> str:
    """A compact, factual one-liner about a product (never invents facts)."""
    bits = [_name(p)]
    if _brand(p) and _brand(p).lower() not in ("indian store",):
        bits.append(f"by {_brand(p)}")
    line = " ".join(bits)
    price = _price(p)
    if price:
        line += f" at {fmt(price)}"
    rating = _float(p, "rating")
    if rating:
        line += f", rated {rating:g}/5"
    line += " (in stock)" if _float(p, "stock") > 0 else " (out of stock)"
    return line


# ---------------------------------------------------- budget / rating parsing

_BUDGET_RE = re.compile(
    r"(?:under|below|within|less\s*than|up\s*to|budget\s*(?:of)?|max(?:imum)?\s*|"
    r"cheaper\s*than|no\s*more\s*than)\s*"
    r"([₹$€£]?\s*[\d][\d,]*(?:\.\d+)?\s*(?:lakh|lac|crore|cr|k)?)",
    re.I,
)
_RATING_RE = re.compile(r"(?:(\d)(?:\.(\d))?\s*\+?\s*star(?:s)?|rated\s+(\d)(?:\.(\d))?)", re.I)
_CUR_HINT_RE = re.compile(r"(?:₹|inr|rupees|\brs\.?|\brs\b)", re.I)
_INR_PER_USD = 83.20


def parse_budget(text: str) -> float | None:
    """Read a budget ceiling out of text and return it in base (USD) units."""
    m = _BUDGET_RE.search(" " + text + " ")
    if not m:
        return None
    raw = m.group(1).strip()
    amount = float(re.sub(r"[^\d.]", "", raw) or 0.0)
    if amount <= 0:
        return None
    if re.search(r"lakh|lac", raw, re.I):
        amount *= 100_000
    elif re.search(r"crore|cr\b", raw, re.I):
        amount *= 10_000_000
    elif re.search(r"k\b", raw, re.I):
        amount *= 1_000
    if "$" in text:
        currency = "USD"
    elif _CUR_HINT_RE.search(text) or amount >= 1000:
        currency = "INR"
    else:
        currency = "USD"
    return amount / _INR_PER_USD if currency == "INR" else amount


def parse_rating(text: str) -> int | None:
    m = _RATING_RE.search(text)
    if not m:
        return None
    whole, frac, whole2, frac2 = m.groups()
    value = float(f"{whole2 or whole}.{frac2 or frac or '0'}")
    return max(1, min(5, int(round(value))))


# ------------------------------------------------------------------- intent

_TOPICS = (
    ("returns", ("return", "refund", "send it back", "exchange", "replacement")),
    ("warranty", ("warranty", "guarantee", "guarantee", "repair", "serviced")),
    ("payments", ("payment", "emi", "cod", "cash on delivery", "upi", "upi",
                  "net banking", "netbanking", "debit", "credit card")),
    ("shipping", ("shipping", "delivery", "deliver", "dispatch", "ship", "courier",
                  "how long", "arrive", "arrive")),
    ("tracking", ("track", "tracking", "order status", "where is my order",
                  "where is my order", "shipped")),
    ("loyalty", ("loyalty", "points", "reward", "redeem")),
    ("coupon", ("coupon", "promo", "promo code", "discount code")),
    ("giftcard", ("gift card", "gift-card", "giftcard", "gift voucher")),
    ("support", ("help", "support", "contact", "talk to", "agent", "human",
                 "helpline", "customer care")),
)
_DEAL_RE = re.compile(r"\b(deal|deals|sale|discount|discounts|offer|offers|cheapest|cheap|bargain|clearance|lowest)\b", re.I)
_GREET_RE = re.compile(r"^\s*(hi|hey|hello|yo|namaste|good (morning|evening|afternoon|day))\b", re.I)
_THANKS_RE = re.compile(r"\b(thanks|thank you|thx|cheers|great|awesome|perfect)\b", re.I)
_COMPARE_RE = re.compile(r"\s+(?:vs\.?|versus|or|compared to|better than)\s+", re.I)
_FOCUS_RE = re.compile(r"\b(this|it|this one|that|the product|this product|the item)\b", re.I)


def support_topic(text: str) -> str | None:
    low = text.lower()
    for topic, keys in _TOPICS:
        if any(k in low for k in keys):
            return topic
    return None


def extract_intent(question: str) -> dict:
    q = (question or "").strip()
    terms = _norm_terms(q)
    return {
        "raw": q,
        "terms": terms,
        "terms_present": bool(terms),
        "budget_usd": parse_budget(q),
        "rating_min": parse_rating(q),
        "topic": support_topic(q),
        "wants_deal": bool(_DEAL_RE.search(q)),
        "greeting": bool(_GREET_RE.search(q)),
        "thanks": bool(_THANKS_RE.search(q)),
        "compare": bool(_COMPARE_RE.search(q)),
        "focus": bool(_FOCUS_RE.search(q)),
    }


# ------------------------------------------------------------------- ranking


def rank_products(question, products, limit: int = 6, *, budget_usd=None,
                  rating_min=None, in_stock_only: bool = True) -> list:
    """Relevance-rank a pool of products for a question.

    Returns ``[(product, score), ...]`` best first. Name matches outweigh brand,
    then category, then the body text; rating and popularity break ties.
    """
    qterms = question if isinstance(question, (set, frozenset)) else _norm_terms(question)
    if not qterms:
        return []
    scored = []
    for p in products or []:
        stock = _float(p, "stock")
        if in_stock_only and stock <= 0:
            continue
        price = _price(p)
        if budget_usd and price > budget_usd:
            continue
        if rating_min and _float(p, "rating") < rating_min:
            continue
        name_t = _norm_terms(_name(p))
        brand_t = _norm_terms(_brand(p))
        cat_t = _norm_terms(_cat(p))
        body_t = _norm_terms(getattr(p, "description", "") or "", _highlights_text(p))
        score = 0.0
        for term in qterms:
            if term in name_t:
                score += 6
            elif term in brand_t:
                score += 4
            elif term in cat_t:
                score += 3
            elif term in body_t:
                score += 1
        if score <= 0:
            continue
        score += _float(p, "rating") * 0.25 + min(_float(p, "sold_count"), 60) * 0.02
        scored.append((score, p))
    scored.sort(key=lambda row: (-row[0], -_float(row[1], "rating"), -_float(row[1], "sold_count")))
    return scored[:limit]


# ------------------------------------------------------- answer composition


def _deal_reply(pool, fmt) -> dict:
    deals = [p for p in pool if _float(p, "compare_at") > _price(p) > 0 and _float(p, "stock") > 0]
    deals.sort(key=lambda p: -(_float(p, "compare_at") - _price(p)))
    if not deals:
        return {"reply": ("Nothing is on a discount this second — but the best "
                          "sellers are always priced sharp. Ask for a category like "
                          "“gaming laptops” and I'll pull the current picks."),
                "engine": ENGINE_NAME}
    top = deals[:3]
    lines = []
    for p in top:
        was = _float(p, "compare_at")
        off = int(round((1 - _price(p) / was) * 100)) if was else 0
        lines.append(f"• {_name(p)} — {fmt(_price(p))} (was {fmt(was)}, {off}% off)")
    return {"reply": "Today's sharpest deals:\n" + "\n".join(lines) +
                     f"\n\n{_name(top[0])} is the biggest saving — tap below to see it.",
            "product_slug": _slug(top[0]), "engine": ENGINE_NAME}


def _product_list_reply(intent, ranked, fmt) -> dict:
    if not ranked:
        return {}
    lead = "Here's what fits"
    if intent["budget_usd"]:
        lead = f"Within that budget ({fmt(intent['budget_usd'])}) I'd look at"
    elif intent["rating_min"]:
        lead = f"Top-rated ({intent['rating_min']}+ stars) options"
    top = ranked[0][1]
    detail = [f"• {summarize(p, fmt)}" for _, p in ranked[:4]]
    reply = (f"{lead}:\n" + "\n".join(detail) +
             f"\n\nI'd start with {_name(top)} — {fmt(_price(top))}, "
             f"{_float(top,'rating'):g}/5 from {int(_float(top,'review_count'))} reviews.")
    return {"reply": reply, "product_slug": _slug(top), "engine": ENGINE_NAME,
            "candidates": [_slug(p) for _, p in ranked[:4]]}


def _nearest_budget_reply(intent, pool, fmt) -> dict:
    budget = intent["budget_usd"]
    in_stock = [p for p in pool if _float(p, "stock") > 0 and _price(p) > 0]
    if not in_stock:
        return {}
    in_stock.sort(key=lambda p: _price(p))
    close = [p for p in in_stock if _price(p) <= budget * 1.15][:3] or in_stock[:3]
    lines = "\n".join(f"• {summarize(p, fmt)}" for p in close)
    return {"reply": (f"I don't have a match under {fmt(budget)} right now, but "
                      f"the closest value picks are:\n{lines}\n\n"
                      f"Tell me a category (headphones, SSD, gaming) and I'll narrow it down."),
            "product_slug": _slug(close[0]), "engine": ENGINE_NAME}


def _focus_reply(page_product, fmt) -> dict:
    p = page_product
    reply = [f"{_name(p)} — {summarize(p, fmt)}."]
    hl = getattr(p, "highlight_list", None) or []
    if hl:
        reply.append("Highlights: " + "; ".join(str(h) for h in hl[:4]) + ".")
    reply.append(f"Warranty {int(_float(p,'warranty_years'))}y · 30-day returns. "
                 f"{'In stock, ready to ship.' if _float(p,'stock') else 'Currently sold out.'}")
    return {"reply": " ".join(reply), "product_slug": _slug(p), "engine": ENGINE_NAME}


def _compare_reply(question, ranked, fmt) -> dict:
    if len(ranked) < 2:
        return {}
    a, b = ranked[0][1], ranked[1][1]
    def side(p):
        return (f"{_name(p)} ({_brand(p) or 'in-house'}) — {fmt(_price(p))}, "
                f"{_float(p,'rating'):g}/5 · {_cat(p) or 'general'}")
    pick = a if _float(a, "rating") >= _float(b, "rating") else b
    return {"reply": (f"Here's the head-to-head:\n• {side(a)}\n• {side(b)}\n\n"
                      f"On ratings, {_name(pick)} edges it — but both are in stock, "
                      f"so it comes down to which spec you weigh more."),
            "product_slug": _slug(pick), "engine": ENGINE_NAME}


_SUPPORT_COPY = {
    "returns": "Returns are easy: 7-day free returns on most items, and unused products "
               "in original packaging get a full refund to the original payment method. "
               "Start it from Orders → Return.",
    "warranty": "Every product carries the manufacturer warranty shown on its page "
                "(usually 1–3 years). Keep the GST invoice we email you — that's all a "
                "service centre needs.",
    "payments": "You can pay by UPI, credit/debit card, net banking, no-cost EMI on "
                "eligible items, or Cash on Delivery. All online payments are secured.",
    "shipping": "Delivery is free over the threshold shown in the footer. Metro pincodes "
                "often get next-day; most of India lands in 2–5 days. You'll get tracking "
                "the moment it ships.",
    "tracking": "Pop your order reference into Track order and you'll see every step — "
                "packed, shipped, out for delivery.",
    "support": "You can raise a ticket from Account → Support and our team replies within "
               "a few hours. For order issues, the tracking page is the fastest route.",
}


def compose_answer(question, products, *, page_product=None, cart_names=(),
                   user_name=None, fmt=str) -> dict:
    """The in-house engine's answer to one shopper message.

    Grounded strictly in ``products`` (and ``page_product`` when relevant); it
    never invents stock, prices or specs. Returns a dict with ``reply`` plus an
    optional ``product_slug`` and (for support intents) ``topic``.
    """
    q = (question or "").strip()
    intent = extract_intent(q)
    pool = list(products or [])
    ranked = rank_products(intent["terms"], pool, limit=6,
                           budget_usd=intent["budget_usd"],
                           rating_min=intent["rating_min"]) if intent["terms_present"] else []

    # 1) Service questions are answered directly, no product needed.
    if intent["topic"] and not ranked:
        copy = _SUPPORT_COPY.get(intent["topic"])
        if copy:
            hello = f"{user_name}, " if user_name and intent["topic"] == "returns" else ""
            return {"reply": hello + copy, "topic": intent["topic"], "engine": ENGINE_NAME}
        return {"reply": "Let me point you the right way — try Account → Support, "
                         "or ask me about a product and I'll pull the live price.",
                "topic": intent["topic"], "engine": ENGINE_NAME}

    # 2) A question about the product the shopper is looking at.
    if page_product is not None and intent["focus"] and not intent["compare"]:
        return _focus_reply(page_product, fmt)

    # 3) Head-to-head comparison.
    if intent["compare"]:
        reply = _compare_reply(q, ranked, fmt)
        if reply:
            return reply

    # 4) Deals / bargains.
    if intent["wants_deal"] and not ranked:
        return _deal_reply(pool, fmt)

    # 5) Normal product search.
    reply = _product_list_reply(intent, ranked, fmt)
    if reply:
        return reply

    # 6) Budget given but nothing under it.
    if intent["budget_usd"]:
        reply = _nearest_budget_reply(intent, pool, fmt)
        if reply:
            return reply

    # 7) A product was named but not in the loaded pool.
    if intent["terms_present"]:
        return {"reply": ("I couldn't find that one in our catalogue right now. Try a "
                          "category (headphones, SSD, gaming, monitor) or a budget like "
                          "“under 20,000” and I'll list what's in stock."),
                "engine": ENGINE_NAME}

    # 8) Small talk.
    if intent["greeting"]:
        who = f" {user_name}" if user_name else ""
        return {"reply": f"Hi{who}! I know our whole catalogue and today's prices. "
                         "Ask for a category, a budget (“under 20,000”), a rating "
                         "(“4 stars and up”) or a deal.", "engine": ENGINE_NAME}
    if intent["thanks"]:
        return {"reply": "Any time. Want me to pull a few in-stock picks in the same "
                         "range?", "engine": ENGINE_NAME}

    return {"reply": "Tell me what you're shopping for — a category, a budget or a "
                     "product name — and I'll answer with live prices.",
            "engine": ENGINE_NAME}


# ------------------------------------------------------------------ copy lab


def _title_words(name: str) -> list:
    words = [w for w in re.findall(r"[A-Za-z0-9']+", name or "")]
    return [w for w in words if len(w) > 2][:4]


def describe_product(name: str, brand: str, category: str, price_text: str | None = None,
                     highlights=None) -> dict:
    """Write honest listing copy from only the facts we were handed.

    No spec, certification or number is invented: benefits come from what we
    already know about every Indian Store order (warehouse, invoices, returns).
    """
    name = (name or "This product").strip()
    brand = (brand or "").strip()
    cat = (category or "").strip().lower() or "electronics"
    by = f" from {brand}" if brand and brand.lower() not in ("indian store",) else ""

    lead = f"{name} is a smart pick in our {cat} range{by}."
    if price_text:
        lead = f"{name} is a strong pick in our {cat} range{by}, priced at {price_text}."
    body = ("It's built for everyday Indian use and backed by our 7-day returns "
            "and the manufacturer warranty printed on the label.")
    close = ("Every order ships from our Bengaluru warehouse with a GST invoice, "
             "and support is a message away if you need a hand.")
    description = " ".join([lead, body, close])

    bullets = [str(h).strip(" -•\t") for h in (highlights or []) if str(h).strip()]
    if not bullets:
        bullets = [
            "Ships from our Bengaluru warehouse",
            "Free delivery over the store threshold",
            "GST invoice included automatically",
            "7-day easy returns",
            "Pay by UPI, card, EMI or Cash on Delivery",
        ]
    return {"description": description, "highlights": bullets[:6], "engine": ENGINE_NAME}


def recommend_reason(anchor, pick) -> str:
    """A short, truthful reason why ``pick`` suits someone viewing ``anchor``."""
    if _cat(anchor) and _cat(anchor) == _cat(pick):
        return f"more in the same {_cat(pick)} family"
    if _brand(anchor) and _brand(anchor) == _brand(pick):
        return f"matches the {_brand(pick)} setup you're viewing"
    rating = _float(pick, "rating")
    reviews = int(_float(pick, "review_count"))
    if rating >= 4 and reviews:
        return f"rated {rating:g} stars by {reviews} buyers"
    if _float(pick, "compare_at") > _price(pick) > 0:
        return "on discount right now"
    return f"frequently bought with {_name(anchor)}"


# ------------------------------------------------------------------- status


def status() -> dict:
    """The UI's health check — the in-house engine is always available."""
    return {
        "online": True,
        "engine": ENGINE_NAME,
        "model": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "base": "in-process",
        "checked_ms": 0,
    }
