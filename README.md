# BNPL Financial Model

A financial-modeling application for a **Buy-Now-Pay-Later (BNPL)** lender. It generates and
analyzes the **Income Statement, Cash Flow Statement, and Balance Sheet**, tracks loan
originations and a full catalog of operational/financial metrics, and lets you **simulate
scenarios** by adjusting any lending parameter — number of installments, cadence, fees, APR,
growth, default/roll-rates, recovery, funding, opex, and more.

**IFRS 9 is wired in as a living part of the model.** Loan delinquency (DPD → Stage 1/2/3),
NPL classification, and ECL / provisioning reserves are computed by an engine that faithfully
implements the [`ifrs9-npl` skill methodology](docs/ifrs9_source_of_truth.md), and those
provisions, charge-offs, recoveries, and reserve balances flow **dynamically** into all three
statements every period.

---

## What it does

* **Income Statement (P&L)** — interest & fee income, funding cost, ECL provision expense, opex, tax.
* **Cash Flow Statement** — direct (authoritative) operating cash with an indirect reconciliation.
* **Balance Sheet** — cash, gross/net receivables, ECL allowance (contra-asset), funding debt,
  deferred fee income, equity. Balances to ~1e-9 every period.
* **Originations monitor** — exact loan counts originated, GMV, growth.
* **IFRS 9 portfolio** — stage summary, DPD-bucket distribution, NPL ratio, ECL coverage,
  charge-offs, recoveries, vintage loss curves.
* **Metrics** — take rate, portfolio yield, NIM, cost of risk, charge-off rate, ROE/ROA, leverage,
  funding utilization, unit economics, and more.
* **Scenario simulation** — change any assumption live; save and compare scenarios side by side.
* **Excel export** — one workbook, a tab per statement/table.

---

## Quick start

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# macOS / Linux:         source .venv/bin/activate
python -m pip install -r requirements.txt  

# ---- React app (primary UI) ----
cd web && npm ci && npm run build && cd ..  
python -m uvicorn server.main:app --port 8000
# open http://localhost:8000

# ---- Tests (55-case invariant grid) ----
python -m pytest -q

# ---- Programmatic use ----
python -c "from bnpl_model import run_model, EngineConfig; r = run_model(EngineConfig()); print(r.portfolio.tail())"
```

**Frontend dev mode** (hot reload): run the API on one terminal and Vite on another —
```bash
python -m uvicorn server.main:app --reload --port 8000   # terminal 1 (API)
cd web && npm run dev                                     # terminal 2 (Vite :5173, proxies /api)
```

The **React app** (`web/`) is the UI, served by a thin FastAPI layer (`server/`) that wraps the
Python engine. You can also use the engine programmatically or via the Excel export without any UI.

### Use the engine in code

```python
from bnpl_model import EngineConfig, run_model

cfg = EngineConfig(
    n_installments=4,
    cadence_mode="biweekly",      # or "monthly" / "custom_days"
    apr=0.0,                       # 0 = interest-free pay-in-4
    merchant_fee_pct=0.05,
    loans_origination_t0=2500,
    origination_growth_mom=0.06,
    base_miss_rate=0.025,
    staging_basis="tight_bnpl",    # IFRS 9 staging basis
    charge_off_dpd=120,
)
res = run_model(cfg)

res.income_statement   # P&L by month
res.cash_flow          # Cash flow by month (cfo == cfo_indirect)
res.balance_sheet      # Balance sheet by month (balances to ~0)
res.portfolio          # stages, NPL ratio, ECL coverage, charge-offs, recoveries...
res.stage_detail       # per-month x DPD-bucket: balance, stage, PD, LGD, ECL
res.metrics            # operational + financial metrics
res.invariant_report   # tie-out checks (all pass)
res.to_excel("outputs/model.xlsx")

# scenario variant
downside = run_model(cfg.scenario(base_miss_rate=0.08, origination_growth_mom=0.02))
```

---

## Architecture

```
bnpl_model/            PYTHON ENGINE (the logic — UI-agnostic, unchanged by the UI)
  config.py        EngineConfig — every parameter + defaults + validate() + presets
  ifrs9.py         IFRS 9: staging, sub-buckets, ECL = PD×LGD×EAD, allowance roll-forward
  engine.py        cohort roll-forward + 3-statement assembly + per-period invariant checks
  metrics.py       operational + financial metrics catalog
  invariants.py    tie-out checks (the proof the statements articulate)
  export.py        Excel workbook export
server/
  main.py          thin FastAPI wrapper: /api/schema, /api/run, /api/export (NO logic)
web/                   REACT APP (UI — Vite + TypeScript + Recharts)
  src/App.tsx      orchestrates: schema-driven controls -> live /api/run -> charts/tables
  src/components/  Sidebar, KPI strip, banner, charts, data table, tabs
  src/api.ts       typed API client
docs/
  model_spec.md            canonical engineering spec (the build was designed against this)
  ifrs9_source_of_truth.md the IFRS 9 methodology this model implements
tests/
  test_invariants.py       55-case scenario grid: every articulation invariant must hold
outputs/
  sample_model.xlsx        example export
