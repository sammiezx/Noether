"""Evaluate a visualization spec and return a Plotly figure dict.

The `build_figure(spec, params)` function is the public interface.
It evaluates the numpy expression from the spec with the given parameter
values and returns a Plotly figure as a plain dict (JSON-serializable).
"""

from __future__ import annotations

import json
import warnings

import numpy as np
import plotly.graph_objects as go

# Safe evaluation namespace — only numpy and math constants, no builtins.
_MATH_ENV: dict = {
    "np": np,
    "pi": np.pi,
    "e": np.e,
    "inf": np.inf,
    "nan": np.nan,
    # Convenience aliases
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "exp": np.exp, "log": np.log, "log10": np.log10, "log2": np.log2,
    "sqrt": np.sqrt, "cbrt": np.cbrt, "abs": np.abs,
    "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    "arcsin": np.arcsin, "arccos": np.arccos, "arctan": np.arctan,
    "arctan2": np.arctan2, "power": np.power,
    "real": np.real, "imag": np.imag, "conj": np.conj,
    "sign": np.sign, "floor": np.floor, "ceil": np.ceil,
    "sinc": np.sinc, "factorial": __import__("math").factorial,
    "__builtins__": {},
}


def _eval(expr: str, local_vars: dict) -> np.ndarray:
    """Evaluate a numpy expression string. Returns a real-valued ndarray with
    NaN where the expression is infinite, complex, or undefined."""
    env = {**_MATH_ENV, **local_vars}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with np.errstate(all="ignore"):
            result = eval(expr, env)  # noqa: S307 — local-only application
    arr = np.asarray(result, dtype=complex)
    real = np.real(arr).astype(float)
    return np.where(np.isfinite(real), real, np.nan)


def _axis(ax_spec: dict) -> np.ndarray:
    lo, hi = ax_spec["range"]
    n = ax_spec.get("points", 400)
    return np.linspace(lo, hi, n)


def _dark_layout(**extra) -> dict:
    base = dict(
        paper_bgcolor="rgba(10,10,20,0)",
        plot_bgcolor="rgba(10,10,20,0.6)",
        font=dict(color="#CBD5E1", family="Inter, system-ui, sans-serif", size=13),
        margin=dict(l=65, r=25, t=45, b=65),
        hoverlabel=dict(bgcolor="#0f172a", font_color="#e2e8f0"),
    )
    base.update(extra)
    return base


def _line(spec: dict, params: dict) -> go.Figure:
    x_spec = spec["x"]
    x = _axis(x_spec)
    y = _eval(spec["expression"], {x_spec["name"]: x, **params})

    fig = go.Figure(go.Scatter(
        x=x, y=y,
        mode="lines",
        line=dict(color="#60a5fa", width=2.5),
        connectgaps=False,
    ))
    fig.update_layout(
        **_dark_layout(),
        title=dict(text=spec.get("title", ""), font=dict(size=15, color="#94a3b8")),
        xaxis=dict(
            title=x_spec.get("label", x_spec["name"]),
            gridcolor="#1e293b", zerolinecolor="#334155",
            type="log" if spec.get("log_x") else "linear",
        ),
        yaxis=dict(
            title=spec.get("y_label", ""),
            gridcolor="#1e293b", zerolinecolor="#334155",
            type="log" if spec.get("log_y") else "linear",
        ),
    )
    return fig


