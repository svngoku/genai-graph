# Prefect server recipes for genai-graph

set dotenv-load
set shell := ["bash", "-euc"]

host := "127.0.0.1"
port := "4200"
pidfile := "prefect-server.pid"
logfile := "prefect-server.log"
envfile := ".prefect_env"

export PREFECT_API_URL := "http://" + host + ":" + port + "/api"

[doc('Start Prefect server (background, idempotent)')]
server:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f {{ pidfile }} ] && kill -0 $(cat {{ pidfile }}) 2>/dev/null; then
        echo "server already running (PID $(cat {{ pidfile }}))"
    else
        echo "Starting Prefect server on {{ host }}:{{ port }} …"
        echo "export PREFECT_API_URL=http://{{ host }}:{{ port }}/api" > {{ envfile }}
        PREFECT_API_URL=http://{{ host }}:{{ port }}/api \
          nohup .venv/bin/prefect server start --host {{ host }} --port {{ port }} \
          >{{ logfile }} 2>&1 &
          echo $! > {{ pidfile }}
        echo "PID $(cat {{ pidfile }})  |  logs: tail -f {{ logfile }}"
        echo ""
        echo "To use Prefect CLI commands, run:"
        echo "  source .venv/bin/activate"
        echo "  source {{ envfile }}"
    fi

[doc('Stop the background Prefect server')]
server-stop:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f {{ pidfile }} ]; then
        PID=$(cat {{ pidfile }})
        if kill -0 $PID 2>/dev/null; then
            echo "Stopping Prefect server (PID $PID) …"
            kill -TERM $PID && rm -f {{ pidfile }} {{ envfile }}
        else
            echo "PID file exists ($PID) but process dead – removing stale PID file"
            rm -f {{ pidfile }} {{ envfile }}
        fi
    else
        echo "No PID file ({{ pidfile }}) – server not running?"
    fi

[doc('Tail the Prefect server log')]
server-logs:
    tail -f {{ logfile }}
