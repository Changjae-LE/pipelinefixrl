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

# Scenarios with generated targets (scenario-001 keeps its explicit block below).
SCENARIOS := 002 003 004 005 006 007 008 009 010

.PHONY: baseline advanced eval-agents help setup doctor kind-up kind-down test lint ci build deploy-base verify-base clean-ns e2e-base \
        scenario-001-broken scenario-001-golden scenario-001 scenario-001-compose e2e-scenario-001 eval \
        $(foreach s,$(SCENARIOS),scenario-$(s) scenario-$(s)-broken scenario-$(s)-golden scenario-$(s)-compose)

SID ?= scenario-001

help:
	@echo "M1: setup doctor kind-up kind-down test lint ci build deploy-base verify-base clean-ns e2e-base"
	@echo "M2: scenario-001-broken scenario-001-golden scenario-001 scenario-001-compose e2e-scenario-001 eval"
	@echo "M3+: scenario-0NN[-broken|-golden|-compose] for NN in $(SCENARIOS) (targets declared; scenarios not yet implemented)"

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

ci:
	bash scripts/ci.sh

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

# --- M3+: boilerplate targets for scenario-002 .. scenario-010 -------------
# Declared now (base-evolution / M-BE) so no later scenario milestone edits
# this Makefile. Each recipe is a thin wrapper over the harness; the scenarios
# themselves are NOT implemented yet.
define SCENARIO_RULES
scenario-$(1)-broken:
	"$$(PY)" -m harness scenario --id scenario-$(1) --variant broken
	"$$(PY)" -m harness scenario-cleanup-ns --id scenario-$(1) --variant broken

scenario-$(1)-golden:
	"$$(PY)" -m harness scenario --id scenario-$(1) --variant golden
	"$$(PY)" -m harness scenario-cleanup-ns --id scenario-$(1) --variant golden

scenario-$(1)-compose:
	"$$(PY)" -m harness compose-check --id scenario-$(1)

scenario-$(1): scenario-$(1)-broken scenario-$(1)-golden scenario-$(1)-compose
	@echo "scenario-$(1): PASS"
endef
$(foreach s,$(SCENARIOS),$(eval $(call SCENARIO_RULES,$(s))))

# --- Baseline / Advanced repair agents (hackathon submission) --------------
# baseline = offline no-LLM heuristic; advanced = Claude Code agentic workflow.
# Both submit a candidate fix that the unchanged harness builds, deploys and
# scores as a `baseline` / `advanced` variant run.
AGENT_SID ?= scenario-001

baseline:
	"$(PY)" -m harness agent --id $(AGENT_SID) --tier baseline --allow-unexpected
	"$(PY)" -m harness scenario-cleanup-ns --id $(AGENT_SID) --variant baseline

advanced:
	"$(PY)" -m harness agent --id $(AGENT_SID) --tier advanced --allow-unexpected
	"$(PY)" -m harness scenario-cleanup-ns --id $(AGENT_SID) --variant advanced

# broken vs golden vs baseline vs advanced, one scenario, all four scored runs.
eval-agents:
	"$(PY)" -m harness scenario --id $(AGENT_SID) --variant broken --allow-unexpected
	"$(PY)" -m harness scenario-cleanup-ns --id $(AGENT_SID) --variant broken
	"$(PY)" -m harness scenario --id $(AGENT_SID) --variant golden
	"$(PY)" -m harness scenario-cleanup-ns --id $(AGENT_SID) --variant golden
	"$(PY)" -m harness agent --id $(AGENT_SID) --tier baseline --allow-unexpected
	"$(PY)" -m harness scenario-cleanup-ns --id $(AGENT_SID) --variant baseline
	"$(PY)" -m harness agent --id $(AGENT_SID) --tier advanced --allow-unexpected
	"$(PY)" -m harness scenario-cleanup-ns --id $(AGENT_SID) --variant advanced
	@echo "eval-agents ($(AGENT_SID)): PASS"
