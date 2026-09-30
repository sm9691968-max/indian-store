"""Populate the Indian Store demo database.

Usage:
    python seed.py            # seed only if the catalogue is empty
    python seed.py --reset    # drop every table first, then seed
"""

import random
import sys
from datetime import timedelta
from decimal import Decimal

from app import create_app
from extensions import db
from helpers import TAX_RATE, shipping_for
from models import (
    Address,
    AuditLog,
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
    ProductVariant,
    Review,
    StockAlert,
    SupportTicket,
    TicketMessage,
    User,
    ViewEvent,
    WishlistItem,
    utcnow,
)
import totp

DEMO_EMAIL = "demo@indianstore.id"
DEMO_PASSWORD = "IndianStore2026"
ADMIN_EMAIL = "admin@indianstore.id"
ADMIN_PASSWORD = "IndianStoreAdmin2026"

CATEGORIES = [
    ("Keyboards", "keyboards", "#6366f1"),
    ("Displays", "displays", "#ec4899"),
    ("Audio", "audio", "#06b6d4"),
    ("Storage", "storage", "#f59e0b"),
    ("Networking", "networking", "#10b981"),
]

PRODUCTS = [
    {
        "name": "Nebula 75 Mechanical Keyboard",
        "sku": "IS-KB-0075",
        "brand": "Aurora Labs",
        "category": "keyboards",
        "price": "189.00",
        "compare_at": "229.00",
        "cost": "96.00",
        "stock": 42,
        "sold_count": 1284,
        "badge": "Best seller",
        "featured": True,
        "image_url": "https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=900&q=80",
        "description": "Hot-swappable 75% board with a gasket mount, PBT doubleshot caps and per-key RGB. Tuned out of the box, sound profile included.",
        "highlights": "Gasket-mounted aluminium plate for a softer bottom-out\nHot-swap sockets plus QMK/VIA support\nPBT doubleshot keycaps that resist shine\nWired USB-C with a detachable braided cable",
    },
    {
        "name": "Vector TKL Pro Wireless",
        "sku": "IS-KB-0110",
        "brand": "Aurora Labs",
        "category": "keyboards",
        "price": "154.00",
        "compare_at": None,
        "cost": "82.00",
        "stock": 28,
        "sold_count": 742,
        "badge": "New",
        "featured": True,
        "image_url": "https://images.unsplash.com/photo-1587825140708-dfaf72ae4b04?w=900&q=80",
        "description": "Tenkeyless wireless board with 2.4GHz and Bluetooth 5.3, an aluminium unibody and a 4000mAh battery.",
        "highlights": "2.4GHz, Bluetooth 5.3 and USB-C in one board\nUp to three paired devices with instant switching\n4000mAh battery, roughly two months per charge\nPer-key backlight with south-facing LEDs",
    },
    {
        "name": "Orbit 27 4K Monitor",
        "sku": "IS-DP-2727",
        "brand": "Lumen",
        "category": "displays",
        "price": "649.00",
        "compare_at": "749.00",
        "cost": "410.00",
        "stock": 12,
        "sold_count": 903,
        "badge": "Pro",
        "featured": True,
        "image_url": "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=900&q=80",
        "description": "27-inch 4K IPS panel running at 144Hz with 99% sRGB coverage, hardware calibration and a single-cable USB-C 90W dock.",
        "highlights": "3840x2160 IPS at 144Hz with 1ms MPRT\nFactory calibrated to delta-E under 1\n90W power delivery over a single USB-C cable\nKVM switch and a fully adjustable stand",
    },
    {
        "name": "Prism 32 Curved Display",
        "sku": "IS-DP-3200",
        "brand": "Lumen",
        "category": "displays",
        "price": "429.00",
        "compare_at": None,
        "cost": "265.00",
        "stock": 0,
        "low_stock_threshold": 5,
        "sold_count": 611,
        "badge": "Popular",
        "featured": False,
        "image_url": "https://images.unsplash.com/photo-1593640408182-31c70c8268f5?w=900&q=80",
        "description": "32-inch QHD 165Hz curved panel with FreeSync support and a slim three-sided borderless bezel.",
        "highlights": "1500R curvature at 2560x1440 and 165Hz\nFreeSync Premium with Adaptive-Sync\nThree-sided borderless panel on a slim stand\nTwo HDMI 2.1 and one DisplayPort input",
    },
    {
        "name": "Echo ANC Headphones",
        "sku": "IS-AU-0410",
        "brand": "Resonant",
        "category": "audio",
        "price": "279.00",
        "compare_at": "329.00",
        "cost": "148.00",
        "stock": 36,
        "sold_count": 2187,
        "badge": "Staff pick",
        "featured": True,
        "image_url": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=900&q=80",
        "description": "Hybrid active noise cancelling, 40mm drivers and 38 hours of playback in a folding travel design.",
        "highlights": "Hybrid ANC with a transparency mode that keeps voices natural\n38 hours on ANC, 55 hours without\nMultipoint pairing across two devices\nFolds flat into a hard travel case",
    },
    {
        "name": "Sonar Studio Monitor Pair",
        "sku": "IS-AU-0525",
        "brand": "Resonant",
        "category": "audio",
        "price": "399.00",
        "compare_at": None,
        "cost": "230.00",
        "stock": 9,
        "sold_count": 428,
        "badge": "",
        "featured": False,
        "image_url": "https://images.unsplash.com/photo-1519558260268-cde7e03a0152?w=900&q=80",
        "description": "Near-field 5.25 inch monitors with a silk dome tweeter, sold as a matched stereo pair.",
        "highlights": "Sold as a calibrated matched pair\nSilk dome tweeter with a waveguide for even dispersion\nBalanced XLR, TRS and USB-C inputs\nIncludes isolation pads and a tilt adapter",
    },
    {
        "name": "Helix NVMe 2TB Gen4",
        "sku": "IS-ST-2200",
        "brand": "Vaultline",
        "category": "storage",
        "price": "219.00",
        "compare_at": None,
        "cost": "132.00",
        "stock": 64,
        "sold_count": 3612,
        "badge": "Fastest",
        "featured": True,
        "image_url": "https://images.unsplash.com/photo-1597872200969-2b65d56bd16b?w=900&q=80",
        "description": "PCIe 4.0 x4 drive rated at 7400MB/s sequential read with a five-year endurance warranty.",
        "highlights": "7400MB/s read and 6800MB/s write on PCIe 4.0\n2TB of TLC NAND with a DRAM cache\nRated for 1200TBW endurance over five years\nWorkspaces up to 1.8 million IOPS",
    },
    {
        "name": "Vault NAS 4-Bay Chassis",
        "sku": "IS-ST-4004",
        "brand": "Vaultline",
        "category": "storage",
        "price": "529.00",
        "compare_at": "599.00",
        "cost": "330.00",
        "stock": 7,
        "low_stock_threshold": 6,
        "sold_count": 254,
        "badge": "",
        "featured": False,
        "image_url": "https://images.unsplash.com/photo-1597872200969-2b65d56bd16b?w=900&q=80&sat=-20",
        "description": "Hot-swap four-bay aluminium enclosure with hardware RAID and dual 2.5GbE ports.",
        "highlights": "Four hot-swap bays supporting 3.5in and 2.5in drives\nRAID 0, 1, 5, 6, 10 and JBOD\nTwo 2.5GbE ports for link aggregation\nTool-less tray replacement",
    },
    {
        "name": "Aether Wi-Fi 7 Router",
        "sku": "IS-NT-7001",
        "brand": "Signalworks",
        "category": "networking",
        "price": "349.00",
        "compare_at": None,
        "cost": "205.00",
        "stock": 21,
        "sold_count": 903,
        "badge": "Wi-Fi 7",
        "featured": True,
        "image_url": "https://images.unsplash.com/photo-1544197150-b99a580bb7a8?w=900&q=80",
        "description": "Tri-band Wi-Fi 7 mesh node with a 10G uplink, multi-gig LAN and automatic band steering.",
        "highlights": "Wi-Fi 7 across three bands with 320MHz channels\n10G uplink and four multi-gig LAN ports\nMesh with automatic band steering\nWPA3, per-client policies and a hardware VPN",
    },
    {
        "name": "Gaia Mechanical Numpad",
        "sku": "IS-KB-0031",
        "brand": "Aurora Labs",
        "category": "keyboards",
        "price": "79.00",
        "compare_at": None,
        "cost": "38.00",
        "stock": 51,
        "sold_count": 187,
        "badge": "",
        "featured": False,
        "image_url": "https://images.unsplash.com/photo-1618384887929-16ec33fab9ef?w=900&q=80",
        "description": "Detachable macropad with eight programmable keys, a rotary encoder and a machined aluminium frame.",
        "highlights": "Eight fully programmable macro layers\nClicky rotary encoder with press support\nDetaches over USB-C so one cable is enough\nStores four layouts on the device itself",
    },
]

