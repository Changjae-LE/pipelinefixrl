"""PipelineFixRL base application.

A deliberately tiny FastAPI service. `/health` is the readiness *and* liveness
target used by the Helm chart. It has no external dependencies so it is always
fast and deterministic.
"""

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

APP_NAME = "pipelinefixrl-app"
APP_VERSION = os.environ.get("APP_VERSION", "0.1.0")

app = FastAPI(title=APP_NAME, version=APP_VERSION)


@app.get("/")
def root() -> dict:
    return {"name": APP_NAME, "version": APP_VERSION}


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
