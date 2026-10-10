"""Verify the SEO fixes: valid JSON-LD, correct canonicals, valid sitemap XML."""
import json
import re
import sys
from pathlib import Path
from xml.dom.minidom import parseString

sys.path.insert(0, str(Path.cwd()))
from app import create_app

app = create_app()
client = app.test_client()
fails = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'' if ok else '  -> ' + detail}")
    if not ok:
        fails.append(label)


print("\n[JSON-LD]")
h = client.get("/").get_data(as_text=True)
m = re.search(r'<script type="application/ld\+json">(.*?)</script>', h, re.S)
check("homepage emits a JSON-LD block", bool(m))
if m:
    check("no HTML entities in the block", "&quot;" not in m.group(1) and "&#34;" not in m.group(1))
    try:
        data = json.loads(m.group(1))
        check("homepage JSON-LD parses", data.get("@type") == "OnlineStore", str(data.get("@type")))
    except Exception as exc:
        check("homepage JSON-LD parses", False, str(exc))

slug = "aero-full-size-mechanical-keyboard"
p = client.get(f"/product/{slug}").get_data(as_text=True)
m = re.search(r'<script type="application/ld\+json">(.*?)</script>', p, re.S)
check("product emits a JSON-LD block", bool(m))
if m:
    try:
        data = json.loads(m.group(1))
        check("product JSON-LD parses", data.get("@type") == "Product", str(data.get("@type")))
        check("product offers block present", "offers" in data)
    except Exception as exc:
        check("product JSON-LD parses", False, str(exc))

print("\n[Canonical]")
cases = [
    ("/", "http://localhost/"),
    (f"/product/{slug}", f"http://localhost/product/{slug}"),
    ("/?category=keyboards", "http://localhost/"),
    ("/?category=keyboards&stock=1&sort=price_desc", "http://localhost/"),
    ("/?page=1", "http://localhost/"),
    ("/?page=3", "http://localhost/?page=3"),
]
for path, expected in cases:
    body = client.get(path).get_data(as_text=True)
    got = re.search(r'<link rel="canonical" href="([^"]+)"', body)
    got = got.group(1) if got else "(none)"
    got = got.replace("http://localhost:80", "http://localhost")
    check(f"canonical {path}", got == expected, f"got {got}, want {expected}")

print("\n[Sitemap]")
xml = client.get("/sitemap.xml").get_data(as_text=True)
try:
    parseString(xml)
    check("sitemap is well-formed XML", True)
except Exception as exc:
    check("sitemap is well-formed XML", False, str(exc))
check("sitemap has lastmod", "<lastmod>" in xml)
check("sitemap url count", xml.count("<loc>") == 2019, f"got {xml.count('<loc>')}")
check("no unescaped ampersands", not re.search(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", xml))

robots = client.get("/robots.txt").get_data(as_text=True)
check("robots has a sitemap pointer", "Sitemap:" in robots)
check("robots blocks /admin/", "Disallow: /admin/" in robots)

print("\n[Error pages]")
# A 404 renders a template, so the context processor runs there too. If the
# canonical builder assumes a matched rule it raises and turns the 404 into a
# 500, which is both wrong and bad for crawling.
for path in ("/nope-does-not-exist", "/product/does-not-exist"):
    check(f"missing page stays a 404: {path}", client.get(path).status_code == 404)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("All SEO checks passed.")