```

### How the model works

It is a **deterministic, monthly-period, cohort/vintage roll-forward**. Each month it:

1. **Originates** a new cohort. Gross receivable is booked **net of the first installment**
   (collected as certain cash at purchase and excluded from the delinquency machine).
2. **Ages** every live cohort through a DPD state machine: installments come due → are paid or
   missed → missed balances roll / cure / charge off via configurable roll-rates → prepayments and
   recoveries are applied.
3. Computes the **bottom-up required ECL** and rolls the loss allowance via the **provision-as-plug**
   identity, so the allowance always equals the required ECL.
4. Assembles the three statements. **Operating cash is computed directly** and is authoritative;
   the indirect cash flow is asserted equal to it. **Cash is the single balance-sheet plug**; debt
   is a bounded draw/repay rule sized off *opening* balances (no funding circularity).
5. Checks the per-period articulation invariants.

### IFRS 9 integration ([source of truth](docs/ifrs9_source_of_truth.md))

| Stage | Standard DPD | Tighter BNPL DPD (default) | ECL basis |
|------|--------------|----------------------------|-----------|
| 1 — Performing | 0–29 | 0 (1–7 = SICR trigger) | 12-month ECL |
| 2 — SICR | 30–89 | 1–89 | Lifetime ECL |
| 3 — NPL / impaired | 90+ | 90+ | Lifetime ECL (PD = 100%) |

* **ECL = PD × LGD × EAD × DF**, probability-weighted across base/upside/downside macro scenarios
  with an optional management overlay.
* Stage-3 sub-buckets (Substandard / Doubtful / Loss) carry escalating LGD.
* **Allowance roll-forward:** `closing = opening + provision − charge-offs + recoveries`, and the
  provision is solved so the closing allowance equals the bottom-up required ECL.
* Charge-offs reduce gross receivables and the allowance equally; recoveries are cash inflows
  (P&L recovery income by default, or an allowance reversal).
* **Non-DPD overrides** — a configurable fraction of the *performing* book can be flagged
  forborne / score-deteriorated (→ Stage 2) or fraud / bankruptcy (→ Stage 3) regardless of DPD,
  applied worst-of and reflected in the NPL ratio, stage tables, and ECL.
* **NPL ratio = Stage 3 balance ÷ gross receivable.**

---

## Correctness — the invariants

The model's tie-outs are enforced as assertions (spec Section H) and tested across a 50-case
scenario grid. The "big six" articulation checks:

| ID | Check |
|----|-------|
| H-BS | Assets = Liabilities + Equity (balances to ~1e-9) |
| H-CASH | Cash-flow ending cash = balance-sheet cash |
| H-CFO | Indirect operating cash = direct operating cash |
| H-ALW1 | Allowance roll-forward ties (opening + provision − write-offs + recoveries) |
| H-ALW2 | Allowance = bottom-up required ECL |
| H-COH | Stage balances sum to gross receivable |

Plus checks on retained-earnings roll, cash continuity, no live balance past the charge-off edge,
finiteness, deferred-fee non-negativity, and Stage-3 PD = 1.0. Checks are tagged **articulation**
(tie-outs that must always hold) or **feasibility** (business signals — `H-CASHNEG` flags a scenario
whose funding can't keep cash non-negative; `H-NPL` bounds). See the **Invariants** tab in the app or
the `Invariants` sheet in the Excel export.

```python
from bnpl_model.invariants import assert_invariants
assert_invariants(run_model(EngineConfig()))   # raises if any ARTICULATION tie-out fails
# (an undercapitalised-but-correctly-articulated scenario is valid input and does not raise;
#  it surfaces as a feasibility warning instead)
```

---

## Key parameters

All parameters live in `EngineConfig` (see [config.py](bnpl_model/config.py) for the full list with
defaults and units). Highlights, all adjustable in the dashboard:

* **Product:** `n_installments`, `cadence_mode` / `cadence_days`, `apr`, `installment_weights`.
* **Revenue:** `merchant_fee_pct`, `consumer_fee_*`, `late_fee_*`, fee recognition policies.
* **Growth:** `loans_origination_t0`, `origination_growth_mom`, `avg_order_value`, `seasonality`.
* **Credit:** `first_payment_default_rate`, `base_miss_rate`, `roll_matrix`, `charge_off_dpd`,
  `recovery_rate` / `recovery_lag_months`, `prepayment_rate`.
* **IFRS 9:** `staging_basis`, `pd_12m_stage1`, `lgd_*`, macro `scenario_prob_*` / `pd_multiplier_*`,
  `management_overlay_*`.
* **Funding/Opex/Tax:** `funding_advance_rate`, `funding_rate_apr`, `min_cash_buffer`,
  `initial_equity`, `opex_*`, `tax_rate`, `dividend_*`.

### Modeling notes

* Loan **originations** are exact; **active / per-stage loan counts** are balance-weighted estimates
  (the financials are driven by exact principal balances).
* Interest income is recognized = collected in-period (no accrued-interest receivable); irrelevant
  at `apr = 0`. Working capital is modeled via the deferred-fee liability and an opex payable.
* Default PD/LGD values are **illustrative BNPL benchmarks** — replace with your own model outputs.

---

## Design provenance

The engine was designed against a canonical spec ([docs/model_spec.md](docs/model_spec.md)) produced
by a judge-panel process (three independent designs → adversarial review → synthesis) that
specifically resolved the classic modeling traps: indirect-cash-flow double-counting, an
over-determined balance sheet, funding circularity, first-installment receivable treatment, the
charge-off vs Stage-3 sub-bucket contradiction, and PD consistency between the cohort engine and ECL.
