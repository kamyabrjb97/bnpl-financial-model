"""
Operational + financial metrics (Section G of the spec).

All annualised ratios use the average-of-opening-and-closing balance convention
and an annualisation factor of 12 for monthly periods.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _avg(series: pd.Series) -> pd.Series:
    """Average of opening (prior) and closing balance. For the first period the
    opening balance is the closing balance (not zero), so a ramp-from-zero book does
    not halve the denominator and inflate annualised ratios in month 0."""
    prev = series.shift(1)
    prev.iloc[0] = series.iloc[0]
    return (prev + series) / 2.0


def _safe(num, den):
    den = den.replace(0, np.nan)
    return (num / den).fillna(0.0)


def build_metrics(results) -> pd.DataFrame:
    isf = results.income_statement
    cf = results.cash_flow
    bs = results.balance_sheet
    pf = results.portfolio

    avg_gross = _avg(pf["gross_receivable"])
    avg_debt = _avg(pf["debt"])
    avg_assets = _avg(bs["total_assets"])
    avg_equity = _avg(bs["total_equity"])

    fee_income = (isf["merchant_fee_income"] + isf["consumer_fee_income"]
                  + isf["late_fee_income"] + isf["interest_income"])

    m = pd.DataFrame(index=pf.index)
    m["loans_originated"] = pf["loans_originated"]
    m["cumulative_loans"] = pf["cumulative_loans"]
    m["active_loans"] = pf["active_loans"]
    m["gmv"] = pf["gmv"]
    m["aov"] = _safe(pf["gmv"], pf["loans_originated"])
    m["take_rate"] = _safe(fee_income, pf["gmv"])
    m["portfolio_yield_ann"] = _safe(12 * fee_income, avg_gross)
    m["nim_ann"] = _safe(12 * (isf["interest_income"] - isf["funding_cost"]), avg_gross)
    m["npl_ratio"] = pf["npl_ratio"]
    m["ecl_coverage"] = pf["ecl_coverage"]
    m["stage1_pct"] = _safe(pf["stage1_balance"], pf["gross_receivable"])
    m["stage2_pct"] = _safe(pf["stage2_balance"], pf["gross_receivable"])
    m["stage3_pct"] = _safe(pf["stage3_balance"], pf["gross_receivable"])
    m["charge_off_rate_ann"] = _safe(12 * pf["write_offs"], avg_gross)
    m["net_charge_off_rate_ann"] = _safe(12 * (pf["write_offs"] - pf["recoveries"]), avg_gross)
    m["cost_of_risk_ann"] = _safe(12 * pf["provision_charge"], avg_gross)
    m["cost_of_funds_ann"] = _safe(12 * isf["funding_cost"], avg_debt)
    m["opex_ratio"] = _safe(isf["operating_expense"], isf["total_revenue"])
    m["net_margin"] = _safe(isf["net_income"], isf["total_revenue"])
    m["roa_ann"] = _safe(12 * isf["net_income"], avg_assets)
    m["roe_ann"] = _safe(12 * isf["net_income"], avg_equity)
    m["leverage"] = _safe(pf["debt"], bs["total_equity"])
    m["funding_utilization"] = _safe(pf["debt"], pf["debt_cap"])
    # active_loans is a balance-weighted estimate; for products with no financed
    # receivable (e.g. n_installments=1) it is ~0, so fall back to originated loans
    # to avoid a misleading zero per-loan figure.
    denom_loans = pf["active_loans"].where(pf["active_loans"] > 1e-6, pf["loans_originated"])
    m["net_revenue_per_loan"] = _safe(isf["net_interest_fee_income"] - pf["provision_charge"],
                                      denom_loans)

    # recovery rate (cumulative run-rate, lower bound — recoveries lag charge-offs)
    cum_wo = pf["write_offs"].cumsum()
    cum_rec = pf["recoveries"].cumsum()
    m["recovery_rate_runrate"] = _safe(cum_rec, cum_wo)
    return m
