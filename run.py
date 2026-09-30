from app import create_app

app = create_app()

if __name__ == "__main__":
    # Debug mode exposes the Werkzeug console, which allows arbitrary code
    # execution, so it is opt-in via FLASK_DEBUG=1 and never the default.
    app.run(debug=app.config["DEBUG"], port=5000)
