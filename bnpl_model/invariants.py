"""
Invariant / tie-out checks (Section H of the spec).

These are the proof that the model articulates. `check_invariants` returns a tidy
report; `assert_invariants` raises on any failure (used by the test-suite).
The "big six" articulation checks are H-BS, H-CASH, H-CFO, H-ALW1, H-ALW2, H-COH.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .ifrs9 import LIVE_BUCKETS, BUCKET_LOWER_DPD

TOL = 1e-6


def _rel_ok(residual, scale, tol=TOL):
    return abs(residual) <= tol * max(1.0, abs(scale))


def check_invariants(results) -> pd.DataFrame:
    cfg = results.config
    isf = results.income_statement
    cf = results.cash_flow
    bs = results.balance_sheet
    pf = results.portfolio
    stage = results.stage_detail
    T = len(bs)

    checks = []

    def add(cid, desc, max_resid, ok):
        checks.append(dict(id=cid, description=desc, max_residual=float(max_resid), passed=bool(ok)))

    # H-BS: balance sheet balances
    resid = bs["balance_check"].abs()
    scale = bs["total_assets"].abs().clip(lower=1.0)
    add("H-BS", "Assets == Liabilities + Equity",
        resid.max(), bool((resid <= TOL * scale).all()))

    # H-CASH: CF ending cash == BS cash
    r = (cf["ending_cash"] - bs["cash"]).abs()
    add("H-CASH", "CF ending cash == BS cash", r.max(),
        bool((r <= TOL * bs["cash"].abs().clip(lower=1.0)).all()))

    # H-CFO: indirect == direct CFO
    r = (cf["cfo"] - cf["cfo_indirect"]).abs()
    add("H-CFO", "CFO indirect == CFO direct", r.max(),
        bool((r <= 1e-3 * cf["cfo"].abs().clip(lower=1.0)).all()))

    # H-ALW1: allowance roll-forward ties
    allow = -bs["ecl_allowance"]            # positive allowance
    allow_prev = allow.shift(1).fillna(0.0)
    rec_alw = pf["recoveries"] if cfg.recovery_accounting == "reverse_allowance" else pd.Series(0.0, index=pf.index)
    roll = allow_prev + pf["provision_charge"] - pf["write_offs"] + rec_alw
    r = (allow - roll).abs()
    add("H-ALW1", "Allowance roll-forward (A = A_prev + prov - WO + rec)", r.max(),
        bool((r <= 1e-3 * allow.abs().clip(lower=1.0)).all()))

    # H-ALW2: allowance == required ECL
    r = (allow - pf["required_ecl"]).abs()
    add("H-ALW2", "Allowance == bottom-up required ECL", r.max(),
        bool((r <= 1e-3 * allow.abs().clip(lower=1.0)).all()))

    # H-COH: stage balances sum to gross
    stagesum = pf["stage1_balance"] + pf["stage2_balance"] + pf["stage3_balance"]
    r = (stagesum - pf["gross_receivable"]).abs()
    add("H-COH", "Stage balances sum to gross receivable", r.max(),
        bool((r <= 1e-4 * pf["gross_receivable"].abs().clip(lower=1.0)).all()))

    # H-NET: net == gross - allowance
    r = (bs["net_receivables"] - (pf["gross_receivable"] - allow)).abs()
    add("H-NET", "Net receivable == gross - allowance", r.max(), bool((r <= 1e-3).all()))

    # H-RE: retained earnings roll
    re = bs["retained_earnings"]
    re_prev = re.shift(1).fillna(0.0)
    r = (re - (re_prev + isf["net_income"] - isf["dividends"])).abs()
    add("H-RE", "Retained earnings = prior + NI - dividends", r.max(),
        bool((r <= 1e-3 * re.abs().clip(lower=1.0)).all()))

    # H-CASHCONT: cash continuity (cash flow reconciles from zero; equity is a t=0 inflow)
    cash_prev = bs["cash"].shift(1).fillna(0.0)
    r = (bs["cash"] - (cash_prev + cf["net_change_cash"])).abs()
    add("H-CASHCONT", "Cash[t] == Cash[t-1] + net change", r.max(),
        bool((r <= 1e-3 * bs["cash"].abs().clip(lower=1.0)).all()))

    # H-CASHNEG: cash stays non-negative unless overdraft is explicitly allowed
    min_cash = bs["cash"].min()
    add("H-CASHNEG", "Cash >= 0 (unless allow_overdraft)",
        max(0.0, -min_cash), bool(cfg.allow_overdraft or (bs["cash"] >= -1e-6).all()))

    # H-FEE: deferred-fee liability never negative (no over-recognition of fees)
    dfi = bs["deferred_fee_income"]
    add("H-FEE", "Deferred fee income (liability) >= 0",
        max(0.0, -dfi.min()), bool((dfi >= -1e-6).all()))

    # H-CO: no live balance in buckets at/over the charge-off edge
    over_edge = stage[stage["dpd_lower"] >= cfg.charge_off_dpd]
    max_live = over_edge["balance"].max() if len(over_edge) else 0.0
    add("H-CO", "No live balance past charge-off edge",
        max_live if not np.isnan(max_live) else 0.0, (max_live <= 1e-6) or np.isnan(max_live))

    # H-NPL: ratio bounds
    ok_npl = bool(((pf["npl_ratio"] >= -1e-9) & (pf["npl_ratio"] <= 1 + 1e-9)).all()
                  and ((pf["ecl_coverage"] >= -1e-9) & (pf["ecl_coverage"] <= 1 + 1e-9)).all())
    add("H-NPL", "0 <= NPL ratio, ECL coverage <= 1", 0.0, ok_npl)

    # H-NAN: everything finite
    allf = all(np.isfinite(df.select_dtypes("number").to_numpy()).all()
               for df in (isf, cf, bs, pf))
    add("H-NAN", "All statement cells finite", 0.0, allf)

    # H-S3PD: Stage-3 ECL uses PD = 1.0 exactly: ecl == EAD * DF * (1+overlay) *
    # sum_s prob_s * min(1, LGD * lgd_mult_s), and the reported base PD is 1.0
    s3 = stage[(stage["stage"] == 3) & (stage["balance"] > 0)]   # empty rows report pd=0
    if len(s3):
        from .ifrs9 import IFRS9Engine
        eng = IFRS9Engine(cfg)
        df_ = eng.discount_factor() * (1.0 + cfg.management_overlay_pct)
        lgd_w = sum(prob * np.minimum(1.0, s3["lgd"] * lgd_mult)
                    for _n, prob, _pdm, lgd_mult in eng.scenarios)
        implied = s3["balance"] * lgd_w * df_
        r3 = (s3["ecl"] - implied).abs()
        pd_ok = bool(((s3["pd"] - 1.0).abs() <= 1e-12).all())
        add("H-S3PD", "Stage-3 ECL == LGD*EAD (PD=1)", r3.max(),
            pd_ok and bool((r3 <= TOL * implied.abs().clip(lower=1.0)).all()))

    df = pd.DataFrame(checks)
    # Feasibility flags are business signals (e.g. undercapitalisation), NOT
    # articulation tie-outs; separate them so a feasibility warning isn't read as a
    # broken model. Articulation/tie-out checks must always pass.
    FEASIBILITY = {"H-CASHNEG", "H-NPL"}
    df["category"] = df["id"].apply(lambda i: "feasibility" if i in FEASIBILITY else "articulation")
    return df


# tie-out checks that must hold for the model to be internally consistent
ARTICULATION_IDS = None  # populated lazily below


def assert_invariants(results, articulation_only: bool = True):
    """Raise if any invariant fails. By default only the *articulation* tie-outs are
    enforced; feasibility flags (negative cash, NPL bounds) are reported but do not
    raise, since an undercapitalised-but-correctly-articulated scenario is valid input."""
    rep = results.invariant_report if len(results.invariant_report) else check_invariants(results)
    scope = rep[rep["category"] == "articulation"] if articulation_only else rep
    failed = scope[~scope["passed"]]
    if len(failed):
        lines = "\n".join(f"  - {r.id}: {r.description} (max residual {r.max_residual:.4g})"
                          for r in failed.itertuples())
        raise AssertionError(f"{len(failed)} invariant(s) failed:\n{lines}")
    return True
