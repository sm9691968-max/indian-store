import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# A published build must supply its own key; the fallback exists so the demo
# runs with zero setup. It is deliberately obvious so it is never shipped.
_DEV_SECRET = "dev-secret-change-me-in-production"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_SECRET)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'shop.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Only set for real HTTPS deployments; a bare "1" would stop the cookie
    # being sent over the http:// used in local development.
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "") == "1"
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * 30
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30

    # CSRF: a None limit means tokens never expire, so cap it.
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8
    WTF_CSRF_HEADERS = ["X-CSRFToken", "X-CSRF-Token"]

    DEBUG = os.environ.get("FLASK_DEBUG", "") == "1"

    # Local AI endpoint used by the shopping assistant and natural-language
    # search. Point it anywhere speaking the OpenAI wire protocol (Ollama, LM
    # Studio, vLLM) — the stack prefers a local model so the demo needs no keys.
    AI_BASE_URL = os.environ.get("AI_BASE_URL", "http://127.0.0.1:11434")
    AI_MODEL = os.environ.get("AI_MODEL", "")
    AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "45"))
    AI_RATE_LIMIT = 8  # assistant turns per client per window
    AI_RATE_WINDOW = 15  # seconds
