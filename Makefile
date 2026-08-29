# PipelineFixRL — canonical task interface.
# Run from Git Bash (recipes use bash + scripts/*.sh).

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# Host locale here is cp949; force UTF-8 for all Python I/O and subprocess pipes.
export PYTHONUTF8 := 1
export PYTHONIOENCODING := utf-8

ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
VENV := $(ROOT)/.venv
PY   := $(VENV)/Scripts/python.exe

.PHONY: help setup doctor kind-up kind-down test lint build deploy-base verify-base clean-ns e2e-base \
        scenario-001-broken scenario-001-golden scenario-001 scenario-001-compose e2e-scenario-001 eval

SID ?= scenario-001

help:
	@echo "M1: setup doctor kind-up kind-down test lint build deploy-base verify-base clean-ns e2e-base"
	@echo "M2: scenario-001-broken scenario-001-golden scenario-001 scenario-001-compose e2e-scenario-001 eval"

setup:
	python -m venv "$(VENV)"
	"$(PY)" -m pip install --upgrade pip
	"$(PY)" -m pip install -e ".[dev]"

doctor:
	bash scripts/doctor.sh

kind-up:
	bash scripts/kind-up.sh

kind-down:
	bash scripts/kind-down.sh

test:
	"$(PY)" -m pytest

lint:
	bash scripts/lint.sh

build:
	"$(PY)" -m harness build --variant base

deploy-base:
	"$(PY)" -m harness run --variant base

verify-base:
	"$(PY)" -m harness verify --variant base --expect-healthy

clean-ns:
	"$(PY)" -m harness cleanup-ns --variant base

e2e-base: doctor kind-up test deploy-base verify-base clean-ns
	@echo "e2e-base: PASS"

# --- Milestone 2: scenario-001 ---------------------------------------------

scenario-001-broken:
	"$(PY)" -m harness scenario --id scenario-001 --variant broken
	"$(PY)" -m harness scenario-cleanup-ns --id scenario-001 --variant broken

scenario-001-golden:
	"$(PY)" -m harness scenario --id scenario-001 --variant golden
	"$(PY)" -m harness scenario-cleanup-ns --id scenario-001 --variant golden

scenario-001-compose:
	"$(PY)" -m harness compose-check --id scenario-001

# broken then golden then compose check; each variant's namespace is deleted
# after its run.
scenario-001: scenario-001-broken scenario-001-golden scenario-001-compose
	@echo "scenario-001: PASS (broken matched expectation, golden scored 100, patches compose to base)"

e2e-scenario-001: doctor kind-up scenario-001
	@echo "e2e-scenario-001: PASS"

eval:
	"$(PY)" -m harness eval --id $(SID)
