PYTHON ?= python3
VENV := env
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
REVIEWTOOL := $(VENV)/bin/reviewtool

.PHONY: all bootstrap install test run run-gui run-tui clean help

all: bootstrap

help:
	@echo "Targets:"
	@echo "  make bootstrap  - Create env/ virtualenv and install reviewtool + dev deps"
	@echo "  make test       - Run pytest test suite via env/bin/pytest"
	@echo "  make run        - Run reviewtool (auto-detect GUI or TUI)"
	@echo "  make run-gui    - Run reviewtool with PyQt6 GUI (--gui)"
	@echo "  make run-tui    - Run reviewtool with Textual TUI (--tui)"
	@echo "  make clean      - Remove build artifacts and cache directories"

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

bootstrap: $(VENV)/bin/activate
	$(PIP) install -e ".[dev]"

install: bootstrap

test: $(VENV)/bin/activate
	$(PYTEST) -v

run: $(VENV)/bin/activate
	$(REVIEWTOOL)

run-gui: $(VENV)/bin/activate
	$(REVIEWTOOL) --gui

run-tui: $(VENV)/bin/activate
	$(REVIEWTOOL) --tui

clean:
	rm -rf build/ dist/ src/*.egg-info .pytest_cache/
	find src tests -type d -name "__pycache__" -exec rm -rf {} +
