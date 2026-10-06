"""
FastAPI backend for the BNPL model — a THIN JSON wrapper around the (unchanged)
`bnpl_model` engine. It contains NO financial logic: it merges the incoming
parameters onto the default config, calls `run_model`, and serialises the result
frames to JSON. It also describes the UI controls (`/api/schema`) so the React app
can render them, and streams the Excel export.

Run (after building the frontend):
    python -m uvicorn server.main:app --reload --port 8000
then open http://localhost:8000
"""
from __future__ import annotations

import io
import json
import math
import os
from typing import Any, Dict, List

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from bnpl_model import EngineConfig, run_model
from bnpl_model.config import preset_default
from bnpl_model.ifrs9 import lifetime_pd_stage2
from bnpl_model.export import to_excel_bytes

app = FastAPI(title="BNPL Model API", version="1.0.0")

# CORS so the Vite dev server (localhost:5173) can call the API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(os.path.dirname(HERE), "web", "dist")


# --------------------------------------------------------------------- helpers
def _records(df: pd.DataFrame, reset_index: bool = True) -> list[dict]:
    """DataFrame -> list of row dicts with native JSON types (NaN -> null)."""
    if df is None or len(df) == 0:
        return []
    d = df.reset_index() if reset_index else df
    return json.loads(d.to_json(orient="records"))


def _parse_curve(text: str) -> List[float]:
    """Parse a comma/semicolon-separated marginal default curve string from the UI.

    Rejects empty input, non-numeric tokens, and non-finite or out-of-[0,1] values
    with a clean 400 (otherwise NaN/inf would slip through to a 500 at JSON encode,
    and out-of-range values would silently mis-shape the curve)."""
    tokens = [x.strip() for x in text.replace(";", ",").split(",") if x.strip() != ""]
    if not tokens:
        raise HTTPException(status_code=400,
                            detail="marginal_default_curve must list at least one number")
    out: List[float] = []
    for tok in tokens:
        try:
            v = float(tok)
        except ValueError:
            raise HTTPException(status_code=400,
                                detail=f"marginal_default_curve: '{tok}' is not a number")
        if not math.isfinite(v) or not (0.0 <= v <= 1.0):
            raise HTTPException(status_code=400,
                                detail=f"marginal_default_curve values must be between 0 and 1 (got '{tok}')")
        out.append(v)
    return out


def _build_config(params: Dict[str, Any]) -> EngineConfig:
    params = params or {}
    cfg = preset_default().to_dict()
    # only override known keys; ignore unknowns from the client
    for k, v in params.items():
        if k in cfg:
            cfg[k] = v
    # opening cash tracks equity (the UI exposes a single "initial equity" lever)
    cfg["initial_cash"] = cfg.get("initial_equity", cfg["initial_cash"])

    # --- marginal default curve: accept a comma/semicolon-separated string from the UI ---
    mdc = cfg.get("marginal_default_curve")
    if isinstance(mdc, str):
        cfg["marginal_default_curve"] = _parse_curve(mdc)

    # --- Stage-2 lifetime PD method (UI-only control, not an EngineConfig field) ---
    # "roll_matrix" / "marginal_curve" -> derive (override = None) by that method;
    # "fixed" -> use the slider value.
    mode = params.get("pd_lifetime_stage2_mode")
    if mode in ("roll_matrix", "marginal_curve"):
        cfg["pd_lifetime_stage2_override"] = None
        cfg["pd_stage2_method"] = mode
    # if mode == "fixed" (or unset), keep whatever pd_lifetime_stage2_override was merged in
    # normalise macro scenario probabilities to sum to 1 (lenient input)
    tot = cfg["scenario_prob_base"] + cfg["scenario_prob_upside"] + cfg["scenario_prob_downside"]
    if tot > 0:
        cfg["scenario_prob_base"] /= tot
        cfg["scenario_prob_upside"] /= tot
        cfg["scenario_prob_downside"] /= tot
    else:
        cfg["scenario_prob_base"], cfg["scenario_prob_upside"], cfg["scenario_prob_downside"] = 0.5, 0.2, 0.3
    try:
        # keep installment weights consistent with the installment count
        n = int(cfg["n_installments"])
        if n < 1:
            raise ValueError("n_installments must be >= 1")
        cfg["installment_weights"] = [1.0 / n] * n
        return EngineConfig(**cfg).validate()
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _kpis(res) -> dict:
    isf, pf = res.income_statement, res.portfolio
    ni = isf["net_income"]
    breakeven = next((int(m) for m in ni.index if ni.loc[m] > 0), None)
    rep = res.invariant_report
    artic = rep[rep["category"] == "articulation"]
    feas_fail = rep[(rep["category"] == "feasibility") & (~rep["passed"])]
    return {
        "cumulative_net_income": float(ni.sum()),
        "final_net_income": float(ni.iloc[-1]),
        "breakeven_month": breakeven,
        "final_npl_ratio": float(pf["npl_ratio"].iloc[-1]),
        "final_ecl_coverage": float(pf["ecl_coverage"].iloc[-1]),
        "final_loans_originated": float(pf["loans_originated"].iloc[-1]),
        "cumulative_loans": float(pf["cumulative_loans"].iloc[-1]),
        "final_gross_receivable": float(pf["gross_receivable"].iloc[-1]),
        "articulation_ok": bool(artic["passed"].all()),
        "feasibility_failures": feas_fail["id"].tolist(),
    }