CUSTOMERS = [
    ("Alex Morgan", DEMO_EMAIL, DEMO_PASSWORD, "+1 555 0142", True),
    ("Priya Raman", "priya@indianstore.id", "IndianStore2026", "+1 555 0177", False),
    ("Daniel Okafor", "daniel@indianstore.id", "IndianStore2026", "+1 555 0198", False),
    ("Sofia Lindqvist", "sofia@indianstore.id", "IndianStore2026", "+1 555 0131", False),
    ("Kenji Watanabe", "kenji@indianstore.id", "IndianStore2026", "+1 555 0165", False),
]

ADDRESSES = [
    ("Home", "14 Lantern Row", "Apt 6B", "Portland", "97209", "United States", True),
    ("Studio", "2200 Foundry Street", "Suite 400", "Seattle", "98104", "United States", False),
]

COUPONS = [
    {
        "code": "INDIANSTORE20",
        "description": "20% off your order",
        "kind": "percent",
        "value": "20.00",
        "min_subtotal": "0.00",
        "max_uses": 0,
        "expires_in_days": 60,
    },
    {
        "code": "LAUNCH50",
        "description": "$50 off orders over $400",
        "kind": "fixed",
        "value": "50.00",
        "min_subtotal": "400.00",
        "max_uses": 100,
        "expires_in_days": 30,
    },
    {
        "code": "FREESHIP",
        "description": "Free delivery, any order",
        "kind": "freeship",
        "value": "0.00",
        "min_subtotal": "0.00",
        "max_uses": 0,
        "expires_in_days": None,
    },
    {
        "code": "VIP30",
        "description": "30% off for the inner circle",
        "kind": "percent",
        "value": "30.00",
        "min_subtotal": "250.00",
        "max_uses": 25,
        "expires_in_days": 90,
    },
    {
        "code": "EXPIRED10",
        "description": "Retired spring promotion",
        "kind": "percent",
        "value": "10.00",
        "min_subtotal": "0.00",
        "max_uses": 0,
        "expires_in_days": -14,
        "is_active": False,
    },
]

REVIEW_POOL = [
    (5, "Exactly what I hoped for", "Arrived two days early and the build quality is well past what I expected at this price. Three weeks in and it has not disappointed me once."),
    (5, "Would buy again tomorrow", "Second one I have ordered, this time for the office. Identical experience to my personal unit, and the support team answered within the hour."),
    (4, "Very good, one small gripe", "Performance is excellent and setup took five minutes. The stock configuration shipped as listed, though I wish the manual were less vague."),
    (4, "Solid daily driver", "Using it every day for work and it holds up. Battery life is roughly what is advertised, give or take a day depending on brightness."),
    (3, "Good, but read the specs", "No complaints about quality, it just is not for everyone. Heavier than I expected, so check the dimensions against your setup first."),
    (5, "Worth every cent", "I compared three alternatives before settling on this and I am glad I did. The difference in everyday use is obvious after about a week."),
    (4, "Happy with the purchase", "Does what it says. Packaging was minimal which I actually appreciated, and everything arrived undamaged."),
    (2, "Not for me", "Build is fine but it did not match my workflow the way I hoped. Returning it was painless, which counts for something."),
]

ORDER_PLAN = [
    (0, ["Nebula 75 Mechanical Keyboard", "Gaia Mechanical Numpad"], "Delivered", "WELCOME10", 0),
    (0, ["Orbit 27 4K Monitor"], "Out for delivery", "LAUNCH50", 1),
    (1, ["Echo ANC Headphones"], "Shipped", "", 2),
    (2, ["Helix NVMe 2TB Gen4"], "Delivered", "", 3),
    (2, ["Aether Wi-Fi 7 Router", "Echo ANC Headphones"], "Packed", "", 4),
    (3, ["Sonar Studio Monitor Pair"], "Processing", "", 0),
]

# --- Generated catalogue ----------------------------------------------------

# The hand-written PRODUCTS above are the showcase items. Everything below is
# generated deterministically so the catalogue is big enough to exercise
# pagination, search and filtering.
#
# Brands deliberately avoid the five used above so a brand filter still resolves
# to a small set, and no generated text contains "nvme" so the storage search
# test keeps matching exactly one hand-written product.
GENERATED_COUNT = 2000
_GENERATED_SEED = 20260930

_GEN_BRANDS = [
    "Northwind", "Kestrel", "Bluepoint", "Halcyon", "Ironwood",
    "Meridian", "Copperfield", "Larkspur", "Verdant", "Quartzline",
]

# Each category is built from product lines: a noun plus the variants that
# actually make sense for it, so the catalogue never produces nonsense like
# "Alice Layout Keycap Set" or "24in FHD Ultrawide".
#
# Every category must expose exactly _GEN_FLAT variants across its lines, and
# exactly 10 series names, so that 10 brands x 40 variants covers the 400
# products each category needs with a perfectly even, collision-free spread.
_GEN_SERIES = {
    "keyboards": ["Aero", "Tactile", "Compact", "Ergo", "Numpad", "Split",
                  "Lowprofile", "Gasket", "Meridian", "Foundry"],
    "displays": ["Vista", "Clarity", "Widefield", "Curved", "Reference", "Ultra",
                 "Studio", "Compact", "Halcyon", "Panorama"],
    "audio": ["Echo", "Resonance", "Studio", "Quietline", "Pulse", "Nomad",
              "Aria", "Crescent", "Verdant", "Larkspur"],
    "storage": ["Helix", "Vault", "Turbine", "Cascade", "Granite", "Pylon",
                "Ridge", "Atlas", "Copperfield", "Ironwood"],
    "networking": ["Signal", "Orbit", "Beacon", "Relay", "Vector", "Pulse",
                   "Anchor", "Strata", "Quartzline", "Northwind"],
}

_GEN_FLAT = 40

