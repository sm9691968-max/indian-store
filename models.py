from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.hybrid import hybrid_property
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db

ORDER_FLOW = ["Processing", "Packed", "Shipped", "Out for delivery", "Delivered"]
CANCELLED = "Cancelled"
CURRENCIES = {
    "USD": {"symbol": "$", "rate": Decimal("1.00"), "label": "US Dollar"},
    "EUR": {"symbol": "€", "rate": Decimal("0.92"), "label": "Euro"},
    "GBP": {"symbol": "£", "rate": Decimal("0.79"), "label": "British Pound"},
    "INR": {"symbol": "₹", "rate": Decimal("83.20"), "label": "Indian Rupee"},
    "JPY": {"symbol": "¥", "rate": Decimal("156.00"), "label": "Japanese Yen"},
}


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(32), default="", nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    remember = db.Column(db.Boolean, default=False, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    marketing_opt_in = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime(timezone=True))
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime(timezone=True))

    # Loyalty programme
    points = db.Column(db.Integer, default=0, nullable=False)

    # Two-factor authentication
    totp_secret = db.Column(db.String(64), default="", nullable=False)
    totp_enabled = db.Column(db.Boolean, default=False, nullable=False)
    totp_enrolled_at = db.Column(db.DateTime)
    recovery_codes = db.Column(db.String(400), default="", nullable=False)

    # Public share token for the wishlist
    wishlist_token = db.Column(db.String(32), default="", nullable=False)

    cart_items = db.relationship(
        "CartItem", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    orders = db.relationship(
        "Order", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    reviews = db.relationship(
        "Review", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    addresses = db.relationship(
        "Address",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Address.is_default.desc(), Address.id.asc()",
    )

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    @property
    def initials(self) -> str:
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    @property
    def default_address(self):
        for address in self.addresses:
            if address.is_default:
                return address
        return self.addresses[0] if self.addresses else None

    @property
    def cart_total(self) -> float:
        return sum(item.line_total for item in self.cart_items)

    @property
    def cart_count(self) -> int:
        return sum(item.quantity for item in self.cart_items if not item.is_saved)

    @property
    def saved_cart_count(self) -> int:
        return sum(item.quantity for item in self.cart_items if item.is_saved)

    @property
    def is_locked(self) -> bool:
        if self.locked_until is None:
            return False
        remaining = self.locked_until - utcnow()
        return remaining.total_seconds() > 0

    @property
    def lock_seconds_remaining(self) -> int:
        if not self.is_locked:
            return 0
        return int((self.locked_until - utcnow()).total_seconds())

    @property
    def total_spent(self) -> Decimal:
        return sum(
            (o.total for o in self.orders if o.status != CANCELLED), Decimal("0.00")
        )

    @property
    def active_items(self):
        return [item for item in self.cart_items if not item.is_saved]

    @property
    def saved_items(self):
        return [item for item in self.cart_items if item.is_saved]

    @property
    def loyalty_tier(self) -> tuple:
        if self.points >= 2500:
            return ("Obsidian", "#a78bfa")
        if self.points >= 1000:
            return ("Titanium", "#22d3ee")
        if self.points >= 300:
            return ("Platinum", "#94a3b8")
        return ("Member", "#34d399")

    @property
    def points_to_next_tier(self) -> int:
        if self.points >= 2500:
            return 0
        if self.points >= 1000:
            return 2500 - self.points
        if self.points >= 300:
            return 1000 - self.points
        return 300 - self.points


class Address(db.Model):
    __tablename__ = "addresses"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(40), default="Home", nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    line1 = db.Column(db.String(180), nullable=False)
    line2 = db.Column(db.String(180), default="", nullable=False)
    city = db.Column(db.String(80), nullable=False)
    postal = db.Column(db.String(20), nullable=False)
    country = db.Column(db.String(80), default="United States", nullable=False)
    phone = db.Column(db.String(32), default="", nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User", back_populates="addresses")

    @property
    def one_line(self) -> str:
        bits = [self.line1, self.line2, self.city, self.postal, self.country]
        return ", ".join(b for b in bits if b)


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    accent = db.Column(db.String(7), default="#e5a83b", nullable=False)
    blurb = db.Column(db.String(200), default="", nullable=False)

    products = db.relationship("Product", back_populates="category", lazy="selectin")


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(180), unique=True, nullable=False, index=True)
    sku = db.Column(db.String(40), default="", nullable=False)
    brand = db.Column(db.String(80), default="Indian Store", nullable=False, index=True)
    description = db.Column(db.Text, default="", nullable=False)
    highlights = db.Column(db.Text, default="", nullable=False)
    specs = db.Column(db.Text, default="", nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    compare_at = db.Column(db.Numeric(10, 2))
    cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    stock = db.Column(db.Integer, default=0, nullable=False)
    low_stock_threshold = db.Column(db.Integer, default=5, nullable=False)
    image_url = db.Column(db.String(400), default="", nullable=False)
    rating = db.Column(db.Numeric(2, 1), default=0.0, nullable=False)
    review_count = db.Column(db.Integer, default=0, nullable=False)
    sold_count = db.Column(db.Integer, default=0, nullable=False)
    badge = db.Column(db.String(40), default="", nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_featured = db.Column(db.Boolean, default=False, nullable=False)
    free_shipping = db.Column(db.Boolean, default=False, nullable=False)
    warranty_years = db.Column(db.Integer, default=2, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    category = db.relationship("Category", back_populates="products", lazy="selectin")

    reviews = db.relationship(
        "Review", back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    images = db.relationship(
        "ProductImage",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductImage.position.asc(), ProductImage.id.asc()",
    )
    variants = db.relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductVariant.position.asc(), ProductVariant.id.asc()",
    )
    questions = db.relationship(
        "ProductQuestion",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductQuestion.created_at.desc()",
    )

    @property
    def in_stock(self) -> bool:
        return self.stock > 0

    @property
    def is_low_stock(self) -> bool:
        return 0 < self.stock <= self.low_stock_threshold

    @property
    def stock_urgency(self) -> str:
        if self.stock <= 0:
            return "out"
        if self.stock <= self.low_stock_threshold:
            return "low"
        return "ok"

    @property
    def discount_pct(self) -> int:
        if self.compare_at and self.compare_at > self.price:
            return int(round((1 - (self.price / self.compare_at)) * 100))
        return 0

    @property
    def margin(self) -> Decimal:
        return Decimal(self.price) - Decimal(self.cost)

    @property
    def gallery(self) -> list:
        """Primary image first, then any extra gallery shots."""
        shots = [self.image_url] if self.image_url else []
        shots += [img.url for img in self.images if img.url not in shots]
        return shots

    @property
    def image(self) -> str:
        """The image shown on cards, falling back to a category glyph."""
        return self.image_url or ""

    @property
    def primary_image(self) -> "ProductImage | None":
        for shot in self.images:
            if shot.is_primary:
                return shot
        return self.images[0] if self.images else None

    @property
    def highlight_list(self) -> list:
        return [line.strip() for line in (self.highlights or "").split("\n") if line.strip()]

    @property
    def spec_rows(self) -> list:
        rows = []
        for line in (self.specs or "").split("\n"):
            if "|" in line:
                key, _, value = line.partition("|")
                if key.strip() and value.strip():
                    rows.append((key.strip(), value.strip()))
        return rows

    @property
    def in_stock_variants(self) -> list:
        return [v for v in self.variants if v.stock > 0]

    @property
    def answered_questions(self):
        return [q for q in self.questions if q.answer]

    def recalc_rating(self) -> None:
        ratings = [r.rating for r in self.reviews]
        self.review_count = len(ratings)
        self.rating = round(sum(ratings) / len(ratings), 1) if ratings else 0.0

    @property
    def rating_breakdown(self) -> dict:
        buckets = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        for review in self.reviews:
            if review.rating in buckets:
                buckets[review.rating] += 1
        return buckets


class ProductImage(db.Model):
    __tablename__ = "product_images"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(400), nullable=False)
    caption = db.Column(db.String(120), default="", nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)
    is_primary = db.Column(db.Boolean, default=False, nullable=False)

    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    product = db.relationship("Product", back_populates="images")


class ProductVariant(db.Model):
    __tablename__ = "product_variants"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(60), nullable=False)
    price_delta = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    stock = db.Column(db.Integer, default=0, nullable=False)
    sku_suffix = db.Column(db.String(20), default="", nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)

    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    product = db.relationship("Product", back_populates="variants")

    @property
    def name(self) -> str:
        return self.label

    @property
    def price(self) -> Decimal:
        return Decimal(self.product.price) + Decimal(self.price_delta)

    @property
    def in_stock(self) -> bool:
        return self.stock > 0

    @property
    def full_sku(self) -> str:
        return f"{self.product.sku}-{self.sku_suffix}" if self.sku_suffix else self.product.sku


class ProductQuestion(db.Model):
    __tablename__ = "product_questions"

    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, default="", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    answered_at = db.Column(db.DateTime(timezone=True))

    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    answered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    product = db.relationship("Product", back_populates="questions")
    user = db.relationship("User", foreign_keys=[user_id])
    answered_by = db.relationship("User", foreign_keys=[answered_by_id])

    @property
    def question(self) -> str:
        return self.body

    @hybrid_property
    def is_answered(self):
        """True once staff have replied. Hybrid so it also works in SQL filters."""
        return self.answer != ""

    @is_answered.expression
    def is_answered(cls):
        return cls.answer != ""

    @property
    def answered_by_name(self) -> str:
        return self.answered_by.full_name if self.answered_by else "Indian Store support"


class Review(db.Model):
    __tablename__ = "reviews"
    __table_args__ = (
        db.UniqueConstraint("user_id", "product_id", name="uq_review_user_product"),
    )

    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(140), default="", nullable=False)
    body = db.Column(db.Text, default="", nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    helpful_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    user = db.relationship("User", back_populates="reviews")
    product = db.relationship("Product", back_populates="reviews")

    @property
    def owner(self):
        return self.user


class WishlistItem(db.Model):
    __tablename__ = "wishlist_items"
    __table_args__ = (
        db.UniqueConstraint("user_id", "product_id", name="uq_wishlist_user_product"),
    )

    id = db.Column(db.Integer, primary_key=True)
    note = db.Column(db.String(160), default="", nullable=False)
    added_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    product = db.relationship("Product", lazy="selectin")


class StockAlert(db.Model):
    __tablename__ = "stock_alerts"
    __table_args__ = (
        db.UniqueConstraint("user_id", "product_id", "kind", name="uq_alert_user_product"),
    )

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(16), default="restock", nullable=False)  # restock | price
    target_price = db.Column(db.Numeric(10, 2))
    notified = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    product = db.relationship("Product", lazy="selectin")


class Coupon(db.Model):
    __tablename__ = "coupons"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    description = db.Column(db.String(160), default="", nullable=False)
    kind = db.Column(db.String(16), default="percent", nullable=False)  # percent | fixed | freeship
    value = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    min_subtotal = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    max_uses = db.Column(db.Integer, default=0, nullable=False)  # 0 = unlimited
    used_count = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires < utcnow()

    @property
    def is_exhausted(self) -> bool:
        return self.max_uses > 0 and self.used_count >= self.max_uses

    @property
    def is_redeemable(self) -> bool:
        return self.is_active and not self.is_expired and not self.is_exhausted

    def discount_for(self, subtotal: Decimal, shipping: Decimal) -> Decimal:
        if subtotal < self.min_subtotal:
            return Decimal("0.00")
        if self.kind == "percent":
            return (subtotal * (self.value / Decimal("100"))).quantize(Decimal("0.01"))
        if self.kind == "fixed":
            return min(self.value, subtotal)
        if self.kind == "freeship":
            return shipping
        return Decimal("0.00")

    @property
    def label(self) -> str:
        if self.kind == "percent":
            return f"{self.value.normalize():f}% off"
        if self.kind == "fixed":
            return f"${self.value:.2f} off"
        return "Free shipping"


class GiftCard(db.Model):
    __tablename__ = "gift_cards"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    initial_balance = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    balance = db.Column(db.Numeric(10, 2), nullable=False)
    redeemed_count = db.Column(db.Integer, default=0, nullable=False)
    currency = db.Column(db.String(3), default="USD", nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    user = db.relationship("User", foreign_keys=[user_id])
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def is_spent(self) -> bool:
        return Decimal(self.balance) <= 0

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires < utcnow()

    @property
    def is_redeemable(self) -> bool:
        return self.is_active and not self.is_spent and not self.is_expired


class LoyaltyEvent(db.Model):
    __tablename__ = "loyalty_events"

    id = db.Column(db.Integer, primary_key=True)
    points = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(160), default="", nullable=False)
    balance_after = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"))

    user = db.relationship("User")
    order = db.relationship("Order")


class CartItem(db.Model):
    __tablename__ = "cart_items"
    # One row per product *per variant*, so two colours of the same keyboard
    # can sit in the basket at once.
    __table_args__ = (
        db.UniqueConstraint("user_id", "product_id", "variant_id", name="uq_cart_user_product"),
    )

    id = db.Column(db.Integer, primary_key=True)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    is_saved = db.Column(db.Boolean, default=False, nullable=False)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"))
    added_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    user = db.relationship("User", back_populates="cart_items")
    product = db.relationship("Product", lazy="selectin")
    variant = db.relationship("ProductVariant", lazy="selectin")

    @property
    def unit_price(self) -> Decimal:
        return self.variant.price if self.variant else Decimal(self.product.price)

    @property
    def line_total(self) -> float:
        return float(self.unit_price) * self.quantity

    @property
    def in_stock(self) -> bool:
        return (self.variant.stock if self.variant else self.product.stock) >= self.quantity

    @property
    def max_quantity(self) -> int:
        return self.variant.stock if self.variant else self.product.stock


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(24), unique=True, nullable=False, index=True)
    subtotal = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    discount = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    shipping_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    tax = db.Column(db.Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    points_redeemed = db.Column(db.Integer, default=0, nullable=False)
    points_earned = db.Column(db.Integer, default=0, nullable=False)
    coupon_code = db.Column(db.String(32), default="", nullable=False)
    gift_card_code = db.Column(db.String(20), default="", nullable=False)
    status = db.Column(db.String(32), default="Processing", nullable=False)
    payment_method = db.Column(db.String(32), default="Card", nullable=False)
    currency = db.Column(db.String(3), default="USD", nullable=False)
    carrier = db.Column(db.String(40), default="Indian Store Express", nullable=False)
    tracking_number = db.Column(db.String(40), default="", nullable=False)
    invoice_no = db.Column(db.String(24), default="", nullable=False)
    invoice_issued_at = db.Column(db.DateTime(timezone=True))
    note = db.Column(db.Text, default="", nullable=False)
    shipping_name = db.Column(db.String(120), nullable=False)
    shipping_address = db.Column(db.Text, nullable=False)
    shipping_city = db.Column(db.String(80), nullable=False)
    shipping_postal = db.Column(db.String(20), nullable=False)
    shipping_phone = db.Column(db.String(32), default="", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User", back_populates="orders")

    items = db.relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    events = db.relationship(
        "OrderEvent",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="OrderEvent.created_at.asc(), OrderEvent.id.asc()",
    )

    @property
    def item_count(self) -> int:
        return sum(i.quantity for i in self.items)

    @property
    def is_cancellable(self) -> bool:
        return self.status in ("Processing", "Packed")

    @property
    def flow_index(self) -> int:
        if self.status == CANCELLED:
            return -1
        return ORDER_FLOW.index(self.status) if self.status in ORDER_FLOW else 0

    def add_event(self, status: str, note: str = "") -> None:
        self.status = status
        self.updated_at = utcnow()
        self.events.append(OrderEvent(status=status, note=note))


class OrderEvent(db.Model):
    __tablename__ = "order_events"

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(32), nullable=False)
    note = db.Column(db.String(200), default="", nullable=False)
    location = db.Column(db.String(80), default="", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    order = db.relationship("Order", back_populates="events")


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    product_name = db.Column(db.String(160), default="", nullable=False)
    variant_name = db.Column(db.String(60), default="", nullable=False)

    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    variant_id = db.Column(db.Integer, db.ForeignKey("product_variants.id"))

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product", lazy="selectin")
    variant = db.relationship("ProductVariant", lazy="selectin")

    @property
    def line_total(self) -> Decimal:
        return Decimal(self.unit_price) * self.quantity


class SupportTicket(db.Model):
    __tablename__ = "support_tickets"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(16), unique=True, nullable=False, index=True)
    subject = db.Column(db.String(180), nullable=False)
    category = db.Column(db.String(40), default="General", nullable=False)
    status = db.Column(db.String(20), default="open", nullable=False)  # open | pending | resolved | closed
    priority = db.Column(db.String(20), default="normal", nullable=False)
    order_ref = db.Column(db.String(24), default="", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User")
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    order = db.relationship("Order")
    messages = db.relationship(
        "TicketMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TicketMessage.created_at.asc(), TicketMessage.id.asc()",
    )

    @property
    def last_message(self):
        return self.messages[-1] if self.messages else None

    @property
    def is_open(self) -> bool:
        return self.status not in ("resolved", "closed")


class TicketMessage(db.Model):
    __tablename__ = "ticket_messages"

    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    is_staff = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    ticket_id = db.Column(db.Integer, db.ForeignKey("support_tickets.id"), nullable=False)
    ticket = db.relationship("SupportTicket", back_populates="messages")
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    author = db.relationship("User")


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(60), nullable=False)
    entity = db.Column(db.String(40), default="", nullable=False)
    entity_id = db.Column(db.String(40), default="", nullable=False)
    detail = db.Column(db.String(300), default="", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    actor = db.relationship("User")


class EmailLog(db.Model):
    __tablename__ = "email_log"

    id = db.Column(db.Integer, primary_key=True)
    to_addr = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, default="", nullable=False)
    kind = db.Column(db.String(40), default="transactional", nullable=False)
    read_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    user = db.relationship("User")


class Subscriber(db.Model):
    __tablename__ = "subscribers"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    source = db.Column(db.String(40), default="footer", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class ViewEvent(db.Model):
    __tablename__ = "view_events"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(40), default="", nullable=False, index=True)
    viewed_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
