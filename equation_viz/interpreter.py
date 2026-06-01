"""Claude-powered equation interpreter.

Takes any equation text (natural language, LaTeX, spoken description, code)
and returns a structured spec that plotter.py can evaluate and render.
"""

from __future__ import annotations

import json
import re

import anthropic

_CLIENT = anthropic.Anthropic()

_SYSTEM = """You are a mathematical equation interpreter for an interactive physics visualization engine.

Given any equation or mathematical description — natural language, LaTeX, pseudo-code, spoken words —
output a JSON specification describing how to visualize it.

Output ONLY valid JSON. No markdown fences, no explanation, no text before or after the JSON.

JSON schema (use exactly these field names):

{
  "title": "Short human-readable title",
  "description": "1-2 sentences describing the physical/mathematical meaning",
  "latex": "LaTeX string for the equation, e.g. N(\\omega) = \\frac{1}{e^{2\\pi\\omega/\\kappa}-1}",
  "plot_type": "line" | "surface" | "parametric2d" | "parametric3d",

  // ── For plot_type = "line" ──────────────────────────────────────────────────
  "x": {"name": "omega", "label": "ω", "range": [0.01, 20], "points": 500},
  "expression": "1 / (np.exp(2 * np.pi * omega / kappa) - 1 + 1e-30)",
  "y_label": "N(ω)",

  // ── For plot_type = "surface" ───────────────────────────────────────────────
  "x": {"name": "x", "label": "x", "range": [-3, 3], "points": 80},
  "y": {"name": "y", "label": "y", "range": [-3, 3], "points": 80},
  "expression": "numpy expression of x, y, and parameter names",
  "z_label": "f(x,y)",
  "color_scale": "Viridis" | "Plasma" | "Hot" | "RdBu" | "Magma",

  // ── For plot_type = "parametric2d" ──────────────────────────────────────────
  "t": {"name": "t", "label": "t", "range": [0, 6.28318], "points": 500},
  "x_expr": "...", "y_expr": "...",
  "x_label": "x", "y_label": "y",

  // ── For plot_type = "parametric3d" ──────────────────────────────────────────
  "t": {"name": "t", "label": "t", "range": [0, 6.28318], "points": 500},
  "x_expr": "...", "y_expr": "...", "z_expr": "...",
  "x_label": "x", "y_label": "y", "z_label": "z",

  // ── Parameters (interactive sliders) ───────────────────────────────────────
  "parameters": [
    {"name": "kappa", "label": "κ (surface gravity)", "default": 1.0, "min": 0.1, "max": 5.0, "step": 0.05}
  ],

  // ── Optional display hints ──────────────────────────────────────────────────
  "log_x": false,
  "log_y": false
}

Rules:
- Variable names must be valid Python identifiers: omega not ω, kappa not κ, hbar not ℏ.
- All expressions use numpy available as `np` (np.sin, np.exp, np.sqrt, np.pi, etc.).
- Guard against singularities: add a tiny epsilon (+ 1e-30) inside denominators, or
  start ranges just past the pole, or use np.where to mask undefined regions.
- Choose ranges that reveal the interesting physics — show the characteristic scale,
  the peak, the asymptotes, the oscillations.
- Natural units: set ℏ = c = k_B = G = 1 unless the equation is explicitly dimensional;
  expose them as adjustable parameters when physically meaningful.
- For thermal/quantum distributions, sweep energy or frequency on the x-axis.
- For wave functions, show 2–3 oscillation periods.
- Surface plots: use 80×80 grid points by default.
- Prefer log_y: true for quantities spanning many decades (spectra, decay rates).
- The `parameters` list may be empty if the function has no free parameters.
"""


def interpret(equation_text: str) -> dict:
    """Call Claude to parse an equation into a visualization spec."""
    resp = _CLIENT.messages.create(
        model="claude-opus-4-7",
        max_tokens=1200,
        system=_SYSTEM,
        messages=[{"role": "user", "content": equation_text}],
    )
    raw = resp.content[0].text.strip()
    # Strip any markdown fences the model might add despite instructions
    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
    raw = re.sub(r"\n?```\s*$", "", raw)
    return json.loads(raw)
