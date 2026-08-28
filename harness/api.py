"""Thin FastAPI control plane over the harness.

Not the primary interface for Milestone 1 (the Make targets are), but present so
runs can be triggered programmatically. Start with:
    uvicorn harness.api:app --port 8080
"""

import json

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from harness import run as runmod
from harness.evaluate import is_healthy
from harness.paths import RUNS_DIR

app = FastAPI(title="pipelinefixrl-harness", version="0.1.0")


class RunRequest(BaseModel):
    variant: str = "base"
    expect_healthy: bool = True


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/runs")
def create_run(req: RunRequest):
    try:
        run_id, score, checks = runmod.run_variant(req.variant, expect_healthy=False)
    except SystemExit as e:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "run_id": run_id,
        "score": score,
        "healthy": is_healthy(checks, score),
        "checks": checks,
    }


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run_dir = RUNS_DIR / run_id
    if not (run_dir / "checks.json").exists():
        raise HTTPException(status_code=404, detail="run not found")
    checks = json.loads((run_dir / "checks.json").read_text())
    meta = json.loads((run_dir / "meta.json").read_text())
    return {"meta": meta, **checks, "healthy": is_healthy(checks["checks"], checks["score"])}