_GEN_MODELS = {
    "keyboards": {
        "code": "KB",
        "price": (39.00, 289.00),
        "warranty": 2,
        "tags": [
            "hot-swappable sockets",
            "PBT doubleshot keycaps",
            "QMK/VIA remappable firmware",
            "a gasket-mounted aluminium plate",
            "south-facing per-key RGB",
        ],
        "lines": [
            {
                "noun": "Mechanical Keyboard",
                "variants": ['60%', '65%', '75%', 'TKL', 'Full Size', '96%',
                             '1800 Compact', 'Alice Layout', 'Slim 75%'],
                "specs": [
                    ("Layout", "{variant}"),
                    ("Mounting", ["Gasket mount, aluminium plate", "Top mount, aluminium plate",
                                  "Gasket mount, polycarbonate plate", "Tray mount, steel plate"]),
                    ("Switches", ["Linear 45g", "Tactile 55g", "Clicky 60g", "Silent linear 45g"]),
                    ("Connection", ["USB-C, detachable braided", "Wired USB-C, fixed cable",
                                    "Tri-mode wireless", "Bluetooth 5.3 and 2.4GHz"]),
                    ("Firmware", ["QMK / VIA, remappable offline", "VIA only, remappable offline"]),
                    ("Keycaps", ["PBT doubleshot, shine-resistant", "ABS doubleshot, pad-printed"]),
                ],
            },
            {
                "noun": "Wireless Keyboard",
                "variants": ['TKL', 'Full Size', '75%', '65%', '60%', 'Compact', 'Slim', 'Ergonomic'],
                "specs": [
                    ("Layout", "{variant}"),
                    ("Connection", ["Bluetooth 5.3 and 2.4GHz", "2.4GHz only, dongle included",
                                    "Bluetooth 5.3, three-device pairing"]),
                    ("Battery", ["4000mAh, roughly two months per charge",
                                 "3000mAh, roughly six weeks per charge",
                                 "2500mAh, roughly three weeks per charge"]),
                    ("Charging", ["USB-C, full charge in 2h", "USB-C, full charge in 90 min"]),
                    ("Backlight", ["Per-key RGB", "White LED only", "No backlight"]),
                    ("Firmware", "QMK / VIA, remappable offline"),
                ],
            },
            {
                "noun": "Keycap Set",
                "variants": ['PBT Doubleshot', 'Dye-Sub PBT', 'ABS Doubleshot', 'Cherry Profile',
                             'OEM Profile', 'SA Profile', 'Blank Kit'],
                "specs": [
                    ("Profile", "{variant}"),
                    ("Material", ["PBT, 1.5mm", "ABS, 1.5mm", "PBT, 1.2mm"]),
                    ("Legend", ["Doubleshot, shine-resistant", "Dye-sub, fade resistant"]),
                    ("Compatibility", ["60% to full-size ANSI", "60% to TKL ANSI", "Universal MX stem"]),
                    ("Key count", ["87 keys", "104 keys", "129 keys", "142 keys"]),
                    ("Warranty", "2 years"),
                ],
            },
            {
                "noun": "Switch Pack",
                "variants": ['10 Pack Linear', '10 Pack Tactile', '70 Pack Linear', '70 Pack Tactile',
                             '90 Pack Clicky', 'Silent Pack'],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Type", ["Linear 45g", "Tactile 55g", "Clicky 60g", "Silent linear 45g"]),
                    ("Housing", ["Nylon top, nylon bottom", "Polycarbonate top, nylon bottom"]),
                    ("Stem", ["POM", "POM, long-pole", "UHMWPE"]),
                    ("Spring", ["Gold-plated 63.5g", "Stainless 60g", "Stainless 55g"]),
                    ("Lubricated", ["Factory lubed, 105g", "Dry, lubing recommended"]),
                ],
            },
            {
                "noun": "Low-Profile Board",
                "variants": ['60% Slim', '65% Slim', '75% Slim', 'Full Slim', 'TKL Slim'],
                "specs": [
                    ("Layout", "{variant}"),
                    ("Profile", "Low-profile, 18mm front height"),
                    ("Switches", ["Low-profile linear 40g", "Low-profile tactile 45g"]),
                    ("Connection", ["Bluetooth 5.3 and 2.4GHz", "USB-C, detachable"]),
                    ("Battery", "2000mAh, roughly four weeks per charge"),
                    ("Warranty", "2 years"),
                ],
            },
            {
                "noun": "Ergonomic Board",
                "variants": ['Alice Layout', 'Split', 'Contoured', 'Columnar', 'Tent'],
                "specs": [
                    ("Layout", "{variant}"),
                    ("Tent angle", ["5 degrees", "8 degrees", "10 degrees"]),
                    ("Split", ["Yes, two halves", "No, single unibody"]),
                    ("Connection", ["USB-C per half", "Bluetooth 5.3 and 2.4GHz"]),
                    ("Wrist rest", ["Magnetic, included", "None, sold separately"]),
                    ("Warranty", "2 years"),
                ],
            },
        ],
    },
    "displays": {
        "code": "DP",
        "price": (159.00, 1899.00),
        "warranty": 3,
        "tags": [
            "an IPS panel with 98% DCI-P3 coverage",
            "factory calibration to Delta E under 2",
            "USB-C power delivery up to 90W",
            "a height, pivot and tilt adjustable stand",
            "adaptive sync from 48Hz to 165Hz",
        ],
        "lines": [
            {
                "noun": "Monitor",
                "variants": ['24in FHD', '24in QHD', '27in QHD', '27in 4K', '32in 4K', '32in QHD',
                             '28in 4K', '34in Ultrawide', '49in Ultrawide', '42in 4K'],
                "specs": [
                    ("Size", "{variant}"),
                    ("Resolution", "AUTO_RES"),
                    ("Refresh rate", ["60Hz", "100Hz", "144Hz", "165Hz", "240Hz"]),
                    ("Panel", ["Fast IPS", "IPS", "VA", "Mini-LED"]),
                    ("Colour", ["99% sRGB", "98% DCI-P3", "95% DCI-P3", "99% sRGB, factory calibrated"]),
                    ("Connectivity", ["HDMI 2.1, DP 1.4", "USB-C 90W, DP 1.4, HDMI 2.1",
                                      "USB-C 65W, HDMI 2.0", "DP 1.4, HDMI 2.0 x2"]),
                ],
            },
            {
                "noun": "Ultrawide",
                "variants": ['34in UWQHD', '34in Curved QHD', '40in Curved QHD', '49in Curved QHD',
                             '34in 4K', '49in 4K', '30in UWQHD', '46in Ultrawide'],
                "specs": [
                    ("Size", "{variant}"),
                    ("Resolution", "AUTO_RES"),
                    ("Refresh rate", ["60Hz", "100Hz", "144Hz", "165Hz"]),
                    ("Curvature", ["1000R", "1500R", "1800R", "Flat"]),
                    ("Panel", "VA"),
                    ("Connectivity", ["HDMI 2.1, DP 1.4", "USB-C 90W, DP 1.4, HDMI 2.1"]),
                ],
            },
            {
                "noun": "Display",
                "variants": ['22in FHD', '24in FHD', '24in QHD', '27in QHD', '27in 4K', '32in 4K',
                             '28in 4K', '34in Ultrawide'],
                "specs": [
                    ("Size", "{variant}"),
                    ("Resolution", "AUTO_RES"),
                    ("Refresh rate", ["60Hz", "75Hz", "100Hz", "144Hz"]),
                    ("Panel", ["IPS", "VA", "Fast IPS"]),
                    ("Bezels", ["Three-sided frameless", "Four-sided frameless"]),
                    ("VESA", ["100x100mm", "75x75mm", "200x200mm"]),
                ],
            },
            {
                "noun": "Panel",
                "variants": ['Reference 27in QHD', 'Reference 32in 4K', 'Studio 24in FHD',
                             'Studio 32in QHD', 'Mini-LED 27in 4K', 'Mini-LED 32in 4K'],
                "specs": [
                    ("Model", "{variant}"),
                    ("Resolution", "AUTO_RES"),
                    ("Refresh rate", ["60Hz", "144Hz"]),
                    ("Colour", ["99% DCI-P3, Delta E under 2", "98% DCI-P3, calibrated in factory"]),
                    ("Uniformity", "1% factory verified, report included"),
                    ("Warranty", "3 years, advance replacement"),
                ],
            },
            {
                "noun": "Screen",
                "variants": ['24in Office FHD', '27in Office QHD', '34in Curved Gaming QHD',
                             '27in Gaming QHD', '32in Gaming QHD', '49in Productivity Ultrawide'],
                "specs": [
                    ("Size", "{variant}"),
                    ("Resolution", "AUTO_RES"),
                    ("Refresh rate", ["60Hz", "144Hz", "165Hz", "240Hz"]),
                    ("Sync", ["Adaptive sync, 48-165Hz", "G-Sync compatible", "FreeSync Premium"]),
                    ("Response", ["1ms MPRT", "0.5ms MPRT", "4ms GTG"]),
                    ("Warranty", "3 years"),
                ],
            },
            {
                "noun": "Desk Mount",
                "variants": ['Single 27in', 'Dual 32in'],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Desk mount", "Gas-spring arm, clamp to 80mm"),
                    ("Cable routing", "Integrated, no visible slack"),
                    ("Load per arm", "Up to 9kg"),
                    ("Rotation", "360 degrees at every joint"),
                    ("Warranty", "5 years"),
                ],
            },
        ],
    },
    "audio": {
        "code": "AU",
        "price": (29.00, 749.00),
        "warranty": 2,
        "tags": [
            "40mm dynamic drivers tuned in-house",
            "hybrid active noise cancelling with transparency mode",
            "Bluetooth 5.3 with multipoint pairing",
            "lossless playback over USB-C",
            "memory-foam earcups wrapped in protein leather",
        ],
        "lines": [
            {
                "noun": "Over-Ear Headphones",
                "variants": ["ANC Wireless", "Studio Wired", "Reference Closed", "ANC Pro",
                             "Wireless Plus", "Travel Foldable", "Hi-Res Wired", "Studio Mk II",
                             "ANC Max", "Open-Back Studio"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Drivers", ["40mm dynamic", "45mm dynamic", "50mm dynamic"]),
                    ("Noise cancelling", ["Hybrid ANC, adjustable", "Not fitted",
                                          "Feedforward ANC, transparency"]),
                    ("Battery", ["38h with ANC, 55h off", "60h playback, 8h with ANC",
                                 "Not battery powered"]),
                    ("Codecs", ["SBC, AAC, LDAC, aptX HD", "SBC, AAC", "SBC, AAC, aptX Lossless"]),
                    ("Weight", ["268g", "320g", "285g"]),
                ],
            },
            {
                "noun": "Wireless Earbuds",
                "variants": ["ANC Flagship", "Sport", "Hi-Res", "Long Battery", "Studio Tuning",
                             "Compact", "ANC Lite", "Travel"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Drivers", ["11mm planar", "12mm dynamic", "Dual 10mm dynamic"]),
                    ("Noise cancelling", ["Adaptive ANC, 3 levels", "Passive isolation only"]),
                    ("Battery", ["8h buds, 32h with case", "10h buds, 40h with case",
                                 "6h buds, 24h with case"]),
                    ("Water resistance", ["IPX5", "IPX4", "IP57"]),
                    ("Weight", ["5.2g per bud", "4.8g per bud"]),
                ],
            },
            {
                "noun": "Bookshelf Speakers",
                "variants": ["2-Way Pair", "3-Way Pair", "Active Powered", "Studio Monitor",
                             "Compact Pair", "Floorstanding Pair"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Drivers", ["5.25in woofer, 1in tweeter", "6.5in woofer, 1in tweeter",
                                 "8in woofer, 1in ribbon tweeter"]),
                    ("Power", ["Passive, 90W", "Active, 120W built-in", "Passive, 140W"]),
                    ("Frequency response", "48Hz - 25kHz"),
                    ("Inputs", ["Binding posts", "RCA, optical, USB-C",
                                "Binding posts, XLR balanced"]),
                    ("Enclosure", "B MDF, braced, walnut veneer"),
                    ("Weight", ["7.4kg per pair", "4.1kg per pair", "19.8kg per pair"]),
                ],
            },
            {
                "noun": "Soundbar",
                "variants": ["2.1 Channel", "3.1 Channel", "5.1 Channel", "Compact", "Atmos", "Dolby TrueHD"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Channels", ["2.1, 320W", "3.1, 420W", "5.1, 620W", "2.0, 200W"]),
                    ("Drivers", ["Dual 45mm full-range", "Tractrix horn-loaded tweeter"]),
                    ("Codecs", ["Dolby Atmos, DTS:X", "Dolby TrueHD", "Dolby Digital Plus"]),
                    ("Connectivity", ["HDMI eARC, optical, BT 5.3", "HDMI eARC, optical, Wi-Fi"]),
                    ("Width", ["950mm", "1200mm", "1650mm", "600mm"]),
                ],
            },
            {
                "noun": "Condenser Microphone",
                "variants": ["USB Desktop", "USB Studio", "XLR Cardioid", "XLR Multipattern",
                             "USB Streaming", "Large Diaphragm"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Capsule", ["17mm electret condenser", "25mm large diaphragm condenser"]),
                    ("Pattern", ["Cardioid", "Cardioid, 8 pattern switchable", "Figure-8"]),
                    ("Connection", ["USB-C, zero-latency monitoring", "XLR, needs preamp",
                                    "USB-C and XLR"]),
                    ("Self noise", ["16dBA", "12dBA", "7dBA"]),
                    ("Mount", ["Desk stand included", "Shock mount and boom arm", "Boom arm"]),
                ],
            },
            {
                "noun": "Headphone Amplifier",
                "variants": ["Desktop Balanced", "Pocket", "Desktop Tube", "Portable Balanced"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Output", ["4.4mm balanced, 6.35mm single-ended", "3.5mm single-ended",
                                "XLR and 6.35mm, single-ended"]),
                    ("Power", ["1200mW into 32ohm", "350mW into 32ohm", "2.5W into 32ohm"]),
                    ("Noise floor", ["8dB at 1V", "-110dB", "-118dB"]),
                    ("Dac", ["PCM 768kHz/32-bit", "PCM 384kHz/32-bit"]),
                    ("Inputs", ["USB-C, optical, RCA", "USB-C, 3.5mm"]),
                ],
            },
        ],
    },
    "storage": {
        "code": "ST",
        "price": (24.00, 649.00),
        "warranty": 5,
        "tags": [
            "PCIe 4.0 transfer rates up to 7400MB/s",
            "hardware AES-256 encryption",
            "an anodised aluminium housing rated to 2m drop",
            "a fanless design that runs silent",
            "hot-swap bays with tool-free trays",
        ],
        "lines": [
            {
                "noun": "Portable SSD",
                "variants": ['500GB', '1TB', '2TB', '4TB', '500GB Rugged', '1TB Rugged',
                             '2TB Gen4', '4TB Gen4'],
                "specs": [
                    ("Capacity", "{variant}"),
                    ("Interface", ["USB 3.2 Gen 2, 10Gbps", "USB 4, 40Gbps"]),
                    ("Sequential read", ["1050MB/s", "2000MB/s", "3800MB/s"]),
                    ("Sequential write", ["1000MB/s", "1800MB/s", "3600MB/s"]),
                    ("Endurance", ["300TBW over 5 years", "600TBW over 5 years", "1200TBW over 5 years"]),
                    ("Protection", ["2m drop, IP54", "3m drop, IP65", "2m drop, hardware AES-256"]),
                ],
            },
            {
                "noun": "Desktop SSD",
                "variants": ['1TB', '2TB', '4TB', '8TB', '2TB Gen4', '4TB Gen4', '1TB Gen3', '8TB Gen4'],
                "specs": [
                    ("Capacity", "{variant}"),
                    ("Interface", ["PCIe 4.0 x4, M.2 2280", "PCIe 3.0 x4, M.2 2280"]),
                    ("Sequential read", ["7000MB/s", "5600MB/s", "3500MB/s"]),
                    ("Sequential write", ["6300MB/s", "5000MB/s", "3000MB/s"]),
                    ("Endurance", ["600TBW over 5 years", "1200TBW over 5 years", "2400TBW over 5 years"]),
                    ("Cooling", ["Aluminium heatsink included", "Fanless, 0.3W idle"]),
                ],
            },
            {
                "noun": "Hard Drive",
                "variants": ['2TB', '4TB', '6TB', '8TB', '10TB', '12TB', '16TB', '18TB'],
                "specs": [
                    ("Capacity", "{variant}"),
                    ("Interface", "SATA 6Gbps, 3.5in"),
                    ("Sequential read", ["180MB/s", "220MB/s", "260MB/s"]),
                    ("Cache", ["256MB", "512MB", "1GB"]),
                    ("Workload", ["5400rpm CMR", "7200rpm CMR"]),
                    ("Warranty", "5 years"),
                ],
            },
            {
                "noun": "USB-C Enclosure",
                "variants": ['USB4 M.2', '1 Bay USB 3.2', '2 Bay USB4', '4 Bay USB 3.2',
                             '2.5GbE Bridge', '4 Bay Thunderbolt'],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Bays", ["1 x M.2 2280", "2 x M.2 2280", "4 x 3.5in SATA"]),
                    ("Interface", ["USB 3.2 Gen 2, 10Gbps", "USB4, 40Gbps", "Thunderbolt 3, 40Gbps"]),
                    ("Transfer", ["1050MB/s", "2000MB/s", "3000MB/s"]),
                    ("Cooling", "Aluminium, fanless"),
                    ("Warranty", "3 years"),
                ],
            },
            {
                "noun": "Docking Station",
                "variants": ["Thunderbolt 4", "USB4 12-Port", "Dual Display", "Triple 4K",
                             "Portable 8-Port", "USB4 16-Port"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Host interface", ["Thunderbolt 4, 40Gbps", "USB-C, 10Gbps"]),
                    ("Displays", ["Dual 4K at 60Hz", "Triple 4K at 60Hz", "Single 8K at 60Hz"]),
                    ("Ports", ["3x USB-C, 6x USB-A, 2.5GbE, SD, TB4",
                               "2x USB-C, 4x USB-A, HDMI, SD", "6x USB-A, HDMI, 2.5GbE"]),
                    ("Power delivery", ["Up to 100W to host", "Up to 65W to host", "Up to 140W to host"]),
                    ("Warranty", "3 years"),
                ],
            },
            {
                "noun": "Memory Card",
                "variants": ['128GB UHS-II', '256GB UHS-II', '512GB UHS-II', '1TB UHS-II'],
                "specs": [
                    ("Capacity", "{variant}"),
                    ("Interface", ["UHS-II, up to 300MB/s", "UHS-I, up to 200MB/s"]),
                    ("Sequential read", ["300MB/s", "200MB/s", "90MB/s"]),
                    ("Sequential write", ["260MB/s", "180MB/s", "80MB/s"]),
                    ("Rating", ["V90, sustained write", "V60", "U3, A2"]),
                    ("Warranty", "Lifetime"),
                ],
            },
        ],
    },
    "networking": {
        "code": "NW",
        "price": (29.00, 899.00),
        "warranty": 3,
        "tags": [
            "a Wi-Fi 7 radio with 4x4 MIMO",
            "multi-gigabit ports that negotiate per cable",
            "WPA3 encryption with per-client guest networks",
            "band steering across three radios",
            "a web dashboard plus local API control",
        ],
        "lines": [
            {
                "noun": "Router",
                "variants": ["AX1800", "AX3000", "AX5400", "Wi-Fi 7 BE9000", "Wi-Fi 7 BE12000",
                             "Gigabit", "Tri-Band Mesh", "Dual-Band Mesh", "4G LTE", "Enterprise VPN"],
                "specs": [
                    ("Class", "{variant}"),
                    ("Radios", ["2.4GHz and 5GHz dual-band", "2.4, 5 and 6GHz tri-band",
                                "2.4GHz single-band"]),
                    ("Ports", ["1x 2.5GbE WAN, 3x GbE LAN", "1x GbE WAN, 3x GbE LAN",
                               "2x 2.5GbE, PoE++", "1x 10GbE WAN, 4x 2.5GbE LAN"]),
                    ("Security", ["WPA3, per-client guest networks", "WPA2/WPA3, VLAN tagging",
                                  "WPA3 with hardware acceleration"]),
                    ("Management", "Web dashboard, local API, SSH"),
                    ("Warranty", "3 years"),
                ],
            },
            {
                "noun": "Switch",
                "variants": ["8-Port Gigabit", "16-Port Gigabit", "24-Port Gigabit PoE",
                             "8-Port 2.5GbE", "5-Port Multi-Gig", "16-Port Managed",
                             "24-Port Managed PoE", "2-Port SFP+"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Ports", ["8 x GbE", "16 x GbE", "24 x GbE, 4 PoE+", "8 x 2.5GbE", "2 x SFP+"]),
                    ("PoE budget", ["None", "65W", "180W", "370W"]),
                    ("Switching capacity", ["16Gbps", "32Gbps", "56Gbps", "100Gbps"]),
                    ("Managed", ["Unmanaged", "Fully managed, L2 and L3"]),
                    ("Warranty", "3 years, lifetime fan warranty"),
                ],
            },
            {
                "noun": "Access Point",
                "variants": ["Wi-Fi 6 Ceiling", "Wi-Fi 6E Ceiling", "Wi-Fi 7 Wall Plate", "Outdoor IP67",
                             "Compact Desktop", "Tri-Band Ceiling", "High-Density Ceiling", "PoE Injector"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Radios", ["2.4GHz and 5GHz dual-band", "2.4, 5 and 6GHz tri-band"]),
                    ("Coverage", ["180m2 typical, medium density", "300m2 typical, high density",
                                  "Outdoor, 400m line of sight"]),
                    ("Power", ["PoE 802.3at", "PoE 802.3af", "DC adapter included"]),
                    ("Management", "Controller or standalone, cloud optional"),
                    ("Warranty", "3 years"),
                ],
            },
            {
                "noun": "Mesh Node",
                "variants": ["2-Pack", "3-Pack", "4-Pack", "Wi-Fi 7 2-Pack", "Wi-Fi 7 3-Pack"],
                "specs": [
                    ("Pack", "{variant}"),
                    ("Radios", ["2.4GHz and 5GHz dual-band", "2.4, 5 and 6GHz tri-band"]),
                    ("Backhaul", ["Dedicated wireless backhaul", "Wired backhaul, auto-detect"]),
                    ("Coverage per node", ["180m2", "220m2", "280m2"]),
                    ("Ethernet", "1x 2.5GbE per node"),
                    ("Warranty", "3 years"),
                ],
            },
            {
                "noun": "Wi-Fi Adapter",
                "variants": ["USB-C AX3000", "USB-C Wi-Fi 7", "PCIe AX5400", "M.2 Wi-Fi 7", "USB Nano"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Standard", ["Wi-Fi 6 AX3000", "Wi-Fi 7 BE9000", "Wi-Fi 6 AX1800"]),
                    ("Interface", ["USB-C", "PCIe x1", "M.2 2230", "USB-A nano"]),
                    ("Antenna", ["Dual, internal", "Dual, external dipole", "Four, external dipole"]),
                    ("Drivers", ["Driverless on Windows 11", "Windows and Linux, kernel 5.10+"]),
                    ("Warranty", "2 years"),
                ],
            },
            {
                "noun": "Modem",
                "variants": ["DOCSIS 3.1", "Fiber ONT", "5G Cellular", "LTE Backup"],
                "specs": [
                    ("Configuration", "{variant}"),
                    ("Downstream", ["2000Mbps", "10000Mbps", "550Mbps", "150Mbps"]),
                    ("Upstream", ["35Mbps", "10000Mbps", "50Mbps", "20Mbps"]),
                    ("Interfaces", ["RJ45 + 2.5GbE", "RJ45, SC/APC adapter", "SIM + 2.5GbE",
                                    "RJ45 failover"]),
                    ("Power", "External 12V adapter"),
                    ("Warranty", "2 years"),
                ],
            },
        ],
    },
}

