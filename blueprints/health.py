from flask import Blueprint, jsonify
from sqlalchemy import text

from extensions import db

health_bp = Blueprint("health", __name__)


@health_bp.get("/healthz")
def healthz():
    """Liveness: the process is up and serving."""
    return jsonify({"status": "ok"}), 200


@health_bp.get("/readyz")
def readyz():
    """Readiness: the process can serve traffic, i.e. the database answers."""
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        db.session.rollback()
        return jsonify({"status": "unavailable", "database": "down"}), 503
    return jsonify({"status": "ok", "database": "up"}), 200
