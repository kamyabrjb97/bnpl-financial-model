"""
BNPL Financial Model — interactive Streamlit dashboard.

Sidebar controls bind to every EngineConfig field; the model re-runs live and the
three financial statements, portfolio/IFRS 9 views, metrics, scenario comparison
and Excel export all update. Run with:

    streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import json
import sys
import os

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

# allow `streamlit run app/streamlit_app.py` from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bnpl_model import EngineConfig, run_model            # noqa: E402
from bnpl_model.config import preset_default              # noqa: E402
from bnpl_model.ifrs9 import LIVE_BUCKETS                 # noqa: E402
from bnpl_model.export import to_excel_bytes              # noqa: E402

st.set_page_config(page_title="BNPL Financial Model", layout="wide",
                   initial_sidebar_state="expanded")

ACCENT = "#4F46E5"
GREEN = "#059669"
RED = "#DC2626"
AMBER = "#D97706"


@st.cache_data(show_spinner=False)
def run_cached(cfg_json: str):
    cfg = EngineConfig(**json.loads(cfg_json))
    return run_model(cfg)


def money(x, dollars=True):
    sign = "-" if x < 0 else ""
    x = abs(x)
    pfx = "$" if dollars else ""
    if x >= 1e9:
        return f"{sign}{pfx}{x/1e9:.2f}B"
    if x >= 1e6:
        return f"{sign}{pfx}{x/1e6:.2f}M"
    if x >= 1e3:
        return f"{sign}{pfx}{x/1e3:.1f}K"
    return f"{sign}{pfx}{x:,.0f}"


# ===================================================================== sidebar
def build_config() -> EngineConfig:
    d = preset_default().to_dict()
    s = st.sidebar
    s.title("⚙️ Model Assumptions")
    s.caption("Every control re-runs the model live.")

    with s.expander("📦 Product & schedule", expanded=True):
        d["n_installments"] = st.number_input("Number of installments", 1, 24, int(d["n_installments"]))
        cad = st.selectbox("Cadence", ["biweekly", "monthly", "custom_days"],
                           index=["biweekly", "monthly", "custom_days"].index(d["cadence_mode"]))
        d["cadence_mode"] = cad
        if cad == "custom_days":
            d["cadence_days"] = st.number_input("Days between installments", 1, 120, int(d["cadence_days"]))
        d["apr"] = st.slider("APR (0 = interest-free)", 0.0, 0.60, float(d["apr"]), 0.01)
        d["horizon_months"] = st.slider("Horizon (months)", 6, 60, int(d["horizon_months"]))
        # equal weights for the chosen installment count
        d["installment_weights"] = [1.0 / d["n_installments"]] * d["n_installments"]

    with s.expander("💵 Revenue & fees"):
        d["merchant_fee_pct"] = st.slider("Merchant fee (% of order value)", 0.0, 0.15,
                                          float(d["merchant_fee_pct"]), 0.005)
        d["merchant_fee_recognition"] = st.selectbox(
            "Merchant fee recognition", ["upfront", "deferred_straight_line"],
            index=["upfront", "deferred_straight_line"].index(d["merchant_fee_recognition"]))
        d["consumer_fee_mode"] = st.selectbox("Consumer fee mode", ["none", "fixed", "pct"],
                                              index=["none", "fixed", "pct"].index(d["consumer_fee_mode"]))
        if d["consumer_fee_mode"] == "fixed":
            d["consumer_fee_fixed"] = st.number_input("Consumer fee ($/loan)", 0.0, 100.0,
                                                      float(d["consumer_fee_fixed"]))
        elif d["consumer_fee_mode"] == "pct":
            d["consumer_fee_pct"] = st.slider("Consumer fee (% of order value)", 0.0, 0.10,
                                              float(d["consumer_fee_pct"]), 0.005)
        d["consumer_fee_recognition"] = st.selectbox(
            "Consumer fee recognition", ["deferred_straight_line", "upfront"],
            index=["deferred_straight_line", "upfront"].index(d["consumer_fee_recognition"]))
        d["late_fee_amount"] = st.number_input("Late fee ($/missed installment)", 0.0, 100.0,
                                               float(d["late_fee_amount"]))
        d["late_fee_collection_rate"] = st.slider("Late fee collection rate", 0.0, 1.0,
                                                  float(d["late_fee_collection_rate"]), 0.05)

    with s.expander("📈 Origination & growth"):
        d["loans_origination_t0"] = st.number_input("Loans originated, month 0", 0, 1_000_000,
                                                    int(d["loans_origination_t0"]), step=100)
        d["origination_growth_mode"] = st.selectbox("Growth mode", ["compound", "linear"],
                                                    index=["compound", "linear"].index(
                                                        d["origination_growth_mode"]))
        d["origination_growth_mom"] = st.slider("MoM origination growth", -0.10, 0.30,
                                                float(d["origination_growth_mom"]), 0.005)
        d["avg_order_value"] = st.number_input("Average order value ($)", 10.0, 5000.0,
                                               float(d["avg_order_value"]))
        d["aov_growth_mom"] = st.slider("MoM AOV drift", -0.05, 0.05, float(d["aov_growth_mom"]), 0.005)

    with s.expander("⚠️ Credit, roll-rates & recovery"):
        d["first_payment_default_rate"] = st.slider("First-payment default rate", 0.0, 0.40,
                                                    float(d["first_payment_default_rate"]), 0.005)
        d["base_miss_rate"] = st.slider("Base installment miss rate", 0.0, 0.30,
                                        float(d["base_miss_rate"]), 0.005)
        d["charge_off_dpd"] = st.selectbox("Charge-off DPD", [90, 120, 180, 365],
                                           index=[90, 120, 180, 365].index(d["charge_off_dpd"])
                                           if d["charge_off_dpd"] in [90, 120, 180, 365] else 1)
        d["recovery_rate"] = st.slider("Recovery rate (of charged-off)", 0.0, 1.0,
                                       float(d["recovery_rate"]), 0.05)
        d["recovery_lag_months"] = st.slider("Recovery lag (months)", 0, 18,
                                             int(d["recovery_lag_months"]))
        d["recovery_accounting"] = st.selectbox("Recovery accounting",
                                                ["pnl_other_income", "reverse_allowance"],
                                                index=["pnl_other_income", "reverse_allowance"].index(
                                                    d["recovery_accounting"]))
        d["prepayment_rate"] = st.slider("Monthly prepayment rate", 0.0, 0.20,
                                         float(d["prepayment_rate"]), 0.005)

    with s.expander("🏛️ IFRS 9 — staging & ECL"):
        d["staging_basis"] = st.selectbox("Staging basis", ["tight_bnpl", "standard"],
                                          index=["tight_bnpl", "standard"].index(d["staging_basis"]),
                                          help="tight_bnpl: 1+ DPD = Stage 2 (SICR). standard: 30+ DPD = Stage 2.")
        c1, c2 = st.columns(2)
        d["pd_12m_stage1"] = c1.slider("PD Stage 1 (12m)", 0.0, 0.30, float(d["pd_12m_stage1"]), 0.005)
        d["lgd_stage1"] = c2.slider("LGD Stage 1", 0.0, 1.0, float(d["lgd_stage1"]), 0.05)
        d["lgd_stage2"] = c1.slider("LGD Stage 2", 0.0, 1.0, float(d["lgd_stage2"]), 0.05)
        d["lgd_s3_substandard"] = c2.slider("LGD S3 Substandard", 0.0, 1.0,
                                            float(d["lgd_s3_substandard"]), 0.05)
        d["lgd_s3_doubtful"] = c1.slider("LGD S3 Doubtful", 0.0, 1.0, float(d["lgd_s3_doubtful"]), 0.05)
        d["lgd_s3_loss"] = c2.slider("LGD S3 Loss", 0.0, 1.0, float(d["lgd_s3_loss"]), 0.05)
        d["management_overlay_pct"] = st.slider("Management overlay (% on ECL)", -0.50, 1.0,
                                                float(d["management_overlay_pct"]), 0.05)
        st.caption("Non-DPD overrides — fraction of the performing book flagged regardless of DPD:")
        oc1, oc2 = st.columns(2)
        d["override_forborne_fraction"] = oc1.slider("Forborne → Stage 2", 0.0, 0.50,
                                                     float(d["override_forborne_fraction"]), 0.01)
        d["override_score_deterioration_fraction"] = oc2.slider("Score drop → Stage 2", 0.0, 0.50,
                                                               float(d["override_score_deterioration_fraction"]), 0.01)
        d["override_fraud_fraction"] = oc1.slider("Fraud → Stage 3", 0.0, 0.20,
                                                  float(d["override_fraud_fraction"]), 0.005)
        d["override_bankruptcy_fraction"] = oc2.slider("Bankruptcy → Stage 3", 0.0, 0.20,
                                                       float(d["override_bankruptcy_fraction"]), 0.005)

    with s.expander("🌐 Macro overlay (probability-weighted)"):
        c1, c2, c3 = st.columns(3)
        d["scenario_prob_base"] = c1.slider("P(base)", 0.0, 1.0, float(d["scenario_prob_base"]), 0.05)
        d["scenario_prob_upside"] = c2.slider("P(upside)", 0.0, 1.0, float(d["scenario_prob_upside"]), 0.05)
        d["scenario_prob_downside"] = c3.slider("P(downside)", 0.0, 1.0,
                                                float(d["scenario_prob_downside"]), 0.05)
        d["pd_multiplier_downside"] = st.slider("Downside PD multiplier", 1.0, 3.0,
                                                float(d["pd_multiplier_downside"]), 0.05)
        # normalise scenario probs so they sum to 1
        tot = d["scenario_prob_base"] + d["scenario_prob_upside"] + d["scenario_prob_downside"]
        if tot <= 0:
            d["scenario_prob_base"], d["scenario_prob_upside"], d["scenario_prob_downside"] = 0.5, 0.2, 0.3
        else:
            d["scenario_prob_base"] /= tot
            d["scenario_prob_upside"] /= tot
            d["scenario_prob_downside"] /= tot

    with s.expander("🏦 Funding, opex & tax"):
        d["funding_advance_rate"] = st.slider("Funding advance rate (cap vs receivable)", 0.0, 1.0,
                                              float(d["funding_advance_rate"]), 0.05)
        d["funding_rate_apr"] = st.slider("Cost of funds (APR)", 0.0, 0.30,
                                          float(d["funding_rate_apr"]), 0.005)
        d["min_cash_buffer"] = st.number_input("Minimum cash buffer ($)", 0, 50_000_000,
                                               int(d["min_cash_buffer"]), step=50_000)
        d["initial_equity"] = st.number_input("Initial equity ($)", 0, 500_000_000,
                                              int(d["initial_equity"]), step=500_000)
        d["initial_cash"] = float(d["initial_equity"])
        d["opex_fixed_monthly"] = st.number_input("Fixed opex / month ($)", 0, 50_000_000,
                                                  int(d["opex_fixed_monthly"]), step=5_000)
        d["opex_per_loan_origination"] = st.number_input("Variable opex / originated loan ($)", 0.0, 100.0,
                                                         float(d["opex_per_loan_origination"]))
        d["tax_rate"] = st.slider("Tax rate", 0.0, 0.50, float(d["tax_rate"]), 0.01)
        d["dividend_policy"] = st.selectbox("Dividend policy", ["none", "payout_pct"],
                                            index=["none", "payout_pct"].index(d["dividend_policy"]))
        if d["dividend_policy"] == "payout_pct":
            d["dividend_payout_pct"] = st.slider("Dividend payout (% of NI)", 0.0, 1.0,
                                                 float(d["dividend_payout_pct"]), 0.05)
    return EngineConfig(**d)


# ====================================================================== charts
def line(df, cols, names=None, title="", pct=False, colors=None):
    fig = go.Figure()
    names = names or cols
    for i, c in enumerate(cols):
        col = colors[i] if colors and i < len(colors) else None
        fig.add_trace(go.Scatter(x=df.index, y=df[c], name=names[i], mode="lines",
                                 line=dict(width=2.5, color=col)))
    fig.update_layout(title=title, height=340, margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=-0.2), hovermode="x unified")
    if pct:
        fig.update_yaxes(tickformat=".1%")
    return fig


def stacked_area(df, cols, names, title="", pct=False, colors=None):
    fig = go.Figure()
    for i, c in enumerate(cols):
        col = colors[i] if colors and i < len(colors) else None
        fig.add_trace(go.Scatter(x=df.index, y=df[c], name=names[i], mode="lines",
                                 stackgroup="one", line=dict(width=0.5, color=col)))
    fig.update_layout(title=title, height=340, margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=-0.2), hovermode="x unified")
    if pct:
        fig.update_yaxes(tickformat=".0%")
    return fig


def bars(x, y, title="", color=ACCENT, pct=False):
    fig = go.Figure(go.Bar(x=x, y=y, marker_color=color))
    fig.update_layout(title=title, height=340, margin=dict(l=10, r=10, t=40, b=10))
    if pct:
        fig.update_yaxes(tickformat=".1%")
    return fig


# ======================================================================== main
def main():
    cfg = build_config()
    res = run_cached(json.dumps(cfg.to_dict(), sort_keys=True))
    isf, cf, bs, pf, met = (res.income_statement, res.cash_flow, res.balance_sheet,
                            res.portfolio, res.metrics)

    st.title("💳 BNPL Financial Model")
    st.caption("Cohort-based three-statement model with IFRS 9 ECL provisioning wired in dynamically. "
               "Source of truth for staging & ECL: the `ifrs9-npl` skill.")

    # ---- KPI strip ----
    cum_ni = isf["net_income"].sum()
    ni = isf["net_income"]
    breakeven = next((m for m in ni.index if ni.loc[m] > 0), None)
    rep = res.invariant_report
    artic = rep[rep["category"] == "articulation"]
    feas = rep[rep["category"] == "feasibility"]
    inv_ok = bool(artic["passed"].all())
    feas_fail = feas[~feas["passed"]]
    k = st.columns(6)
    k[0].metric("Cumulative net income", money(cum_ni))
    k[1].metric("Final monthly NI", money(ni.iloc[-1]))
    k[2].metric("Breakeven month", f"m{breakeven}" if breakeven is not None else "—")
    k[3].metric("Final NPL ratio", f"{pf['npl_ratio'].iloc[-1]:.2%}")
    k[4].metric("Final ECL coverage", f"{pf['ecl_coverage'].iloc[-1]:.2%}")
    k[5].metric("Loans / mo (final)", money(pf["loans_originated"].iloc[-1], dollars=False))

    if not inv_ok:
        st.error("⚠️ One or more articulation invariants failed — see the Invariants tab.")
    else:
        st.success("✓ All articulation invariants pass (balance sheet balances, cash ties, "
                   "ECL allowance ties to the statements).", icon="✅")
    if len(feas_fail):
        ids = ", ".join(feas_fail["id"])
        st.warning(f"Feasibility flag ({ids}): this scenario is internally consistent but not "
                   f"self-funding — e.g. cash goes negative. Add equity, raise the funding line, "
                   f"or slow growth.", icon="⚠️")

    tabs = st.tabs(["📊 Overview", "🧾 Income Statement", "💧 Cash Flow", "⚖️ Balance Sheet",
                    "🏛️ Portfolio & IFRS 9", "📐 Metrics", "🔬 Scenario Compare",
                    "✅ Invariants", "⬇️ Export"])

    # ---------------------------------------------------------------- Overview
    with tabs[0]:
        c1, c2 = st.columns(2)
        c1.plotly_chart(line(pf, ["loans_originated"], ["Loans originated"],
                             "Loan originations / month", colors=[ACCENT]), width='stretch')
        c2.plotly_chart(line(pf, ["gross_receivable", "net_receivable"],
                             ["Gross receivable", "Net receivable"], "Receivables",
                             colors=[ACCENT, GREEN]), width='stretch')
        c3, c4 = st.columns(2)
        c3.plotly_chart(line(isf, ["total_revenue", "net_income"], ["Total revenue", "Net income"],
                             "Revenue & net income", colors=[ACCENT, GREEN]), width='stretch')
        c4.plotly_chart(line(pf, ["npl_ratio", "ecl_coverage"], ["NPL ratio", "ECL coverage"],
                             "Credit quality", pct=True, colors=[RED, AMBER]), width='stretch')
        st.plotly_chart(stacked_area(pf, ["stage1_balance", "stage2_balance", "stage3_balance"],
                                     ["Stage 1", "Stage 2", "Stage 3"], "Portfolio by IFRS 9 stage ($)",
                                     colors=[GREEN, AMBER, RED]), width='stretch')

    # -------------------------------------------------------- Income Statement
    with tabs[1]:
        st.plotly_chart(line(isf, ["total_revenue", "provision_expense", "operating_expense",
                                   "net_income"],
                             ["Revenue", "Provision (ECL)", "Opex", "Net income"],
                             "Income statement drivers",
                             colors=[ACCENT, RED, AMBER, GREEN]), width='stretch')
        show = isf.copy()
        st.dataframe(show.style.format("{:,.0f}"), width='stretch', height=420)

    # -------------------------------------------------------------- Cash Flow
    with tabs[2]:
        st.plotly_chart(line(cf, ["cfo", "cff", "ending_cash"],
                             ["Operating CF", "Financing CF", "Ending cash"], "Cash flow",
                             colors=[ACCENT, AMBER, GREEN]), width='stretch')
        st.caption("Operating cash is computed **directly** (authoritative); the indirect column is "
                   "asserted equal to it (invariant H-CFO).")
        cols = ["net_income", "provision_addback", "charge_offs_to_allowance", "d_gross_receivable",
                "d_deferred_fee", "d_opex_payable", "cfo", "cfo_indirect", "debt_draw_repay",
                "equity_issuance", "dividends", "cff", "net_change_cash", "ending_cash"]
        st.dataframe(cf[cols].style.format("{:,.0f}"), width='stretch', height=420)

    # ------------------------------------------------------------ Balance Sheet
    with tabs[3]:
        st.plotly_chart(stacked_area(bs, ["cash", "net_receivables"], ["Cash", "Net receivables"],
                                     "Asset composition ($)", colors=[GREEN, ACCENT]),
                        width='stretch')
        maxchk = bs["balance_check"].abs().max()
        st.caption(f"Balance check (Assets − Liabilities − Equity), max |residual| = {maxchk:.2e}")
        cols = ["cash", "gross_receivables", "ecl_allowance", "net_receivables", "total_assets",
                "funding_debt", "deferred_fee_income", "opex_payable", "total_liabilities",
                "paid_in_capital", "retained_earnings", "total_equity", "balance_check"]
        st.dataframe(bs[cols].style.format("{:,.0f}"), width='stretch', height=420)

    # ---------------------------------------------------- Portfolio & IFRS 9
    with tabs[4]:
        last = int(pf.index[-1])
        sd = res.stage_detail
        sdl = sd[sd["month"] == last]
        st.subheader(f"IFRS 9 stage summary — month {last}")
        stage_tbl = (sdl.groupby("stage")
                     .agg(balance=("balance", "sum"), ecl=("ecl", "sum"),
                          est_loans=("est_loans", "sum")).reset_index())
        tot_bal = stage_tbl["balance"].sum()
        stage_tbl["% balance"] = stage_tbl["balance"] / tot_bal if tot_bal else 0
        stage_tbl["ECL rate"] = np.where(stage_tbl["balance"] > 0,
                                         stage_tbl["ecl"] / stage_tbl["balance"], 0)
        stage_tbl["Stage"] = stage_tbl["stage"].map({1: "Stage 1 (Performing)",
                                                     2: "Stage 2 (SICR)", 3: "Stage 3 (NPL)"})
        c1, c2 = st.columns([3, 2])
        c1.dataframe(stage_tbl[["Stage", "balance", "% balance", "ecl", "ECL rate", "est_loans"]]
                     .style.format({"balance": "{:,.0f}", "ecl": "{:,.0f}", "est_loans": "{:,.0f}",
                                    "% balance": "{:.1%}", "ECL rate": "{:.2%}"}),
                     width='stretch')
        c2.metric("NPL ratio", f"{pf['npl_ratio'].iloc[-1]:.2%}")
        c2.metric("ECL coverage", f"{pf['ecl_coverage'].iloc[-1]:.2%}")
        c2.metric("Loss allowance", money(pf["allowance"].iloc[-1]))

        c3, c4 = st.columns(2)
        bucket_bal = sdl.set_index("bucket").reindex(LIVE_BUCKETS)["balance"].fillna(0)
        c3.plotly_chart(bars(LIVE_BUCKETS, bucket_bal.values, "Balance by DPD bucket (latest)"),
                        width='stretch')
        c4.plotly_chart(line(pf, ["provision_charge", "write_offs", "recoveries"],
                             ["Provision charge", "Charge-offs", "Recoveries"],
                             "Credit flows", colors=[AMBER, RED, GREEN]), width='stretch')

        if len(res.vintage):
            st.subheader("Vintage — cumulative loss rate by cohort")
            v = res.vintage
            st.plotly_chart(bars(v["cohort"], v["cum_loss_rate"],
                                 "Cumulative charge-off rate by origination cohort",
                                 color=RED, pct=True), width='stretch')

    # --------------------------------------------------------------- Metrics
    with tabs[5]:
        pick = st.multiselect("Metrics to chart", list(met.columns),
                              default=["npl_ratio", "ecl_coverage", "net_margin", "roe_ann"])
        if pick:
            pct_like = all(met[c].abs().max() <= 3 for c in pick)
            st.plotly_chart(line(met, pick, pick, "Selected metrics", pct=pct_like),
                            width='stretch')
        st.dataframe(met.style.format("{:,.4f}"), width='stretch', height=420)

    # ------------------------------------------------------- Scenario Compare
    with tabs[6]:
        st.write("Save the current assumptions as a named scenario, then compare key trajectories.")
        if "scenarios" not in st.session_state:
            st.session_state.scenarios = {}
        c1, c2, c3 = st.columns([2, 1, 1])
        name = c1.text_input("Scenario name", value=f"Scenario {len(st.session_state.scenarios)+1}")
        if c2.button("💾 Save current"):
            st.session_state.scenarios[name] = json.dumps(cfg.to_dict(), sort_keys=True)
            st.success(f"Saved '{name}'.")
        if c3.button("🗑️ Clear all"):
            st.session_state.scenarios = {}

        if st.session_state.scenarios:
            metric_choice = st.selectbox("Compare metric",
                                         ["net_income", "npl_ratio", "ecl_coverage",
                                          "gross_receivable", "loans_originated"])
            fig = go.Figure()
            # include current (live) too
            live_src = {"▶ Current (live)": json.dumps(cfg.to_dict(), sort_keys=True)}
            for label, cj in {**live_src, **st.session_state.scenarios}.items():
                r = run_cached(cj)
                src = r.income_statement if metric_choice == "net_income" else r.portfolio
                fig.add_trace(go.Scatter(x=src.index, y=src[metric_choice], name=label,
                                         mode="lines", line=dict(width=2.5)))
            pct = metric_choice in ("npl_ratio", "ecl_coverage")
            fig.update_layout(title=f"Scenario comparison — {metric_choice}", height=420,
                              legend=dict(orientation="h", y=-0.2), hovermode="x unified")
            if pct:
                fig.update_yaxes(tickformat=".1%")
            st.plotly_chart(fig, width='stretch')

            # summary table of saved scenarios
            rows = []
            for label, cj in st.session_state.scenarios.items():
                r = run_cached(cj)
                rows.append(dict(Scenario=label,
                                 CumNI=r.income_statement["net_income"].sum(),
                                 FinalNPL=r.portfolio["npl_ratio"].iloc[-1],
                                 FinalCoverage=r.portfolio["ecl_coverage"].iloc[-1],
                                 FinalCash=r.balance_sheet["cash"].iloc[-1]))
            st.dataframe(pd.DataFrame(rows).style.format(
                {"CumNI": "{:,.0f}", "FinalNPL": "{:.2%}", "FinalCoverage": "{:.2%}",
                 "FinalCash": "{:,.0f}"}), width='stretch')
        else:
            st.info("No saved scenarios yet.")

    # ------------------------------------------------------------ Invariants
    with tabs[7]:
        st.write("Tie-out checks proving the statements articulate (Section H of the spec).")
        rt = res.invariant_report.copy()
        rt["status"] = np.where(rt["passed"], "✅ pass", "❌ FAIL")
        st.dataframe(rt[["id", "category", "description", "status", "max_residual"]]
                     .style.format({"max_residual": "{:.2e}"}), width='stretch', height=520)

    # ---------------------------------------------------------------- Export
    with tabs[8]:
        st.write("Download the full model as an Excel workbook (a tab per statement/table).")
        xls = to_excel_bytes(res)
        st.download_button("⬇️ Download Excel workbook", data=xls,
                           file_name="bnpl_model.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("⬇️ Download assumptions (JSON)",
                           data=json.dumps(cfg.to_dict(), indent=2, sort_keys=True),
                           file_name="bnpl_assumptions.json", mime="application/json")


if __name__ == "__main__":
    main()