_GEN_IMAGES = [
    "https://images.unsplash.com/photo-1527814050087-3793815479db?w=900&q=80",
    "https://images.unsplash.com/photo-1593642532400-2682810df593?w=900&q=80",
    "https://images.unsplash.com/photo-1498049794561-7780e7231661?w=900&q=80",
    "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=900&q=80",
    "https://images.unsplash.com/photo-1555617981-dac3880eac6e?w=900&q=80",
]

_GEN_BADGES = [None, None, None, None, None, None, "New", "Best seller", "Clearance"]

# Resolution follows the token in the model name, so a "24in FHD" never ships
# with a 4K panel listed against it.
_GEN_RESOLUTIONS = [
    ("Ultrawide", "5120x1440"),
    ("UWQHD", "3440x1440"),
    ("4K", "3840x2160"),
    ("FHD", "1920x1080"),
    ("QHD", "2560x1440"),
]

# Copy is written to stay true for every line in the category, including the
# accessories. "It arrives configured" and "quiet enough for an open office"
# were both wrong for a keycap set or a switch pack.
# Category-level marketing tags were being asserted about lines they did not
# fit: a "Studio Wired" headphone was described as Bluetooth 5.3, because the
# tag pool was shared across the whole category. The copy is now built from the
# product line's own spec rows, so a sentence can never contradict the table
# printed directly beneath it.
_GEN_DESCRIPTIONS = {
    "keyboards": [
        "{name} — {fact0}, {fact1}, {fact2}. Part of the {brand} range, covered for {warranty} years and dispatched next day across India.",
        "The {variant} {noun} in the {brand} range. {fact0} and {fact1} are what you are paying for; the rest is table stakes. {warranty}-year warranty.",
        "{name} sits in the middle of the {brand} range. {fact0}, {fact1} and {fact2} are the three worth comparing before you buy.",
    ],
    "displays": [
        "{name} — {fact0}, {fact1}, {fact2}. Part of the {brand} range, covered for {warranty} years and dispatched next day across India.",
        "The {variant} {noun} in the {brand} range. {fact0} and {fact1} are what you are paying for; the rest is table stakes. {warranty}-year warranty.",
        "{name} sits in the middle of the {brand} range. {fact0}, {fact1} and {fact2} are the three worth comparing before you buy.",
    ],
    "audio": [
        "{name} — {fact0}, {fact1}, {fact2}. Part of the {brand} range, covered for {warranty} years and dispatched next day across India.",
        "The {variant} {noun} in the {brand} range. {fact0} and {fact1} are what you are paying for; the rest is table stakes. {warranty}-year warranty.",
        "{name} sits in the middle of the {brand} range. {fact0}, {fact1} and {fact2} are the three worth comparing before you buy.",
    ],
    "storage": [
        "{name} — {fact0}, {fact1}, {fact2}. Part of the {brand} range, covered for {warranty} years and dispatched next day across India.",
        "The {variant} {noun} in the {brand} range. {fact0} and {fact1} are what you are paying for; the rest is table stakes. {warranty}-year warranty.",
        "{name} sits in the middle of the {brand} range. {fact0}, {fact1} and {fact2} are the three worth comparing before you buy.",
    ],
    "networking": [
        "{name} — {fact0}, {fact1}, {fact2}. Part of the {brand} range, covered for {warranty} years and dispatched next day across India.",
        "The {variant} {noun} in the {brand} range. {fact0} and {fact1} are what you are paying for; the rest is table stakes. {warranty}-year warranty.",
        "{name} sits in the middle of the {brand} range. {fact0}, {fact1} and {fact2} are the three worth comparing before you buy.",
    ],
}


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 2))).quantize(Decimal("0.01"))


