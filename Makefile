PY ?= python

.PHONY: help install dev run test lint fmt migrate seed docker clean

help:
	@echo "install  - install runtime + dev dependencies"
	@echo "dev      - Flask development server with reloader"
	@echo "run      - production server (waitress)"
	@echo "test     - run every test suite in order"
	@echo "lint     - ruff check"
	@echo "fmt      - ruff format"
	@echo "migrate  - autogenerate a migration (m=\"message\")"
	@echo "seed     - reseed the demo database"
	@echo "docker   - build and start via docker compose"
	@echo "clean    - remove Python caches"

install:
	$(PY) -m pip install -r requirements-dev.txt

dev:
	$(PY) -m flask --app wsgi run --debug

run:
	$(PY) wsgi.py

test:
	$(PY) test_smoke.py
	$(PY) test_seo.py
	$(PY) test_security.py
	$(PY) test_features.py

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff format .

migrate:
	$(PY) -m flask --app wsgi db migrate -m "$(m)"

seed:
	$(PY) seed.py --reset

docker:
	docker compose up --build

clean:
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
