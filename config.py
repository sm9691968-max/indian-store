import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_DEV_SECRET = "dev-secret-change-me-in-production"

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional at runtime
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    ENV = "development"
    DEBUG = env_bool("FLASK_DEBUG")
    TESTING = False

    SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_SECRET)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'shop.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_bool("COOKIE_SECURE")
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * 30
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30

    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8
    WTF_CSRF_HEADERS = ["X-CSRFToken", "X-CSRF-Token"]

    AI_RATE_LIMIT = 8
    AI_RATE_WINDOW = 15

    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


class DevelopmentConfig(Config):
    ENV = "development"
    DEBUG = env_bool("FLASK_DEBUG", True)


class TestingConfig(Config):
    ENV = "testing"
    DEBUG = False
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite://")


class ProductionConfig(Config):
    ENV = "production"
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    SECRET_KEY = os.environ.get("SECRET_KEY")


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "dev": DevelopmentConfig,
    "testing": TestingConfig,
    "test": TestingConfig,
    "production": ProductionConfig,
    "prod": ProductionConfig,
    "default": Config,
}


def get_config(name: str | None = None):
    key = name or os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "development"
    return CONFIG_MAP.get(key.strip().lower(), DevelopmentConfig)