# ----------------------------------------------------------------------- routes
@app.get("/api/schema")
def schema():
    """Default parameter values + the control metadata that drives the UI."""
    cfg = preset_default()
    defaults = cfg.to_dict()
    # Stage-2 lifetime PD method: default to deriving from the marginal curve (the
    # engine's behaviour when no override is set). Seed the fixed-point slider with
    # the derived value so switching to "fixed" starts consistent.
    defaults["pd_lifetime_stage2_mode"] = (
        "fixed" if defaults.get("pd_lifetime_stage2_override") is not None
        else defaults["pd_stage2_method"])
    if defaults.get("pd_lifetime_stage2_override") is None:
        # snap the seed to the slider step (0.005) so the control can represent it exactly
        defaults["pd_lifetime_stage2_override"] = round(round(lifetime_pd_stage2(cfg) / 0.005) * 0.005, 4)
    # expose the marginal default curve as an editable comma-separated string
    curve = defaults.get("marginal_default_curve") or []
    defaults["marginal_default_curve"] = ", ".join(f"{x:g}" for x in curve)
    return {"defaults": defaults, "groups": CONTROL_GROUPS}


@app.post("/api/run")
def run(params: Dict[str, Any] | None = None):
    cfg = _build_config(params or {})
    res = run_model(cfg)
    return {
        "config": cfg.to_dict(),
        # The Stage-2 lifetime PD actually used (derived from the curve, or the fixed
        # override). The UI syncs the fixed slider to this in curve mode so switching
        # methods doesn't jump the value.
        "pd_lifetime_stage2_used": float(lifetime_pd_stage2(cfg)),
        "kpis": _kpis(res),
        "income_statement": _records(res.income_statement),
        "cash_flow": _records(res.cash_flow),
        "balance_sheet": _records(res.balance_sheet),
        "portfolio": _records(res.portfolio),
        "metrics": _records(res.metrics),
        "stage_detail": _records(res.stage_detail, reset_index=False),
        "vintage": _records(res.vintage, reset_index=False),
        "invariants": _records(res.invariant_report, reset_index=False),
    }


@app.post("/api/export")
def export(params: Dict[str, Any] | None = None):
    cfg = _build_config(params or {})
    res = run_model(cfg)
    data = to_excel_bytes(res)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="bnpl_model.xlsx"'},
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---- serve the built React app (production / single-port run) ----
if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(DIST, "index.html"))

    @app.get("/{path:path}")
    def spa(path: str):
        # resolve and confine to DIST: an absolute path or ../ must not escape it
        root = os.path.realpath(DIST)
        candidate = os.path.realpath(os.path.join(root, path))
        try:
            inside = os.path.commonpath([root, candidate]) == root
        except ValueError:                     # different drive on Windows
            inside = False
        if inside and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(DIST, "index.html"))  # SPA fallback


