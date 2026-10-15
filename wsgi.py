from app import create_app
from config import get_config

app = create_app(get_config())


if __name__ == "__main__":
    import os

    from waitress import serve

    serve(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
    )