def _validate() -> None:
    """Fail loudly if a future edit breaks the even 10 x 40 spread."""
    for name, spec in _GEN_MODELS.items():
        flat = sum(len(line["variants"]) for line in spec["lines"])
        if flat != _GEN_FLAT:
            raise ValueError(
                f"{name}: {flat} variants, expected {_GEN_FLAT} "
                f"(so every brand gets an equal slice)"
            )
        if len(_GEN_SERIES[name]) != 10:
            raise ValueError(f"{name}: expected 10 series names, got {len(_GEN_SERIES[name])}")
        if name not in _GEN_DESCRIPTIONS:
            raise ValueError(f"{name}: no description templates")


def _resolve(value: str, variant: str) -> str:
    if value != "AUTO_RES":
        return value
    for token, resolution in _GEN_RESOLUTIONS:
        if token in variant:
            return resolution
    raise ValueError(f"cannot infer a resolution for variant {variant!r}")


def _resolve_specs(line: dict, variant: str, rng: random.Random) -> list[tuple[str, str]]:
    """Pick a value for every spec row of a product line."""
    rows = []
    for label, value in line["specs"]:
        if isinstance(value, list):
            value = rng.choice(value)
        rows.append((label, _resolve(value.replace("{variant}", variant), variant)))
    return rows


