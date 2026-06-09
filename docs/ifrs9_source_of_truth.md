# IFRS 9 Source of Truth

> This document captures the methodology defined by the **`ifrs9-npl`** skill, which is the
> authoritative source for loan staging, NPL classification, and ECL/provisioning logic in this
> application. The modeling engine (`bnpl_model/ifrs9.py`) implements these rules. Every threshold
> below is exposed as a **configurable parameter** so scenarios can flex them, but the defaults and
> structure follow this reference.

## Part 1 — Staging Rules

### DPD-based stage assignment (standard)

| Stage | DPD Range | Classification | ECL Horizon |
|-------|-----------|----------------|-------------|
| **Stage 1** | 0 – 29 DPD | Performing | 12-month ECL |
| **Stage 2** | 30 – 89 DPD | Underperforming / SICR | Lifetime ECL |
| **Stage 3** | 90+ DPD | Non-Performing / Credit-Impaired | Lifetime ECL |

### Tighter thresholds for short-term fintech products (BNPL, micro-loans)

- 1–7 DPD → Stage 2 trigger (SICR)
- 8–89 DPD → Stage 2
- 90+ DPD → Stage 3

> The app exposes a **staging basis** toggle (`standard` vs `tighter_bnpl`). Default for this
> BNPL product = **tighter_bnpl**.

### Stage 3 NPL sub-classification

| DPD | Sub-bucket | Typical LGD assumption |
|-----|------------|------------------------|
| 90–179 | Substandard | 60–80% |
| 180–364 | Doubtful | 80–95% |
| 365+ | Loss | 95–100% |

### Override cases (non-DPD triggers)

Even at 0 DPD, move to Stage 2 or Stage 3 if any apply:
- Borrower bankruptcy / insolvency proceedings
- Fraud confirmed on the account
- Loan restructured or forborne
- Significant credit-score deterioration since origination

## Part 2 — Restructured / Forborne Loans

| Interpretation | Stage after restructuring | Back to Stage 1 |
|----------------|--------------------------|-----------------|
| **Permissive** (IAS 39-aligned) | Stage 2 immediately, even if DPD resets to 0 | After ≥12 months clean payment history |
| **Strict / EBA** (EBA/GL/2017/06) | Remains Stage 3 for full cure period | ≥12 months clean + 2-year performing forborne watch |

**Shared rule:** a DPD reset to 0 does **not** reset the stage. The forbearance flag is maintained
independently of DPD.

## Part 3 — ECL Calculation (PD × LGD × EAD)

```
ECL = PD × LGD × EAD × DF
```
DF = discount factor = 1 / (1 + EIR)^t (often 1.0 for short horizons).

| Component | Definition |
|-----------|------------|
| **PD** | Probability of default within the measurement horizon |
| **LGD** | % of EAD not recovered after default (net of recovery costs) |
| **EAD** | Outstanding balance at default (here: outstanding principal of unpaid installments) |
| **DF** | Present-value adjustment using the original effective interest rate |

### ECL by stage

| Stage | PD horizon | ECL formula |
|-------|------------|-------------|
| Stage 1 | 12-month PD | ECL = PD₁₂ × LGD × EAD |
| Stage 2 | Lifetime PD | ECL = Σ (PDₜ × LGDₜ × EADₜ × DFₜ) over remaining life |
| Stage 3 | PD = 100% | ECL = LGD × EAD |

### Illustrative BNPL benchmark ranges (unsecured personal / BNPL)

| Stage | Illustrative PD | Illustrative LGD | Benchmark ECL Rate |
|-------|----------------|------------------|--------------------|
| Stage 1 | 1–5% | 60–75% | ~0.6–3.75% of EAD |
| Stage 2 | 10–35% | 65–80% | ~6.5–28% of EAD |
| Stage 3 (Substandard) | 100% | 60–80% | 60–80% of EAD |
| Stage 3 (Doubtful) | 100% | 80–95% | 80–95% of EAD |
| Stage 3 (Loss) | 100% | 95–100% | 95–100% of EAD |

> Used as **defaults** in the app and labelled illustrative. If the user supplies actual PD/LGD,
> those override the benchmarks.

### Macroeconomic overlays

IFRS 9 requires forward-looking information:
- Base / upside / downside PD scenarios (probability-weighted)
- Key macro drivers (unemployment, consumer credit index)
- Any management overlay and its basis

## Part 4 — Reporting

- **Stage Summary table** (always): Stage, DPD range, # loans, balance, % balance, ECL basis.
- **NPL ratio** = Stage 3 balance / total balance.
- **ECL Estimate table**: Stage, EAD, PD, LGD, ECL ($), ECL rate.
- **Key risk flags** (loans near stage boundaries, migration risk, forborne tracking, data gaps).
- Lead with tables; state conclusions directly; label illustrative vs model figures.

---

### Application mapping (how this flows into the statements)

| IFRS 9 concept | Income Statement | Balance Sheet | Cash Flow |
|----------------|------------------|---------------|-----------|
| ECL / provision charge | Credit loss (provision) expense | — | Non-cash add-back |
| Loss allowance | — | Contra-asset (reduces gross receivables to net) | Δ allowance |
| Charge-off (write-off) | — (already provisioned) | ↓ gross receivables and ↓ allowance | — (non-cash) |
| Recovery | Other income / allowance reversal | ↑ cash | Cash inflow |
| NPL classification | — | Disclosed in portfolio tables | — |
