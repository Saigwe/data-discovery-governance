PY ?= python
PG_DSN ?= postgresql://postgres:postgres@localhost:5432/pagila
MONGO ?= mongodb://localhost:27017
PAGILA = https://raw.githubusercontent.com/devrimgunduz/pagila/ce6e114236bb828e5ec0dc835f65f1bf1b9179f5

.PHONY: all download load estate discover test

all: download load estate discover test   ## needs PostgreSQL 18 (Pagila uses uuidv7) with pgvector, and MongoDB; CI starts both

download:   ## Pagila at a pinned commit (PostgreSQL licence), 13 MB
	mkdir -p data/raw
	for f in pagila-schema.sql pagila-data.sql LICENSE.txt; do [ -s data/raw/$$f ] || curl -sL -o data/raw/$$f $(PAGILA)/$$f; done

load:       ## Pagila (PostgreSQL licence) into PostgreSQL, exactly as published
	psql "$(PG_DSN)" -q -v ON_ERROR_STOP=1 -f data/raw/pagila-schema.sql
	psql "$(PG_DSN)" -q -v ON_ERROR_STOP=1 -f data/raw/pagila-data.sql

estate:     ## the app team's MongoDB and the partner's JSON feed, built from it, with planted problems
	$(PY) estate/build_estate.py --pg "$(PG_DSN)" --mongo "$(MONGO)" --feeds data/estate

discover:
	cd discovery && $(PY) run.py --pg "$(PG_DSN)" --mongo "$(MONGO)" --feeds ../data/estate --planted ../estate/planted.json --out ../results --catalog ../catalog

test:
	$(PY) -m pytest -q tests