def _display_noun(noun: str) -> str:
    """Lowercase a noun for mid-sentence use, but leave acronyms alone."""
    if any(ch.isupper() for ch in noun[1:]):
        return noun
    return noun.lower()


def _phrase(rows: list[tuple[str, str]], start: int, stop: int) -> str:
    """Render a slice of spec rows as prose: "battery 38h, weight 268g"."""
    return ", ".join(f"{label.lower()} {value}" for label, value in rows[start:stop])


def build_generated_products(count: int = GENERATED_COUNT) -> list[dict]:
    """Return ``count`` deterministic product payloads in the PRODUCTS shape."""
    _validate()
    rng = random.Random(_GENERATED_SEED)
    slugs = {slugify(item["name"]) for item in PRODUCTS}
    categories = list(_GEN_MODELS)
    out: list[dict] = []

    for index in range(count):
        category = categories[index % len(categories)]
        spec = _GEN_MODELS[category]
        serial = index // len(categories) + 1          # 1..400 within a category

        # A flat 40-entry space of (line, variant) pairs and 10 series give 400
        # unique combinations per category. The pair is the unit, not the bare
        # variant string: "1TB" is both a Portable SSD and a Desktop SSD, and
        # collapsing those onto one noun would duplicate the product name.
        flat_pairs = [
            (line, variant)
            for line in spec["lines"]
            for variant in line["variants"]
        ]
        line, variant = flat_pairs[(serial - 1) % _GEN_FLAT]
        series = _GEN_SERIES[category][(serial - 1) // _GEN_FLAT]

        name = f"{series} {variant} {line['noun']}"

        base_slug = slugify(name)
        slug = base_slug
        bump = 2
        while slug in slugs:
            slug = f"{base_slug}-{bump}"
            bump += 1
        slugs.add(slug)

        low, high = spec["price"]
        price = _money(rng.uniform(low, high))
        cost = _money(float(price) * rng.uniform(0.45, 0.72))
        compare_at = _money(float(price) * rng.uniform(1.08, 1.35)) if rng.random() < 0.3 else None

        brand = _GEN_BRANDS[index % len(_GEN_BRANDS)]
        rows = _resolve_specs(line, variant, rng)
        template = _GEN_DESCRIPTIONS[category][(index // 3) % 3]

        out.append(
            {
                "name": name,
                "slug": slug,
                "sku": f"IS-{spec['code']}-{serial:05d}",
                "brand": brand,
                "category": category,
                "price": str(price),
                "compare_at": str(compare_at) if compare_at else None,
                "cost": str(cost),
                "stock": rng.choice([0, 3, 7, 12, 18, 24, 31, 45, 60, 88, 120]),
                "sold_count": rng.randint(0, 2400),
                "badge": _GEN_BADGES[index % len(_GEN_BADGES)],
                "featured": False,
                "image_url": _GEN_IMAGES[index % len(_GEN_IMAGES)],
                "description": template.format(
                    name=name,
                    variant=variant,
                    noun=_display_noun(line["noun"]),
                    brand=brand,
                    warranty=spec["warranty"],
                    fact0=_phrase(rows, 0, 1),
                    fact1=_phrase(rows, 1, 2),
                    fact2=_phrase(rows, 2, 3),
                ),
                # Remaining spec rows become the bullets, so the highlights
                # never repeat the sentence above or contradict the table.
                "highlights": "\n".join(
                    [f"{label}: {value}" for label, value in rows[3:]]
                    + [
                        f"{spec['warranty']}-year Indian Store warranty",
                        "Free next-day delivery across India",
                    ]
                ),
                "specs": "\n".join(f"{label}|{value}" for label, value in rows),
                "warranty_years": spec["warranty"],
            }
        )

    names = {item["name"] for item in out}
    if len(names) != len(out):
        raise ValueError(
            f"generator produced {len(out) - len(names)} duplicate product names"
        )

    return out


# --- New-feature seed data -------------------------------------------------

PRODUCT_SPECS = {
    "Nebula 75 Mechanical Keyboard": (
        "Layout|75% (82 keys)\n"
        "Switch options|Linear 45g, Tactile 55g, Clicky 60g\n"
        "Mounting|Gasket, aluminium plate\n"
        "Connection|USB-C, detachable braided\n"
        "Firmware|QMK / VIA, remappable offline"
    ),
    "Orbit 27 4K Monitor": (
        "Panel|27in IPS, 3840x2160\n"
        "Refresh rate|144Hz, 1ms MPRT\n"
        "Colour|99% sRGB, factory calibrated\n"
        "Connectivity|USB-C 90W, DP 1.4, HDMI 2.1\n"
        "Stand|Fully adjustable, pivot, height, tilt"
    ),
    "Echo ANC Headphones": (
        "Driver|40mm dynamic, closed back\n"
        "Noise cancelling|Hybrid ANC + transparency\n"
        "Battery|38h ANC, 55h off\n"
        "Codecs|SBC, AAC, LDAC, aptX HD\n"
        "Weight|268g"
    ),
    "Helix NVMe 2TB Gen4": (
        "Interface|PCIe 4.0 x4, M.2 2280\n"
        "Sequential read|7400MB/s\n"
        "Sequential write|6800MB/s\n"
        "Endurance|1200TBW over 5 years\n"
        "Warranty|5 years"
    ),
}

VARIANTS = {
    "Nebula 75 Mechanical Keyboard": [
        ("Navy / Linear", "0.00", 18, "NVY-LIN"),
        ("Navy / Tactile", "10.00", 12, "NVY-TAC"),
        ("Silver / Clicky", "10.00", 0, "SLV-CLK"),
    ],
    "Echo ANC Headphones": [
        ("Midnight", "0.00", 20, "MID"),
        ("Sandstone", "0.00", 16, "SND"),
    ],
    "Orbit 27 4K Monitor": [
        ("27in 4K 144Hz", "0.00", 8, "4K144"),
    ],
}

GALLERY = {
    "Nebula 75 Mechanical Keyboard": [
        "https://images.unsplash.com/photo-1595225476474-87563907a212?w=900&q=80",
        "https://images.unsplash.com/photo-1618384887929-16ec33fab9ef?w=900&q=80",
        "https://images.unsplash.com/photo-1615869442320-fd02a129c77c?w=900&q=80",
    ],
    "Echo ANC Headphones": [
        "https://images.unsplash.com/photo-1546435770-a3e426bf472b?w=900&q=80",
        "https://images.unsplash.com/photo-1583394838336-acd977736f90?w=900&q=80",
    ],
    "Orbit 27 4K Monitor": [
        "https://images.unsplash.com/photo-1551645120-d70bfe84c826?w=900&q=80",
        "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=900&q=80",
    ],
}

QUESTIONS = [
    (
        0,
        "Nebula 75 Mechanical Keyboard",
        "Does the keyboard ship with the keycap puller and a spare set of switches?",
        "It ships with a puller, a switch opener and two spare switches of the variant you "
        "ordered. A full spare set is not included.",
    ),
    (
        1,
        "Orbit 27 4K Monitor",
        "Can I power a laptop from the USB-C port at full 90W while driving 4K144Hz?",
        "Yes. The single USB-C cable carries video, data and up to 90W of power delivery, "
        "though a 4K144Hz signal needs a DSC-capable cable — the one included is.",
    ),
    (
        0,
        "Echo ANC Headphones",
        "How do these compare to the previous generation on call quality?",
        "The microphone array gained a dedicated wind-noise filter, so calls are noticeably "
        "cleaner outdoors. Ask if you want the comparison chart and I will send it over.",
    ),
]

TICKETS = [
    (
        "Tracking number has not updated in two days",
        "Delivery",
        "The tracking link has shown the same scan since Tuesday. Is the parcel still moving?",
        "open",
        "Yes, the parcel is moving — the carrier's status feed lags about a day behind the "
        "scans they actually take. I have flagged it with the depot and you should see the "
        "next scan within 24 hours. Sorry for the worry.",
    ),
    (
        "Wanted to change the delivery address on NX order",
        "Order",
        "I moved last week and need the next order sent to my new place rather than my address book.",
        "resolved",
        "All set — I have updated the default address on your account, so future checkouts "
        "will use it. The order in question was already with the carrier, so I have sent the "
        "courier a reroute request.",
    ),
]

GIFT_CARDS = [
    (0, "NXGC-DEMO50", "50.00", "25.00", 0),
    (1, "NXGC-THANKS25", "25.00", "25.00", 0),
    (0, "NXGC-SPENT100", "100.00", "0.00", 1),
]

SUBSCRIBE_EMAILS = [
    "fan.one@indianstore.id",
    "fan.two@indianstore.id",
    "fan.three@indianstore.id",
]


def slugify(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def make_user(full_name: str, email: str, password: str, phone: str = "", is_admin: bool = False):
    user = User(full_name=full_name, email=email, phone=phone, is_admin=is_admin)
    user.set_password(password)
    return user


# --------------------------------------------------------------------------- media


def build_product_media(db, products) -> None:
    """Attach specification tables, variants and gallery shots to key products."""
    for product in products:
        specs = PRODUCT_SPECS.get(product.name)
        if specs:
            product.specs = specs

        for position, (label, delta, stock, suffix) in enumerate(VARIANTS.get(product.name, [])):
            db.session.add(
                ProductVariant(
                    product_id=product.id,
                    label=label,
                    price_delta=Decimal(delta),
                    stock=stock,
                    sku_suffix=suffix,
                    position=position,
                )
            )

        for position, url in enumerate(GALLERY.get(product.name, [])):
            db.session.add(
                ProductImage(
                    product_id=product.id,
                    url=url,
                    caption=f"{product.name} - view {position + 1}",
                    position=position,
                    is_primary=position == 0,
                )
            )

    # The sold-out curved display gives the restock-alert demo something to fire on.
    db.session.commit()


# ------------------------------------------------------------------ q&a and alerts


def build_questions(db, products, customers, admin) -> None:
    """Two answered questions and one still waiting, so both states are visible."""
    by_name = {p.name: p for p in products}
    for index, (user_index, product_name, body, answer) in enumerate(QUESTIONS):
        created = utcnow() - timedelta(days=9 - index * 3, hours=index)
        question = ProductQuestion(
            user_id=customers[user_index].id,
            product_id=by_name[product_name].id,
            body=body,
            created_at=created,
        )
        if answer:
            question.answer = answer
            question.answered_by_id = admin.id
            question.answered_at = created + timedelta(hours=4)
        db.session.add(question)
    db.session.commit()


def build_alerts_and_views(db, products, customers, rng) -> None:
    """Stock alerts plus enough view history for the recently-viewed rail."""
    by_name = {p.name: p for p in products}
    out_of_stock = by_name["Prism 32 Curved Display"]
    headphones = by_name["Echo ANC Headphones"]

    # The sold-out panel is the perfect restock-alert demo.
    db.session.add(
        StockAlert(
            user_id=customers[0].id,
            product_id=out_of_stock.id,
            kind="restock",
            notified=False,
            created_at=utcnow() - timedelta(days=4),
        )
    )
    db.session.add(
        StockAlert(
            user_id=customers[0].id,
            product_id=headphones.id,
            kind="price",
            target_price=Decimal("249.00"),
            notified=False,
            created_at=utcnow() - timedelta(days=2),
        )
    )
    # A second customer already got notified, so the account page has history.
    db.session.add(
        StockAlert(
            user_id=customers[1].id,
            product_id=out_of_stock.id,
            kind="restock",
            notified=True,
            created_at=utcnow() - timedelta(days=12),
        )
    )

    popular = rng.sample(products, 6)
    for customer in customers:
        for product in rng.sample(popular, 4):
            db.session.add(
                ViewEvent(
                    user_id=customer.id,
                    product_id=product.id,
                    session_id=f"seed-{customer.id}-{product.id}",
                    viewed_at=utcnow() - timedelta(days=rng.randint(0, 20), minutes=rng.randint(0, 900)),
                )
            )
    db.session.commit()


# ------------------------------------------------------------- gift cards and loyalty


def build_gift_cards_and_loyalty(db, products, customers, orders, admin) -> None:
    """Issued gift cards plus a loyalty history that matches the seeded orders."""
    by_name = {p.name: p for p in products}
    for user_index, code, amount, balance, redeemed in GIFT_CARDS:
        db.session.add(
            GiftCard(
                code=code,
                amount=Decimal(amount),
                initial_balance=Decimal(amount),
                balance=Decimal(balance),
                redeemed_count=redeemed,
                currency="USD",
                is_active=True,
                user_id=customers[user_index].id,
                created_by_id=admin.id,
                created_at=utcnow() - timedelta(days=30 - user_index * 5),
            )
        )
    db.session.commit()

    # Loyalty history for the demo account: earnings tied to real orders, plus a
    # manual goodwill grant so the points page has more than one kind of row.
    demo = customers[0]
    running = 0
    demo_orders = [o for o in orders if o.user_id == demo.id]
    for offset, order in enumerate(demo_orders):
        points = max(int(float(order.total)), 0)
        running += points
        db.session.add(
            LoyaltyEvent(
                user_id=demo.id,
                order_id=order.id,
                points=points,
                balance_after=running,
                reason=f"Earned on order {order.reference}",
                created_at=order.created_at + timedelta(hours=1),
            )
        )

    for reason, points, age_days in [
        ("Welcome bonus for joining Indian Store", 150, 95),
        ("Goodwill credit after a delayed delivery", 75, 21),
    ]:
        running += points
        db.session.add(
            LoyaltyEvent(
                user_id=demo.id,
                order_id=None,
                points=points,
                balance_after=running,
                reason=reason,
                created_at=utcnow() - timedelta(days=age_days),
            )
        )

    demo.points = running
    db.session.commit()


# ------------------------------------------------------------------- support desk


def build_tickets(db, customers, admin) -> None:
    """A live thread and a resolved one, each with a staff reply."""
    for index, (subject, category, opening, status, reply) in enumerate(TICKETS):
        created = utcnow() - timedelta(days=6 - index * 4, hours=index * 3)
        ticket = SupportTicket(
            reference=f"TCK-{(index + 1) * 0x1A2B:04X}",
            user_id=customers[0].id,
            subject=subject,
            category=category,
            status=status,
            priority="normal",
            created_at=created,
            updated_at=created + timedelta(hours=2),
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add(
            TicketMessage(
                ticket_id=ticket.id,
                author_id=customers[0].id,
                is_staff=False,
                body=opening,
                created_at=created,
            )
        )
        db.session.add(
            TicketMessage(
                ticket_id=ticket.id,
                author_id=admin.id,
                is_staff=True,
                body=reply,
                created_at=created + timedelta(hours=2),
            )
        )
    db.session.commit()


# ------------------------------------------------------- audit and transactional email


def build_audit_and_email(db, products, customers, coupons, admin, orders, rng) -> None:
    """A believable trail of admin actions and the emails they produced."""
    now = utcnow()
    entries = [
        (admin, "product_create", "Product", products[0].id, f"Created {products[0].name}", 60),
        (admin, "product_update", "Product", products[2].id, f"Adjusted stock for {products[2].name}", 55),
        (admin, "coupon_create", "Coupon", coupons[1].code, "Issued LAUNCH50", 50),
        (admin, "order_status", "Order", orders[0].reference, "Moved to Delivered", 40),
        (admin, "user_role", "User", customers[2].id, "Granted support access", 33),
        (admin, "question_answer", "ProductQuestion", 1, "Answered a product question", 28),
        (admin, "gift_card_issue", "GiftCard", GIFT_CARDS[0][1], f"Issued {GIFT_CARDS[0][1]}", 21),
        (admin, "support_reply", "SupportTicket", "TCK-0001", "Replied to a customer", 6),
        (admin, "order_status", "Order", orders[1].reference, "Moved to Out for delivery", 3),
    ]
    for actor, action, entity, entity_id, detail, days_ago in entries:
        db.session.add(
            AuditLog(
                actor_id=actor.id,
                action=action,
                entity=entity,
                entity_id=str(entity_id),
                detail=detail,
                created_at=now - timedelta(days=days_ago, minutes=rng.randint(0, 500)),
            )
        )

    demo = customers[0]
    emails = [
        (demo, demo.email, "Welcome to Indian Store", "Hi Alex,\n\nYour Indian Store account is live. You start with 0 points and earn 1 per dollar spent.\n\n— The Indian Store team", "welcome", 95),
        (demo, demo.email, "Your Indian Store order has shipped", "Hi Alex,\n\nOrder IS-1A2B3C4D left the Seattle fulfilment centre. Track it from your account.\n\n— The Indian Store team", "order", 40),
        (demo, demo.email, f"Price alert armed for {products[4].name}", f"We will email you the moment {products[4].name} drops below $249.00.", "alert", 2),
        (demo, demo.email, "Delivery update for your order", "Hi Alex,\n\nYour parcel is out for delivery today.\n\n— The Indian Store team", "order", 1),
    ]
    for user, to_addr, subject, body, kind, days_ago in emails:
        row = EmailLog(
            user_id=user.id,
            to_addr=to_addr,
            subject=subject,
            body=body,
            kind=kind,
            created_at=now - timedelta(days=days_ago, minutes=rng.randint(0, 500)),
        )
        if days_ago > 30:
            row.read_at = row.created_at + timedelta(minutes=rng.randint(5, 600))
        db.session.add(row)

    db.session.commit()


def build_reviews(db, products, customers, rng) -> None:
    """Seed real review rows and let recalc_rating derive each product's average."""
    pool = list(REVIEW_POOL)
    reviewers = customers[1:]  # never review as the demo account
    for product in products:
        chosen = rng.sample(pool, 4)
        chosen_reviewers = rng.sample(reviewers, 4)
        for offset, (reviewer, (rating, title, body)) in enumerate(zip(chosen_reviewers, chosen)):
            db.session.add(
                Review(
                    product_id=product.id,
                    user_id=reviewer.id,
                    rating=rating,
                    title=title,
                    body=body,
                    is_verified=True,
                    created_at=utcnow() - timedelta(days=2 + offset * 6 + rng.randint(0, 3)),
                )
            )
    db.session.commit()

    # Recalculate from the database rather than the relationship, which can be
    # stale inside the identity map while rows are still being inserted.
    for product in products:
        counts = dict(
            db.session.execute(
                db.select(Review.rating, db.func.count(Review.id))
                .where(Review.product_id == product.id)
                .group_by(Review.rating)
            ).all()
        )
        product.review_count = sum(counts.values())
        product.rating = (
            round(
                sum(stars * n for stars, n in counts.items()) / product.review_count, 1
            )
            if product.review_count
            else 0.0
        )
    db.session.commit()


def build_orders(db, products, customers, coupons, rng) -> list:
    """Create sample orders spread across the fulfilment flow, with tracking events."""
    by_name = {p.name: p for p in products}
    now = utcnow()
    flow = ["Processing", "Packed", "Shipped", "Out for delivery", "Delivered"]
    created_orders = []

    for user_index, product_names, target_status, coupon_code, age_days in ORDER_PLAN:
        customer = customers[user_index]
        lines = [by_name[name] for name in product_names]

        subtotal = sum((Decimal(p.price) for p in lines), Decimal("0.00"))
        coupon = next((c for c in coupons if c.code == coupon_code), None)
        shipping = shipping_for(subtotal)
        discount = coupon.discount_for(subtotal, shipping) if coupon else Decimal("0.00")
        shipping_after = Decimal("0.00") if (coupon and coupon.kind == "freeship") else shipping
        taxable = max(subtotal - discount, Decimal("0.00"))
        tax = (taxable * TAX_RATE).quantize(Decimal("0.01"))
        total = taxable + shipping_after + tax

        created = now - timedelta(days=age_days, hours=rng.randint(1, 9))
        order = Order(
            reference=f"IS-{rng.getrandbits(32):08X}",
            subtotal=subtotal,
            discount=discount,
            shipping_cost=shipping_after,
            tax=tax,
            total=total,
            coupon_code=coupon.code if coupon else "",
            payment_method=rng.choice(["Card", "Card", "Wallet", "COD"]),
            note="Leave with the building concierge if I am out." if user_index == 0 else "",
            shipping_name=customer.full_name,
            shipping_address=", ".join(
                b for b in [ADDRESSES[0][1], ADDRESSES[0][2]] if b
            ),
            shipping_city=ADDRESSES[0][3],
            shipping_postal=ADDRESSES[0][4],
            shipping_phone=customer.phone,
            user_id=customer.id,
            created_at=created,
            updated_at=created,
        )
        db.session.add(order)
        db.session.flush()

        for product in lines:
            order.items.append(
                OrderItem(
                    product_id=product.id,
                    quantity=1,
                    unit_price=product.price,
                    product_name=product.name,
                )
            )

        reached = flow.index(target_status) if target_status in flow else 0
        for step_index, status in enumerate(flow[: reached + 1]):
            order.events.append(
                OrderEvent(
                    status=status,
                    note=ORDER_EVENT_NOTES.get(status, ""),
                    created_at=created + timedelta(hours=step_index * 18),
                )
            )
        order.status = target_status
        order.updated_at = created + timedelta(hours=reached * 18)

        if reached >= flow.index("Shipped"):
            order.tracking_number = f"NX{rng.getrandbits(32):08X}"
            order.carrier = rng.choice(["Indian Store Express", "Indian Store Express", "AeroPost"])

        if coupon:
            coupon.used_count += 1

        created_orders.append(order)

    db.session.commit()
    return created_orders


ORDER_EVENT_NOTES = {
    "Processing": "Order received and awaiting payment confirmation.",
    "Packed": "Picked and packed at the Seattle fulfilment centre.",
    "Shipped": "Handed to the carrier.",
    "Out for delivery": "On the van, arriving today.",
    "Delivered": "Left with the recipient.",
}


def main() -> None:
    reset = "--reset" in sys.argv
    app = create_app()

    with app.app_context():
        if reset:
            print("Dropping existing tables...")
            db.drop_all()
            db.create_all()
        elif db.session.execute(db.select(Product.id).limit(1)).first():
            print("Catalogue already seeded — nothing to do. Use --reset to rebuild.")
            return

        rng = random.Random(20260929)

        categories = {}
        for name, slug, accent in CATEGORIES:
            category = Category(name=name, slug=slug, accent=accent)
            db.session.add(category)
            categories[slug] = category
        db.session.flush()

        products = []
        for data in PRODUCTS + build_generated_products():
            payload = dict(data)
            category_slug = payload.pop("category")
            # Generated rows carry a pre-deduplicated slug; the hand-written
            # ones derive theirs from the name.
            product_slug = payload.pop("slug", None) or slugify(payload["name"])
            payload["price"] = Decimal(payload["price"])
            payload["compare_at"] = (
                Decimal(payload["compare_at"]) if payload.get("compare_at") else None
            )
            payload["cost"] = Decimal(payload["cost"])
            payload["low_stock_threshold"] = payload.get("low_stock_threshold", 5)
            payload["is_featured"] = bool(payload.pop("featured", False))
            product = Product(
                slug=product_slug,
                category=categories[category_slug],
                **payload,
            )
            db.session.add(product)
            products.append(product)
        db.session.flush()

        admin = make_user("Nora Vale", ADMIN_EMAIL, ADMIN_PASSWORD, "+1 555 0100", is_admin=True)
        db.session.add(admin)

        customers = [
            make_user(full_name, email, password, phone)
            for full_name, email, password, phone, _ in CUSTOMERS
        ]
        db.session.add_all(customers)
        db.session.flush()

        for label, line1, line2, city, postal, country, is_default in ADDRESSES:
            db.session.add(
                Address(
                    user_id=customers[0].id,
                    label=label,
                    full_name=customers[0].full_name,
                    line1=line1,
                    line2=line2,
                    city=city,
                    postal=postal,
                    country=country,
                    phone=customers[0].phone,
                    is_default=is_default,
                )
            )

        coupons = []
        for data in COUPONS:
            payload = dict(data)
            payload["value"] = Decimal(payload["value"])
            payload["min_subtotal"] = Decimal(payload["min_subtotal"])
            expires_in = payload.pop("expires_in_days", None)
            payload["expires_at"] = (
                utcnow() + timedelta(days=expires_in) if expires_in is not None else None
            )
            payload["is_active"] = payload.get("is_active", True)
            payload["used_count"] = 0
            coupon = Coupon(**payload)
            db.session.add(coupon)
            coupons.append(coupon)
        db.session.flush()

        for product in products[1:4]:
            db.session.add(WishlistItem(user_id=customers[0].id, product_id=product.id))

        db.session.commit()

        build_reviews(db, products, customers, rng)
        orders = build_orders(db, products, customers, coupons, rng)
        build_product_media(db, products)
        build_questions(db, products, customers, admin)
        build_alerts_and_views(db, products, customers, rng)
        build_gift_cards_and_loyalty(db, products, customers, orders, admin)
        build_tickets(db, customers, admin)
        build_audit_and_email(db, products, customers, coupons, admin, orders, rng)

        print("Seeded Indian Store demo data")
        print(f"  categories : {len(CATEGORIES)}")
        print(f"  products   : {len(products)}")
        print(f"  customers  : {len(customers)}")
        print(f"  coupons    : {len(coupons)}")
        print(f"  orders     : {len(orders)}")
        print(f"  variants   : {db.session.scalar(db.select(db.func.count(ProductVariant.id)))}")
        print(f"  questions  : {db.session.scalar(db.select(db.func.count(ProductQuestion.id)))}")
        print(f"  alerts     : {db.session.scalar(db.select(db.func.count(StockAlert.id)))}")
        print(f"  gift cards : {db.session.scalar(db.select(db.func.count(GiftCard.id)))}")
        print(f"  tickets    : {db.session.scalar(db.select(db.func.count(SupportTicket.id)))}")
        print(f"  loyalty    : {db.session.scalar(db.select(db.func.count(LoyaltyEvent.id)))} events")
        print(f"  audit rows : {db.session.scalar(db.select(db.func.count(AuditLog.id)))}")
        print(f"  demo user  : {DEMO_EMAIL} / {DEMO_PASSWORD}")
        print(f"  admin user : {ADMIN_EMAIL} / {ADMIN_PASSWORD}")


if __name__ == "__main__":
    main()
