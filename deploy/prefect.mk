PREFECT_PROFILE      := $(APP)
PREFECT_PROFILE_PATH := .prefect_profile
PIDFILE              := prefect-server.pid
LOGFILE              := prefect-server.log
ENVFILE              := .prefect_env
HOST                 := 127.0.0.1
PORT                 := 4200
VENV_BIN             := .venv/bin
export PREFECT_API_URL := http://$(HOST):$(PORT)/api

prefect-server: ## start Prefect server (background, idempotent)
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
	    echo "server already running (PID $$(cat $(PIDFILE)))"; \
	else \
	    echo "Starting Prefect server on $(HOST):$(PORT) …"; \
	    mkdir -p $$(dirname $(PIDFILE)); \
	    echo "export PREFECT_API_URL=http://$(HOST):$(PORT)/api" > $(ENVFILE); \
	    PREFECT_API_URL=http://$(HOST):$(PORT)/api \
	      nohup $(VENV_BIN)/prefect server start --host $(HOST) --port $(PORT) \
	      >$(LOGFILE) 2>&1 & \
	      echo $$! > $(PIDFILE); \
	    echo "PID $$(cat $(PIDFILE))  |  logs: tail -f $(LOGFILE)"; \
	    echo ""; \
	    echo "To use Prefect CLI commands, run:"; \
	    echo "  source .venv/bin/activate"; \
	    echo "  source $(ENVFILE)"; \
	fi

prefect-server-stop: ## Stop the background Prefect server
	@if [ -f $(PIDFILE) ]; then \
	    PID=$$(cat $(PIDFILE)); \
	    if kill -0 $$PID 2>/dev/null; then \
	        echo "Stopping Prefect server (PID $$PID) …"; \
	        kill -TERM $$PID && rm -f $(PIDFILE) $(ENVFILE); \
	    else \
	        echo "PID file exists ($$PID) but process dead – removing stale PID file"; \
	        rm -f $(PIDFILE) $(ENVFILE); \
	    fi; \
	else \
	    echo "No PID file ($(PIDFILE)) – server not running?"; \
	fi

prefect-server-logs: ## Tail the server log
	tail -f $(LOGFILE)