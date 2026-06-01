"""Standalone FastAPI app for the equation visualizer.

Run with:  python equation_viz/app.py
Then open: http://127.0.0.1:8766/

Later, this module can be mounted into the main Jarvis server as a sub-app.
"""

from __future__ import annotations

import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

try:
    from interpreter import interpret
    from plotter import build_figure
except ImportError:
    from equation_viz.interpreter import interpret
    from equation_viz.plotter import build_figure

app = FastAPI(title="Equation Visualizer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = Path(__file__).parent / "static"


class InterpretRequest(BaseModel):
    equation: str


class EvalRequest(BaseModel):
    spec: dict
    params: dict


@app.post("/interpret")
async def interpret_equation(req: InterpretRequest):
    """Parse an equation text → spec + initial Plotly figure."""
    try:
        spec = interpret(req.equation)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Interpretation failed: {exc}")

    default_params = {p["name"]: p["default"] for p in spec.get("parameters", [])}
    try:
        figure = build_figure(spec, default_params)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Plot error: {exc}")

    return {"spec": spec, "figure": figure}


@app.post("/evaluate")
async def evaluate_equation(req: EvalRequest):
    """Re-evaluate a spec with updated parameter values (slider interaction)."""
    try:
        figure = build_figure(req.spec, req.params)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"figure": figure}


@app.get("/", response_class=HTMLResponse)
async def index():
    return (_STATIC / "index.html").read_text()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766, log_level="info")
