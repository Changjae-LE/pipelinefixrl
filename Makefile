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

.PHONY: help setup doctor kind-up kind-down test lint build deploy-base verify-base clean-ns e2e-base eval

help:
	@echo "targets: setup doctor kind-up kind-down test lint build deploy-base verify-base clean-ns e2e-base eval"

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

eval:
	"$(PY)" -m harness eval
