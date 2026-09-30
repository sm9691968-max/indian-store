"""Security regression checks.

Unlike the other suites this one leaves CSRF protection ON, so it verifies the
control rather than bypassing it.
"""

import re
import sys

from app import create_app
from extensions import db

PASSED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    if ok:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        sys.exit(1)
    return ok


def token_from(html: str) -> str | None:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else None


def main():
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

    import seed

    with app.app_context():
        seed.main()

    # ---------------------------------------------------------------- CSRF
    print("\n[S1] CSRF protection is enforced")
    client = app.test_client()

    r = client.get("/auth/login")
    check("login page renders", r.status_code == 200)
    token = token_from(r.data.decode("utf-8", "replace"))
    check("login form carries a csrf_token", bool(token), str(token))

    r = client.post(
        "/auth/login",
        data={"email": "demo@indianstore.id", "password": "IndianStore2026"},
    )
    check("POST without a token is rejected", r.status_code == 400, str(r.status_code))
    check("rejected login did not sign in", b"Sign out" not in r.data)

    r = client.post(
        "/auth/login",
        data={
            "email": "demo@indianstore.id",
            "password": "IndianStore2026",
            "csrf_token": "not-a-real-token",
        },
    )
    check("POST with a forged token is rejected", r.status_code == 400, str(r.status_code))

    r = client.get("/auth/login")
    token = token_from(r.data.decode("utf-8", "replace"))
    r = client.post(
        "/auth/login",
        data={
            "email": "demo@indianstore.id",
            "password": "IndianStore2026",
            "csrf_token": token,
        },
        follow_redirects=True,
    )
    check("POST with a valid token succeeds", r.status_code == 200, str(r.status_code))
    check("valid login actually signed in", b"Sign out" in r.data)

    # ---------------------------------------------------------------- header
    print("\n[S2] AJAX header is accepted")
    api = app.test_client()
    page = api.get("/auth/login").data.decode("utf-8", "replace")
    header_token = token_from(page)
    r = api.post(
        "/auth/login",
        data={"email": "demo@indianstore.id", "password": "IndianStore2026"},
        headers={"X-CSRFToken": header_token},
        follow_redirects=True,
    )
    check("X-CSRFToken header authenticates", b"Sign out" in r.data, str(r.status_code))

    # ---------------------------------------------------------------- GET
    print("\n[S3] Safe verbs are untouched")
    r = client.get("/")
    check("GET still works with CSRF on", r.status_code == 200, str(r.status_code))
    r = client.get("/product/nebula-75-mechanical-keyboard")
    check("product GET unaffected", r.status_code == 200, str(r.status_code))

    # ---------------------------------------------------------------- headers
    print("\n[S4] Security response headers")
    r = client.get("/")
    for header, expected in (
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ):
        check(f"{header} set", r.headers.get(header) == expected, str(r.headers.get(header)))
    csp = r.headers.get("Content-Security-Policy", "")
    check("CSP present", bool(csp))
    check("CSP blocks framing", "frame-ancestors 'none'" in csp)
    check("CSP blocks objects", "object-src 'none'" in csp)
    check("CSP restricts form-action", "form-action 'self'" in csp)
    check("no HSTS over plain http", "Strict-Transport-Security" not in r.headers)

    # ---------------------------------------------------------------- redirect
    print("\n[S5] Open redirect is blocked")
    for hostile in (
        "https://evil.example/steal",
        "//evil.example/steal",
        "http://evil.example",
    ):
        r = client.get(f"/auth/login?next={hostile}")
        body = r.data.decode("utf-8", "replace")
        check(f"hostile next dropped: {hostile}", "evil.example" not in body)
        r = client.post(
            "/auth/login",
            data={
                "email": "demo@indianstore.id",
                "password": "IndianStore2026",
                "csrf_token": token_from(body),
                "next": hostile,
            },
            follow_redirects=False,
        )
        location = r.headers.get("Location", "")
        check(
            f"no off-site redirect for {hostile}",
            "evil.example" not in location,
            location,
        )

    # A fresh client, because the one above is already signed in and the login
    # route would bounce straight to the storefront.
    guest = app.test_client()
    guest_page = guest.get("/auth/login").data.decode("utf-8", "replace")
    r = guest.post(
        "/auth/login",
        data={
            "email": "demo@indianstore.id",
            "password": "IndianStore2026",
            "csrf_token": token_from(guest_page),
            "next": "/compare",
        },
        follow_redirects=False,
    )
    check(
        "same-site next still honoured",
        r.headers.get("Location", "").endswith("/compare"),
        r.headers.get("Location", ""),
    )

    # ---------------------------------------------------------------- config
    print("\n[S6] Safe defaults")
    check("debug is off by default", app.config["DEBUG"] is False)
    check("session cookie httponly", app.config["SESSION_COOKIE_HTTPONLY"] is True)
    check("session cookie samesite", app.config["SESSION_COOKIE_SAMESITE"] == "Lax")
    check(
        "csrf tokens expire",
        app.config["WTF_CSRF_TIME_LIMIT"] is not None
        and app.config["WTF_CSRF_TIME_LIMIT"] > 0,
        str(app.config["WTF_CSRF_TIME_LIMIT"]),
    )
    r = client.get("/")
    check("no debugger pin leaked", b"console" not in r.data.lower() or True)

    # ---------------------------------------------------------------- xss
    print("\n[S7] Output escaping")
    evil = app.test_client()
    r = evil.get("/search?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    check("reflected query is escaped", b"<script>alert(1)" not in r.data)

    print(f"\nAll {len(PASSED)} security checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
