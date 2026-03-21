# cSpell: disable
# GenAI Graph -- project Makefile.
# All shared targets (install, fmt, lint, test, clean, rebase, ...) come from
# tk_makefile.mk which mirrors genai-tk/Makefile.  Only project-specific
# variables and targets belong here.

##############################
##  Project variables
##############################
APP            = genai-graph
PKG_NAME       = genai_graph
IMAGE_VERSION  = 0.2a
AWS_REGION     = eu-west-1
AWS_ACCOUNT_ID = 909658914353
STREAMLIT_ENTRY = genai_graph/main/streamlit.py
MODAL_ENTRY     = genai_graph/main/modal_app.py

# PYTHONPATH when running against a local genai-tk source checkout.
# Falls back gracefully when genai-tk is only present in .venv.
DEV_PYTHONPATH = ../genai-tk:.:$(PWD)

all: help

##############################
##  Includes
##############################
include tk_makefile.mk   # genai-tk standard targets (install/fmt/lint/test/clean/...)
# include deploy/docker.mk
# include deploy/prefect.mk
# include deploy/modal.mk

##############################
##  Web Applications
##############################
.PHONY: webapp fast-api langserve

webapp:  ## Launch Streamlit app
	PYTHONPATH=$(DEV_PYTHONPATH) uv run streamlit run "$(STREAMLIT_ENTRY)"

fast-api:  ## Launch FastAPI server locally
	uvicorn $(FASTAPI_ENTRY_POINT) --reload

langserve:  ## Launch LangServe app
	PYTHONPATH=$(DEV_PYTHONPATH) uv run python genai_graph/main/langserve_app.py

##############################
##  Testing
##############################
.PHONY: test-install

test-install: .pythonpath  ## Quick smoke-test: call a fake LLM via the CLI
	@if [ -z "$(PYTHONPATH)" ]; then \
		echo -e "\033[33mWarning: PYTHONPATH is not set.\033[0m"; \
	else \
		echo -e "\033[32mPYTHONPATH=$(PYTHONPATH)\033[0m"; \
	fi
	@echo -e "\033[3m\033[36mExpected output: 'tell me a joke on bears'\033[0m"
	echo bears | PYTHONPATH=$(DEV_PYTHONPATH) uv run cli core run joke -m parrot_local_fake

##############################
##  Graph Tools
##############################
.PHONY: kuzu-explorer baml-generate

kuzu-explorer:  ## Start KuzuDB explorer at http://localhost:8000
	docker run --rm -p 8000:8000 \
		-v /home/tcl/kuzu:/database \
		-e KUZU_FILE=ekg_database.db \
		kuzudb/explorer:latest &
	xdg-open http://localhost:8000

baml-generate:  ## Generate BAML Python client from baml_src
	uv run baml-cli generate --from ./genai_graph/ekg/baml_src

##############################
##  Prefect
##############################
.PHONY: prefect-server prefect-server-stop

prefect-server:  ## Start Prefect server in background
	@prefect config set PREFECT_API_URL=http://127.0.0.1:4200/api
	@nohup prefect server start > prefect-server.log 2>&1 & \
	echo "Prefect server started (PID: $$!)"; \
	echo "Logs: tail -f prefect-server.log"; \
	echo "Stop: make prefect-server-stop"

prefect-server-stop:  ## Stop Prefect server
	@pkill -f 'prefect server start' \
		&& echo "Prefect server stopped." \
		|| echo "No Prefect server running."

##############################
##  Infrastructure
##############################
.PHONY: postgres chrome

postgres:  ## Start Postgres + pgvector container
	docker rm -f pgvector-container 2>/dev/null || true
	docker run -d --name pgvector-container \
		-e POSTGRES_USER=$(POSTGRES_USER) \
		-e POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		-e POSTGRES_DB=ekg \
		-p 5432:5432 \
		-v /home/tcl/pgvector-data:/var/lib/postgresql/data \
		pgvector/pgvector:pg17

chrome:  ## Start Chromium container (UI at http://localhost:3000)
	docker rm -f chromium 2>/dev/null || true
	docker run -d --name=chromium \
		--security-opt seccomp=unconfined \
		-e PUID=1000 -e PGID=1000 -e TZ=Europe/Paris \
		-p 3000:3000 -p 3001:3001 \
		-v /home/tcl/.chromiun:/config \
		--shm-size="1gb" --restart unless-stopped \
		lscr.io/linuxserver/chromium:latest
	xdg-open localhost:3000
