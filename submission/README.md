# PipelineFixRL — Submission Package

- **Repo:** https://github.com/Changjae-LE/pipelinefixrl.git (branch `master`)
- **Reproduction / commands:** see the top-level [`../README.md`](../README.md)
  (Quickstart, "Baseline & advanced solutions", "Evaluation") and
  [`../docs/PLAN.md`](../docs/PLAN.md) §11.8 for per-milestone evidence.
- **Baseline / advanced agents:** `../harness/agents/fix_agent.py`
  (`python -m harness agent --id <scenario> --tier baseline|advanced`,
  or `make baseline|advanced|eval-agents AGENT_SID=<scenario>`).

## Contents
| File | What |
|---|---|
| `agent-trajectory.jsonl` | full Claude Code build-session trace (email + host username redacted; valid JSONL) |
| `SUBMISSION_NOTES.md` | status, commit chain, per-scenario validation results |
| `M7-M8.patch` | `git format-patch 22eb6c1..HEAD` for the final scenario milestones |
| `pipelinefixrl-full.bundle` | offline full-history clone source (`git clone pipelinefixrl-full.bundle`) |