# =========================================================================
# UI control metadata (drives the React sidebar). Pure presentation config —
# the engine's parameter semantics live in bnpl_model/config.py.
# =========================================================================
CONTROL_GROUPS = [
    {"title": "Product & schedule", "icon": "package", "fields": [
        {"key": "n_installments", "label": "Number of installments", "type": "number", "min": 1, "max": 24, "step": 1},
        {"key": "cadence_mode", "label": "Cadence", "type": "select", "options": ["biweekly", "monthly", "custom_days"]},
        {"key": "cadence_days", "label": "Days between installments", "type": "number", "min": 1, "max": 120, "step": 1,
         "showIf": {"key": "cadence_mode", "equals": "custom_days"}},
        {"key": "apr", "label": "APR (0 = interest-free)", "type": "slider", "min": 0, "max": 0.6, "step": 0.01, "fmt": "pct"},
        {"key": "horizon_months", "label": "Horizon (months)", "type": "slider", "min": 6, "max": 60, "step": 1},
    ]},
    {"title": "Revenue & fees", "icon": "dollar", "fields": [
        {"key": "merchant_fee_pct", "label": "Merchant fee (% of order)", "type": "slider", "min": 0, "max": 0.15, "step": 0.005, "fmt": "pct"},
        {"key": "merchant_fee_recognition", "label": "Merchant fee recognition", "type": "select", "options": ["upfront", "deferred_straight_line"]},
        {"key": "consumer_fee_mode", "label": "Consumer fee mode", "type": "select", "options": ["none", "fixed", "pct"]},
        {"key": "consumer_fee_fixed", "label": "Consumer fee ($/loan)", "type": "number", "min": 0, "max": 100, "step": 0.5,
         "showIf": {"key": "consumer_fee_mode", "equals": "fixed"}},
        {"key": "consumer_fee_pct", "label": "Consumer fee (% of order)", "type": "slider", "min": 0, "max": 0.1, "step": 0.005, "fmt": "pct",
         "showIf": {"key": "consumer_fee_mode", "equals": "pct"}},
        {"key": "consumer_fee_recognition", "label": "Consumer fee recognition", "type": "select", "options": ["deferred_straight_line", "upfront"]},
        {"key": "late_fee_amount", "label": "Late fee ($/missed)", "type": "number", "min": 0, "max": 100, "step": 1},
        {"key": "late_fee_collection_rate", "label": "Late fee collection rate", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
    ]},
    {"title": "Origination & growth", "icon": "trend", "fields": [
        {"key": "loans_origination_t0", "label": "Loans originated, month 0", "type": "number", "min": 0, "max": 1000000, "step": 100},
        {"key": "origination_growth_mode", "label": "Growth mode", "type": "select", "options": ["compound", "linear"]},
        {"key": "origination_growth_mom", "label": "MoM origination growth", "type": "slider", "min": -0.1, "max": 0.3, "step": 0.005, "fmt": "pct"},
        {"key": "avg_order_value", "label": "Average order value ($)", "type": "number", "min": 10, "max": 5000, "step": 5},
        {"key": "aov_growth_mom", "label": "MoM AOV drift", "type": "slider", "min": -0.05, "max": 0.05, "step": 0.005, "fmt": "pct"},
    ]},
    {"title": "Credit, roll-rates & recovery", "icon": "alert", "fields": [
        {"key": "first_payment_default_rate", "label": "First-payment default rate", "type": "slider", "min": 0, "max": 0.4, "step": 0.005, "fmt": "pct"},
        {"key": "base_miss_rate", "label": "Base installment miss rate", "type": "slider", "min": 0, "max": 0.3, "step": 0.005, "fmt": "pct"},
        {"key": "charge_off_dpd", "label": "Charge-off DPD", "type": "select", "options": [90, 120, 180, 365]},
        {"key": "recovery_rate", "label": "Recovery rate (of charged-off)", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "recovery_lag_months", "label": "Recovery lag (months)", "type": "slider", "min": 0, "max": 18, "step": 1},
        {"key": "recovery_accounting", "label": "Recovery accounting", "type": "select", "options": ["pnl_other_income", "reverse_allowance"]},
        {"key": "prepayment_rate", "label": "Monthly prepayment rate", "type": "slider", "min": 0, "max": 0.2, "step": 0.005, "fmt": "pct"},
    ]},
    {"title": "IFRS 9 — staging & ECL", "icon": "bank", "fields": [
        {"key": "staging_basis", "label": "Staging basis", "type": "select", "options": ["tight_bnpl", "standard"]},
        {"key": "pd_12m_stage1", "label": "PD Stage 1 (12m)", "type": "slider", "min": 0, "max": 0.3, "step": 0.005, "fmt": "pct"},
        {"key": "pd_lifetime_stage2_mode", "label": "PD Stage 2 (lifetime) method", "type": "select",
         "options": ["roll_matrix", "marginal_curve", "fixed"],
         "help": "How the Stage-2 lifetime PD is SET: per bucket from the roll matrix (consistent with "
                 "realised charge-offs), from the marginal default curve below, or a fixed value."},
        {"key": "pd_lifetime_stage2_override", "label": "PD Stage 2 (lifetime) — fixed", "type": "slider",
         "min": 0, "max": 0.6, "step": 0.005, "fmt": "pct",
         "showIf": {"key": "pd_lifetime_stage2_mode", "equals": "fixed"}},
        {"key": "marginal_default_curve", "label": "Marginal default curve (per month, comma-separated)",
         "type": "text",
         "help": "Monthly marginal default rates (0-1). Always shapes WHEN defaults occur in the cohort engine; "
                 "in 'marginal_curve' method it also sets the Stage-2 lifetime PD = 1 - product(1 - rate)."},
        {"key": "lgd_stage1", "label": "LGD Stage 1", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "lgd_stage2", "label": "LGD Stage 2", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "lgd_s3_substandard", "label": "LGD S3 Substandard", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "lgd_s3_doubtful", "label": "LGD S3 Doubtful", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "lgd_s3_loss", "label": "LGD S3 Loss", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "management_overlay_pct", "label": "Management overlay (% on ECL)", "type": "slider", "min": -0.5, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "override_forborne_fraction", "label": "Forborne → Stage 2", "type": "slider", "min": 0, "max": 0.5, "step": 0.01, "fmt": "pct"},
        {"key": "override_score_deterioration_fraction", "label": "Score drop → Stage 2", "type": "slider", "min": 0, "max": 0.5, "step": 0.01, "fmt": "pct"},
        {"key": "override_fraud_fraction", "label": "Fraud → Stage 3", "type": "slider", "min": 0, "max": 0.2, "step": 0.005, "fmt": "pct"},
        {"key": "override_bankruptcy_fraction", "label": "Bankruptcy → Stage 3", "type": "slider", "min": 0, "max": 0.2, "step": 0.005, "fmt": "pct"},
    ]},
    {"title": "Macro overlay", "icon": "globe", "fields": [
        {"key": "scenario_prob_base", "label": "P(base)", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "scenario_prob_upside", "label": "P(upside)", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "scenario_prob_downside", "label": "P(downside)", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "pd_multiplier_upside", "label": "Upside PD multiplier", "type": "slider", "min": 0.2, "max": 1, "step": 0.05},
        {"key": "pd_multiplier_downside", "label": "Downside PD multiplier", "type": "slider", "min": 1, "max": 3, "step": 0.05},
        {"key": "lgd_multiplier_upside", "label": "Upside LGD multiplier", "type": "slider", "min": 0.5, "max": 1, "step": 0.05},
        {"key": "lgd_multiplier_downside", "label": "Downside LGD multiplier", "type": "slider", "min": 1, "max": 2, "step": 0.05},
    ]},
    {"title": "Funding, opex & tax", "icon": "building", "fields": [
        {"key": "funding_advance_rate", "label": "Funding advance rate", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct"},
        {"key": "funding_rate_apr", "label": "Cost of funds (APR)", "type": "slider", "min": 0, "max": 0.3, "step": 0.005, "fmt": "pct"},
        {"key": "min_cash_buffer", "label": "Minimum cash buffer ($)", "type": "number", "min": 0, "max": 50000000, "step": 50000},
        {"key": "initial_equity", "label": "Initial equity ($)", "type": "number", "min": 0, "max": 500000000, "step": 500000},
        {"key": "opex_fixed_monthly", "label": "Fixed opex / month ($)", "type": "number", "min": 0, "max": 50000000, "step": 5000},
        {"key": "opex_per_loan_origination", "label": "Variable opex / loan ($)", "type": "number", "min": 0, "max": 100, "step": 0.5},
        {"key": "tax_rate", "label": "Tax rate", "type": "slider", "min": 0, "max": 0.5, "step": 0.01, "fmt": "pct"},
        {"key": "dividend_policy", "label": "Dividend policy", "type": "select", "options": ["none", "payout_pct"]},
        {"key": "dividend_payout_pct", "label": "Dividend payout (% of NI)", "type": "slider", "min": 0, "max": 1, "step": 0.05, "fmt": "pct",
         "showIf": {"key": "dividend_policy", "equals": "payout_pct"}},
    ]},
]