def _surface(spec: dict, params: dict) -> go.Figure:
    x = _axis(spec["x"])
    y = _axis(spec["y"])
    X, Y = np.meshgrid(x, y)
    Z = _eval(spec["expression"], {
        spec["x"]["name"]: X,
        spec["y"]["name"]: Y,
        **params,
    })

    fig = go.Figure(go.Surface(
        x=x, y=y, z=Z,
        colorscale=spec.get("color_scale", "Viridis"),
        showscale=True,
        colorbar=dict(tickfont=dict(color="#94a3b8")),
    ))
    fig.update_layout(
        **_dark_layout(),
        scene=dict(
            xaxis=dict(title=spec["x"].get("label", "x"), gridcolor="#1e293b", backgroundcolor="rgba(10,10,20,0)"),
            yaxis=dict(title=spec["y"].get("label", "y"), gridcolor="#1e293b", backgroundcolor="rgba(10,10,20,0)"),
            zaxis=dict(title=spec.get("z_label", ""), gridcolor="#1e293b", backgroundcolor="rgba(10,10,20,0)"),
            bgcolor="rgba(10,10,20,0.8)",
        ),
    )
    return fig


def _parametric2d(spec: dict, params: dict) -> go.Figure:
    t = _axis(spec["t"])
    local = {spec["t"]["name"]: t, **params}
    x = _eval(spec["x_expr"], local)
    y = _eval(spec["y_expr"], local)

    fig = go.Figure(go.Scatter(
        x=x, y=y, mode="lines",
        line=dict(color="#60a5fa", width=2.5),
    ))
    fig.update_layout(
        **_dark_layout(),
        xaxis=dict(title=spec.get("x_label", "x"), gridcolor="#1e293b", zerolinecolor="#334155"),
        yaxis=dict(title=spec.get("y_label", "y"), gridcolor="#1e293b", zerolinecolor="#334155"),
    )
    return fig


def _parametric3d(spec: dict, params: dict) -> go.Figure:
    t = _axis(spec["t"])
    local = {spec["t"]["name"]: t, **params}
    x = _eval(spec["x_expr"], local)
    y = _eval(spec["y_expr"], local)
    z = _eval(spec["z_expr"], local)

    colorvals = np.linspace(0, 1, len(t))
    fig = go.Figure(go.Scatter3d(
        x=x, y=y, z=z, mode="lines",
        line=dict(color=colorvals, colorscale="Plasma", width=4),
    ))
    fig.update_layout(
        **_dark_layout(),
        scene=dict(
            xaxis=dict(title=spec.get("x_label", "x"), backgroundcolor="rgba(10,10,20,0)"),
            yaxis=dict(title=spec.get("y_label", "y"), backgroundcolor="rgba(10,10,20,0)"),
            zaxis=dict(title=spec.get("z_label", "z"), backgroundcolor="rgba(10,10,20,0)"),
            bgcolor="rgba(10,10,20,0.8)",
        ),
    )
    return fig


_BUILDERS = {
    "line": _line,
    "surface": _surface,
    "parametric2d": _parametric2d,
    "parametric3d": _parametric3d,
}


_BDATA_DTYPES = {"f8": np.float64, "f4": np.float32, "i4": np.int32, "i8": np.int64}

import base64 as _base64


def _decode_bdata(d: dict) -> list:
    """Decode a plotly-6 bdata blob into a plain Python list."""
    raw = _base64.b64decode(d["bdata"])
    dtype = _BDATA_DTYPES.get(d.get("dtype", "f8"), np.float64)
    arr = np.frombuffer(raw, dtype=dtype)
    return [None if not np.isfinite(v) else float(v) for v in arr]


def _to_serializable(obj):
    """Recursively convert plotly-6 internal formats to plain JSON-safe types."""
    if isinstance(obj, dict):
        if "bdata" in obj:              # plotly-6 binary-encoded array
            return _decode_bdata(obj)
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, np.ndarray):
        return [None if not np.isfinite(v) else float(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(v) for v in obj]
    return obj


def build_figure(spec: dict, params: dict) -> dict:
    """Build a Plotly figure from a spec + parameter values.

    Returns the figure as a plain JSON-serializable dict (no bdata blobs).
    """
    plot_type = spec.get("plot_type", "line")
    builder = _BUILDERS.get(plot_type)
    if builder is None:
        raise ValueError(f"Unknown plot_type: {plot_type!r}")
    fig = builder(spec, params)
    return _to_serializable(fig.to_dict())
