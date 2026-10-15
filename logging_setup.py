from logging.config import dictConfig


def configure_logging(app) -> None:
    level = app.config.get("LOG_LEVEL", "INFO")
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%dT%H:%M:%S%z",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"level": level, "handlers": ["console"]},
            "loggers": {
                "werkzeug": {"level": "INFO", "handlers": ["console"], "propagate": False},
            },
        }
    )
    app.logger.setLevel(level)
