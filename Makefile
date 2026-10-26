# Convenience targets for the sdn-cnanm SRv6 lab.
#
#   make setup     one-time bootstrap (OS packages, .venv, sudoers rule)
#   make check     parse/validate the YAML topology (no root, no network)
#   make run       start the lab; 'exit' at the mininet> prompt tears it down
#   make clean     remove Mininet/FRR leftovers after a crash or Ctrl-C
#   make doctor    preflight checks (FRR, seg6, sudoers, imports)
#   make lint      ruff check
#   make format    ruff format
#   make typecheck pyright (strict, must be 0 errors)

REPO_ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
VENV := $(REPO_ROOT)/.venv
VENV_PYTHON := $(VENV)/bin/python
ENTRYPOINT := $(REPO_ROOT)/src/net_topology_yaml.py

# `run` and `clean` deliberately use exactly these absolute, argument-free
# command lines: /etc/sudoers.d/net_topology matches the exact argv, so adding
# arguments here would make sudo prompt for a password (see RUNBOOK.md §2.3).
.PHONY: help setup deps check run clean doctor lint format typecheck

help:
	@sed -n '2,12p' $(MAKEFILE_LIST) | sed 's/^# \{0,1\}//'

setup:
	./scripts/setup.sh

deps:
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt

check:
	$(VENV_PYTHON) $(ENTRYPOINT) --check

run:
	sudo $(VENV_PYTHON) $(ENTRYPOINT)

clean:
	sudo $(VENV_PYTHON) -m ipmininet.clean

doctor:
	./scripts/doctor.sh

lint:
	$(VENV)/bin/ruff check .

format:
	$(VENV)/bin/ruff format .

typecheck:
	$(VENV)/bin/pyright
