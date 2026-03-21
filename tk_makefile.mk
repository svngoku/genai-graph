# cSpell: disable
# tk_makefile.mk -- genai-tk standard targets, vendored for downstream projects.
# Mirrors genai-tk/Makefile; update when genai-tk introduces new shared targets.
#
# Requires PKG_NAME defined before this include.
# Optional: DEV_PYTHONPATH for local genai-tk source checkout.

##############################
##  Shell & flags
##############################
MAKEFLAGS += --warn-undefined-variables
SHELL     := bash -euo pipefail -c

##############################
##  .env discovery (walk up to find .env)
##############################
ENV_FILE := $(shell \
	if   [ -f ".env" ];       then echo "$(CURDIR)/.env"; \
	elif [ -f "../.env" ];    then echo "$(CURDIR)/../.env"; \
	elif [ -f "../../.env" ]; then echo "$(CURDIR)/../../.env"; \
	else echo ""; fi)
ifneq ($(ENV_FILE),)
include $(ENV_FILE)
else
$(warning .env file not found in current or parent directories)
endif

##############################
##  Guards
##############################
.PHONY: .uv .pre-commit .pythonpath

.uv:  ## Check that uv is installed
	@uv -V || echo 'Please install uv: curl -LsSf https://astral.sh/uv/install.sh | sh'

.pre-commit: .uv  ## Check that pre-commit is installed
	@uv run pre-commit -V || uv pip install pre-commit

.pythonpath:
	@if [ -z "$(PYTHONPATH)" ]; then \
		echo "Warning: PYTHONPATH not set. Consider: export PYTHONPATH=\".\"; \
	fi

##############################
##  Install
##############################
.PHONY: check-uv install install-dev

check-uv:  ## Check if uv is installed, install if missing
	@if command -v uv >/dev/null 2>&1; then \
		echo "uv is already installed"; \
	else \
		echo "Installing uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		. $$HOME/.local/bin/env; \
	fi

install: check-uv  ## Install package and dependencies
	uv sync

install-dev: check-uv  ## Install with development dependencies
	uv sync --all-extras

##############################
##  Code Quality
##############################
.PHONY: fmt lint quality check

fmt:  ## Format code with ruff (imports + style)
	uv run ruff format .
	uv run ruff check --select I --fix .

lint:  ## Lint code with ruff (fix safe issues)
	uv run ruff check --fix $(PKG_NAME)

quality:  ## Ruff check without auto-fix (CI-safe)
	@echo "Checking $(PKG_NAME) (excluding .venv and wip)..."
	uv run ruff check --exclude .venv --exclude $(PKG_NAME)/wip .

check: fmt lint test  ## Run fmt + lint + test sequentially

##############################
##  Testing
##############################
.PHONY: test test-unit test-integration test-full

test:  ## Run unit and integration tests
	uv run pytest tests/unit_tests/ tests/integration_tests/ -v

test-unit:  ## Run unit tests only
	uv run pytest tests/unit_tests/ -v

test-integration:  ## Run integration tests only
	uv run pytest tests/integration_tests/ -v

test-full:  ## Run ALL tests including real LLM calls (requires API keys)
	@echo "Requires a valid API key for the 'fast_model' tag."
	uv run pytest tests/unit_tests/ tests/integration_tests/ \
		--include-real-models -m "not slow" -v

##############################
##  Maintenance
##############################
.PHONY: rebase clean clean-notebooks clean-history

rebase:  ## Sync with remote, stash changes, rebase, upgrade genai-tk
	git fetch origin
	git stash
	git rebase origin/main
	uv sync --upgrade-package genai-tk

clean:  ## Clean Python bytecode and cache files
	uv cache prune
	find . \( -name "*.py[co]" -o -name "__pycache__" \
	         -o -name ".ruff_cache" -o -name ".mypy_cache" \
	         -o -name ".pytest_cache" \) \
		-exec rm -rf {} + 2>/dev/null || true

clean-notebooks:  ## Clear Jupyter notebook outputs
	@find . -path "./.venv" -prune -o -name "*.ipynb" -print | while read -r nb; do \
		echo "Cleaning: $$nb"; \
		uv run --with nbconvert python -m nbconvert --clear-output --inplace "$$nb"; \
	done

clean-history:  ## Remove duplicates and noise from ~/.bash_history
	@if [ -f ~/.bash_history ]; then \
		awk '!/^(ls|cat|hgrep|h|cd|p|m|ll|pwd|code|mkdir|export|rmdir|uv tree|make)( |$$)/ \
		      && !seen[$$0]++' ~/.bash_history > ~/.bash_history_unique && \
		mv ~/.bash_history_unique ~/.bash_history; \
		echo "Done. Run 'history -c; history -r' to reload."; \
	else \
		echo "No ~/.bash_history found"; \
	fi

##############################
##  Info
##############################
.PHONY: help show-dev-path

show-dev-path:  ## Show DEV_PYTHONPATH and genai-tk availability
	@echo "DEV_PYTHONPATH : $(DEV_PYTHONPATH)"
	@echo "genai-tk source: $$([ -d ../genai-tk ] && echo 'EXISTS (local checkout)' || echo 'NOT FOUND (using .venv)')"

help:
	@echo "Available targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ \
		{ printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST) | sort -u
