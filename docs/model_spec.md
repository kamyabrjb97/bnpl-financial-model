I'll synthesize the canonical spec now. This is a pure specification-writing task — the IFRS 9 source of truth is embedded in the prompt, the three candidate specs are provided in full, and the judge verdict gives me a precise fix-list. I have everything I need to produce the merged deliverable directly, with no tool calls required.

Let me write the canonical, build-ready spec.

---

# BNPL MODELING ENGINE — CANONICAL ENGINEERING SPECIFICATION (v1.0, build-ready)

**Scope.** Deterministic, monthly-period, cohort/vintage-based BNPL financial engine (pandas/numpy) with IFRS 9 ECL provisioning wired dynamically into a fully-articulating three-statement model (P&L, Cash Flow, Balance Sheet), powering a Streamlit dashboard with Excel export. This document is the math/data-structure/invariant spec; it is directly implementable. No code is included.

**Synthesis basis.** Statement-articulation backbone and cash-as-checked-reconciliation from `accounting-first`; IFRS 9 staging/sub-bucket/ECL/overlay engine from `ifrs9-first`; two-layer event→month time model and principal-conservation invariant from `cohort-first`. Every "must fix before build" item from the judge is resolved below and cross-referenced as `[FIX-n]`.

**Notation conventions.**
- `t` = reporting/calendar month index, `t ∈ {0,1,…,T}`. `t=0` = initialization (equity injected, opening cash set; no lending flows unless configured).
- `m` (a.k.a. `v`) = origination cohort/vintage month index; `a = t − m` = cohort age in months.
- `k` = installment index within a loan, `k ∈ {1,…,N}`. `k=1` collected at origination.
- `b` = DPD bucket id; `s ∈ {1,2,3}` = IFRS 9 stage.
- All monetary values reporting-currency float64. Rates are decimals (0.04 = 4%). Rounding only at display/export, never inside the loop.
- Sign convention: in the engine, P&L revenues positive, expenses positive (subtracted in subtotals); displayed as magnitudes. Stocks positive; the ECL allowance is carried as a positive contra-asset and subtracted.
- "Loan" = one customer purchase = one schedule of N installments. "Cohort"/"vintage" = all loans originated in month `m`.
- The engine tracks **expected (fractional) balances** under deterministic roll-rates — not Monte Carlo. This is exact for financial statements and fully vectorizable.

**Average days per month constant:** `DPM = 30.4375`.

---

## SECTION A — PARAMETER LIST

All parameters live in a single frozen `EngineConfig` dataclass with a `.validate()` method (validates the invariants flagged H-VAL in Section H). Units: `ccy` = currency, `frac` = fraction in [0,1], `mo` = months, `d` = days, `#` = count, `rate/yr` = annualized rate.

### A.1 Product / schedule

| Name | Meaning | Default | Units |
|---|---|---|---|
| `n_installments` (N) | Installments per loan | 4 | # |
| `cadence_days` | Days between installments | 14 | d |
| `cadence_mode` | `"biweekly"`/`"monthly"`/`"custom_days"` (sets `cadence_days`) | `"biweekly"` | enum |
| `installment_weights` | Per-installment **principal** split; must sum to 1.0; len = N | `[0.25,0.25,0.25,0.25]` | vector |
| `first_inst_at_origination` | Installment 1 collected at purchase | `True` | bool |
| `apr` | Nominal APR on financed balance (0 = interest-free) | 0.00 | rate/yr |
| `interest_basis` | `"simple"`/`"effective"` accrual on outstanding | `"simple"` | enum |
| `day_count` | Day-count convention | `"ACT/365"` | enum |

### A.2 Revenue / fees

| Name | Meaning | Default | Units |
|---|---|---|---|
| `merchant_fee_pct` | Merchant discount rate, % of order value (GMV) | 0.040 | frac |
| `merchant_fee_recognition` | `"upfront"`/`"deferred_straight_line"`/`"deferred_eir"` **[FIX-FEE]** | `"upfront"` | enum |
| `consumer_fee_mode` | `"fixed"`/`"pct"`/`"none"` | `"none"` | enum |
| `consumer_fee_fixed` | Fixed consumer fee per loan | 0.00 | ccy |
| `consumer_fee_pct` | Consumer fee, % of order value | 0.00 | frac |
| `consumer_fee_recognition` | `"upfront"`/`"deferred_straight_line"`/`"deferred_eir"` **[FIX-FEE]** | `"deferred_straight_line"` | enum |
| `late_fee_amount` | Flat late fee per missed-installment event | 7.00 | ccy |
| `late_fee_mode` | `"per_missed_installment"`/`"per_period_outstanding"`/`"none"` | `"per_missed_installment"` | enum |
| `late_fee_cap_per_loan` | Max cumulative late fees per loan | 28.00 | ccy |
| `late_fee_collection_rate` | Fraction of assessed late fees actually collected (cash) | 0.40 | frac |
| `late_fee_recognition` | `"cash_basis"`/`"accrual"` | `"cash_basis"` | enum |

> **[FIX-FEE] Per-fee-type recognition.** Merchant fee is economically earned at point of sale → defaults to `"upfront"`. Only fees integral to the financing EIR are deferrable. The single global flag from `cohort-first` is **replaced** by per-fee policies. Deferred fees create a deferred-fee liability (Section F) released to revenue over loan life (Section D.1).

### A.3 Origination / growth

| Name | Meaning | Default | Units |
|---|---|---|---|
| `loans_origination_t0` | New loans originated in month 0 | 1000 | # |
| `origination_growth_mode` | `"compound"`/`"linear"`/`"explicit_vector"`/`"s_curve"` | `"compound"` | enum |
| `origination_growth_mom` | Month-over-month growth in new-loan count | 0.05 | frac/mo |
| `origination_vector` | Explicit per-month loan counts (overrides growth) | `None` | vector |
| `seasonality` | 12-vector of monthly multipliers | `[1.0]*12` | frac |
| `avg_order_value` (AOV) | Average GMV per loan | 250.00 | ccy |
| `aov_growth_mom` | MoM drift in AOV | 0.00 | frac/mo |
| `horizon_months` (T) | Modeled months | 36 | # |

### A.4 Credit / roll-rate / recovery

| Name | Meaning | Default | Units |
|---|---|---|---|
| `dpd_bucket_edges` | DPD bucket edges (days) — fixed at staging thresholds **[FIX-BKT]** | `[0,1,8,30,60,90,120,180,365,∞]` | vector |
| `roll_matrix` | Per-bucket monthly transition matrix (B.6) | see B.6 | matrix |
| `first_payment_default_rate` | P(first **scheduled post-origination** installment, k=2, missed) | 0.03 | frac |
| `base_miss_rate` | Baseline P(a scheduled installment k≥2 missed) for current accounts | 0.02 | frac |
| `marginal_default_curve` | Marginal default propensity by months-on-book (drives roll + ECL PD) **[FIX-PD]** | see B.7 | vector by MOB |
| `cure_rate` | Monthly P(delinquent bucket cures toward current), per bucket | see B.6 | vector |
| `charge_off_dpd` | DPD at which an unpaid installment is written off **[FIX-CO]** | 120 | d |
| `acceleration_on_default` | On charge-off, accelerate whole remaining loan principal | `True` | bool |
| `recovery_rate` | Fraction of charged-off principal eventually recovered | 0.15 | frac |
| `recovery_lag_months` | Months from charge-off to recovery cash | 6 | mo |
| `recovery_curve` | Optional multi-period recovery distribution (overrides single lag; sums to 1) | `None` | vector |
| `recovery_accounting` | `"pnl_other_income"`/`"reverse_allowance"` (C.7) | `"pnl_other_income"` | enum |
| `prepayment_rate` | Monthly P(remaining balance prepaid in full) | 0.00 | frac/mo |

> **[FIX-BKT] Bucket edges sit exactly on staging/sub-bucket thresholds** so no bucket ever straddles a stage or sub-bucket boundary: `{0 | 1–7 | 8–29 | 30–59 | 60–89 | 90–119 | 120–179 | 180–364 | 365+}`. See B.4 and C.1.

### A.5 IFRS 9 — staging

| Name | Meaning | Default | Units |
|---|---|---|---|
| `staging_basis` | `"standard"`/`"tight_bnpl"` | `"tight_bnpl"` | enum |
| `dpd_stage1_max_standard` | Upper DPD for Stage 1 (standard) | 29 | d |
| `dpd_stage2_max_standard` | Upper DPD for Stage 2 (standard) | 89 | d |
| `dpd_sicr_trigger_tight` | DPD that triggers SICR → Stage 2 (tight) | 1 | d |
| `dpd_stage2_max_tight` | Upper DPD for Stage 2 (tight) | 89 | d |
| `dpd_stage3_min` | DPD entering Stage 3 (both bases) | 90 | d |
| `s3_substandard_max` | Upper DPD of Substandard sub-bucket | 179 | d |
| `s3_doubtful_max` | Upper DPD of Doubtful sub-bucket | 364 | d |
| `cure_clean_months` | Consecutive clean months required to cure stage | 12 | mo |
| `forbearance_policy` | `"permissive"`/`"strict_eba"` | `"permissive"` | enum |
| `cure_watch_months_strict` | Extra performing-forborne watch (strict only) | 24 | mo |
| `override_bankruptcy_stage` | Stage forced on bankruptcy/insolvency flag | 3 | stage |
| `override_fraud_stage` | Stage forced on confirmed fraud | 3 | stage |
| `override_forborne_stage` | Stage forced on restructured/forborne | 2 | stage |
| `override_score_deterioration_stage` | Stage forced on significant score drop | 2 | stage |

### A.6 IFRS 9 — PD / LGD / EAD / discounting (defaults illustrative — label as such)

| Name | Meaning | Default | Units |
|---|---|---|---|
| `pd_12m_stage1` | 12-month PD, Stage 1 (point input) | 0.03 | frac |
| `lgd_stage1` | LGD, Stage 1 | 0.70 | frac |
| `lgd_stage2` | LGD, Stage 2 | 0.72 | frac |
| `lgd_s3_substandard` | LGD, 90–179 DPD | 0.70 | frac |
| `lgd_s3_doubtful` | LGD, 180–364 DPD | 0.875 | frac |
| `lgd_s3_loss` | LGD, 365+ DPD | 1.00 | frac |
| `eir_annual` | Effective interest rate for ECL discounting (if None, derive from apr+fees) | `None` | rate/yr |
| `ecl_discount_enabled` | Apply DF = 1/(1+EIR)^t (default off for short horizon) | `False` | bool |
| `ead_include_accrued_interest` | Include accrued interest in EAD | `True` | bool |

> **[FIX-PD] Stage-2 lifetime PD is NOT a free point input.** It is derived from the same `marginal_default_curve` used by the cohort roll engine: `pd_lifetime_stage2 = 1 − ∏(1 − marginal_pd_a)` over the loan's remaining installments. Only the Stage-1 12-month PD remains a separate point input. See C.4 and the cross-cutting reconciliation note in B.7.

### A.7 Macro overlays & management overlay

| Name | Meaning | Default | Units |
|---|---|---|---|
| `scenario_probs` | {base, upside, downside} weights (must sum to 1) | {0.50, 0.20, 0.30} | frac |
| `pd_multiplier_base/upside/downside` | PD scaling per scenario | 1.00 / 0.85 / 1.40 | mult |
| `lgd_multiplier_base/upside/downside` | LGD scaling per scenario | 1.00 / 0.95 / 1.15 | mult |
| `management_overlay_pct` | Multiplicative top-up on probability-weighted ECL | 0.00 | frac |
| `management_overlay_abs` | Absolute add-on to ECL allowance | 0.00 | ccy |

### A.8 Funding / capital / opex / tax

| Name | Meaning | Default | Units |
|---|---|---|---|
| `funding_advance_rate` | Cap: max debt as fraction of receivable | 0.85 | frac |
| `funding_base` | `"gross_receivable"`/`"net_receivable_opening"` — sized off **opening (t−1)** balances **[FIX-CIRC]** | `"net_receivable_opening"` | enum |
| `funding_rate_apr` | Cost of funds on drawn debt | 0.09 | rate/yr |
| `funding_commitment_fee` | Fee on undrawn line | 0.005 | rate/yr |
| `min_cash_buffer` | Target minimum cash (triggers debt draw) | 250_000 | ccy |
| `initial_equity` | Paid-in capital at t=0 | 5_000_000 | ccy |
| `initial_cash` | Opening cash at t=0 (≤ initial_equity) | 5_000_000 | ccy |
| `dividend_policy` | `"none"`/`"payout_pct"` | `"none"` | enum |
| `dividend_payout_pct` | Dividend as % of positive net income | 0.00 | frac |
| `opex_fixed_monthly` | Fixed operating cost / month | 150_000 | ccy |
| `opex_fixed_growth_mom` | MoM growth in fixed opex | 0.00 | frac/mo |
| `opex_per_loan_origination` | Variable cost per originated loan | 3.50 | ccy |
| `opex_per_active_loan` | Servicing cost per active loan / month | 0.25 | ccy |
| `opex_pct_of_gmv` | Variable opex as % of GMV originated | 0.00 | frac |
| `payables_days` | DPO for accrued opex payable | 30 | d |
| `merchant_remit_lag_days` | Days to remit GMV (net of fee) to merchant | 2 | d |
| `tax_rate` | Income tax rate | 0.25 | frac |
| `tax_loss_carryforward` | Enable NOL carryforward | `True` | bool |
| `tax_pay_lag_months` | Lag from tax accrual to cash payment | 0 | mo |

---

## SECTION B — PORTFOLIO / COHORT ENGINE MECHANICS

### B.1 Per-loan economics at origination (cohort `m`)

For order value `OV_m = avg_order_value · (1+aov_growth_mom)^m`:

```
GMV_m                      = O_m · OV_m                              # O_m = loans originated in m
total_loan_amount (per ln) = OV_m                                   # principal financed = order value (apr=0)
inst_principal_k           = installment_weights[k] · OV_m          # per loan, per installment
inst_interest_k            = interest accrued on outstanding before k (0 if apr=0)
inst_amount_k              = inst_principal_k + inst_interest_k
First_Inst                 = inst_amount_1                          # collected at origination (certain cash)
Outstanding_after_orig     = total_loan_amount − inst_principal_1   # = principal of installments 2..N  [FIX-ORIG]
merchant_fee (per loan)    = merchant_fee_pct · OV_m
consumer_fee (per loan)    = consumer_fee_fixed | consumer_fee_pct·OV_m | 0
```

> **[FIX-ORIG] First-installment treatment (context-mandated).** Gross receivable booked at origination = `O_m · (OV_m − inst_principal_1)`. The first installment is **certain cash at t0** and is **excluded from the delinquency/roll state machine entirely**. Only installments `k = 2..N` are subject to miss/roll/cure/charge-off. Invariant H-ORIG asserts `Outstanding_after_orig == OV − inst_principal_1`.

For `apr>0`: level-payment annuity over N installments at per-installment rate `i = apr·cadence_days/365`; `A = OV·i/(1−(1+i)^(−N))`; `inst_principal_k = A − inst_interest_k`, `inst_interest_k = i·balance_{k−1}`. With `apr=0`, `inst_principal_k = installment_weights[k]·OV`.

### B.2 Origination driver

```
compound: O_m = round(loans_origination_t0 · (1+g)^m · seasonality[m mod 12])
linear:   O_m = round(loans_origination_t0 · (1+g·m) · seasonality[m mod 12])
vector:   O_m = origination_vector[m]
```

### B.3 Two-layer time model (event layer → month layer) — from `cohort-first`

**Event layer (sub-monthly).** For each cohort, enumerate installment due-events on the true calendar:
`due_date(m,k) = origination_date(m) + (k−1)·cadence_days`, each carrying scheduled principal `O_m·inst_principal_k` on **live** loans. Build an immutable **event table** once (vectorized).

**Month layer.** Reporting month `t` spans `[t·DPM, (t+1)·DPM)` days from t0. Each event maps to exactly one month `month(m,k) = floor(days(due_date(m,k))/DPM)`. Multiple events from one cohort can land in one month; the engine **sums** them. The DPD state machine (B.5–B.6) operates on unpaid event balances aged by actual days, but is settled/reported monthly. Implementation: `events.groupby([cohort, month]).sum()` gives `SchedDue[m,t]`.

### B.4 State variables (the canonical state) — buckets from `ifrs9-first`/`cohort-first` with [FIX-BKT] edges

For each cohort `m` and bucket `b`, at end of month `t`, track **principal balance** `Bal[m,b,t]` and **loan count** `Cnt[m,b,t]`. Buckets (edges per A.4):

| Bucket | DPD range | Standard stage | Tight stage | Sub-bucket / note |
|---|---|---|---|---|
| `CUR` | 0 (current/not-yet-due) | 1 | 1 | performing |
| `D1` | 1–7 | 1 | **2** | tight SICR trigger |
| `D8` | 8–29 | 1 | 2 | |
| `D30` | 30–59 | 2 | 2 | |
| `D60` | 60–89 | 2 | 2 | |
| `D90` | 90–119 | 3 | 3 | Substandard |
| `D120` | 120–179 | 3 | 3 | Substandard (charge-off zone if `charge_off_dpd=120`) |
| `D180` | 180–364 | 3 | 3 | Doubtful |
| `D365` | 365+ | 3 | 3 | Loss |
| `PAID` | absorbing | — | — | repaid/cured-to-paid |
| `PREPAID` | absorbing | — | — | prepaid in full |
| `CHARGED_OFF` | absorbing | — | — | written off (gross + allowance) |

Per-cohort scalars: `forborne_flag[m]`, `clean_months[m]` (cure counter), `cum_late_fee[m]`, override flags.

**Portfolio gross receivable:** `GrossReceivable[t] = Σ_m Σ_{b∈LIVE} Bal[m,b,t]`, where `LIVE = {CUR,D1,D8,D30,D60,D90,D120,D180,D365}` (excludes PAID/PREPAID/CHARGED_OFF).

> **[FIX-CO] Charge-off vs Stage-3 sub-bucket reconciliation.** `charge_off_dpd` defines the **single** charge-off boundary. ECL is computed **only on live (non-charged-off) buckets**. Two coherent regimes, selectable via `charge_off_dpd`:
> - **Default `charge_off_dpd=120`:** balances entering `D120` (120–179) charge off at month-end. `D180`/`D365` then **cannot hold live balances** and their Doubtful/Loss LGDs are unused (vestigial). Live Stage-3 ECL applies only to `D90` (and the portion of `D120` still being processed in-month). The roll matrix (B.6) routes `D120 → CHARGED_OFF`, never to `D180/D365`.
> - **Late charge-off `charge_off_dpd=365`:** set the matrix so `D90→D120→D180→D365` carry live balances with escalating LGD, and only `D365` charges off. Doubtful/Loss LGDs become active.
>
> The engine validates that `roll_matrix`, bucket labels, and `charge_off_dpd` are mutually consistent (Invariant H-CO): no live balance may exist in any bucket whose lower DPD edge `≥ charge_off_dpd`.

### B.5 Monthly update sequence (deterministic, ordered)

For each `t`, execute in this exact order (ordering is load-bearing for tie-out). Let opening = prior close: `Bal[m,b,t]⁻ = Bal[m,b,t−1]`.

0. **Originate cohort `m=t`.** `Cnt[t,CUR,t] += O_t`; `Bal[t,CUR,t] += O_t·(OV_t − inst_principal_1)` **[FIX-ORIG]**. Record certain origination cash: collect `O_t·First_Inst + O_t·consumer_fee`; book merchant fee (deducted from merchant remittance, lagged `merchant_remit_lag_days`). The first installment never enters any LIVE delinquency bucket.

1. **Scheduled amounts come due.** From the event table, `SchedDue[m,t]` (installments k≥2) becomes "due-now" within `CUR`.

2. **Pay vs miss on due amounts.** Miss probability = `first_payment_default_rate` for the first post-origination installment (k=2), else `base_miss_rate`, modulated by `marginal_default_curve[a]` **[FIX-PD]**. Paid fraction → `PAID` (cash inflow). Missed fraction → enters `D1`.

3. **Age delinquency via roll matrix (B.6).** Each delinquent bucket splits into cure / stay / roll-forward / charge-off per `roll_matrix`. Rows sum to 1.

4. **Prepayment.** Apply `prepayment_rate` to remaining `CUR` future principal → `PREPAID` (cash inflow of full remaining balance).

5. **Charge-off.** Balances routed to `CHARGED_OFF` recorded as `WriteOffs[t]` (reduces gross + allowance, C.6). If `acceleration_on_default`, the loan's whole remaining principal charges off together.

6. **Recovery scheduling.** Each `WriteOffs` slice schedules recovery cash `recovery_rate·WriteOffs` at `t + recovery_lag_months` (or spread via `recovery_curve`).

7. **Late fees.** Assess `late_fee_amount` per delinquency event (capped by `late_fee_cap_per_loan`); cash = `late_fee_collection_rate · assessed`; revenue per `late_fee_recognition`.

8. **Override / forbearance flags & cure clock** updated (C.2). DPD reset on restructure does NOT reset stage.

9. **Close.** Write `Bal[m,b,t]`, `Cnt[m,b,t]`; update `clean_months[m]`.

### B.6 Roll matrix (default, monthly transitions) — [FIX-ROLL] with self/stay

Rows = opening bucket, columns = destination; each row sums to 1.0. Defaults illustrative, fully configurable. **Self/stay probabilities are nonzero for delinquent buckets** (judge minor fix on `accounting-first`): an account can remain in a band across a month-end without curing or rolling a full bucket. Calibrate dwell to cadence.

| From \ To | cure (→CUR/PAID) | stay (self) | next worse | charge-off |
|---|---|---|---|---|
| `CUR` (on due) | 0.94 (paid) | — | 0.06 → D1 | — |
| `D1` (1–7) | 0.40 | 0.15 | 0.45 → D8 | — |
| `D8` (8–29) | 0.28 | 0.22 | 0.50 → D30 | — |
| `D30` (30–59) | 0.16 | 0.24 | 0.60 → D60 | — |
| `D60` (60–89) | 0.10 | 0.20 | 0.70 → D90 | — |
| `D90` (90–119) | 0.05 | 0.25 | 0.70 → D120 | — |
| `D120` (120–179) | 0.02 | 0.18 | — | 0.80 (→CHARGED_OFF if `charge_off_dpd≤120`) |

For `charge_off_dpd=365`, extend rows `D120→D180→D365` with stay/forward/cure and charge-off only from `D365`. The matrix is stored as `roll_matrix[from] -> {dest: prob}`; engine validates each row sums to 1.0 (H-ROLL) and obeys the charge-off-edge constraint (H-CO).

### B.7 Default / vintage curve and PD reconciliation — [FIX-PD]

`marginal_default_curve` gives marginal default propensity by months-on-book (illustrative BNPL shape, peaks early):

| MOB | 1 | 2 | 3 | 4 | 5 | 6+ |
|---|---|---|---|---|---|---|
| marginal default rate | 0.015 | 0.020 | 0.018 | 0.010 | 0.005 | 0.002 |

This curve is the **single source of expected default**. It (a) modulates the period miss/roll rates in B.5 step 2, and (b) defines Stage-2 lifetime PD for ECL: `pd_lifetime_stage2(loan) = 1 − ∏_{a∈remaining}(1 − marginal_default_curve[a])`. Thus realized charge-offs (cohort engine) and ECL allowance (C) imply one consistent loss expectation; coverage ratios are internally coherent. Stage-1 12-month PD remains a separate point input `pd_12m_stage1`.

### B.8 Charge-off & recovery timing — exact equations

```
WriteOffs[t]    = Σ_m (principal routed into CHARGED_OFF during t)
RecoveryCash[t] = Σ_{j≤t} WriteOffs_at[j] · recovery_rate · w(t−j)
                  where w(Δ)=1 if Δ=recovery_lag_months & no curve, else recovery_curve[Δ], else 0
```

### B.9 Cohort principal-conservation identity — from `cohort-first` (Invariant H-CONS)

Per cohort, every month, principal only **migrates** between buckets (plus origination inflow); nothing is created/destroyed:
```
Σ_b Bal[m,b,t] (ALL buckets incl. absorbing) = Σ_b Bal[m,b,t−1] + Origination_into_m(t)
```
Equivalently, per cohort over all time: `Originated_m = Collected_m + Prepaid_m + ChargedOff_m + Outstanding_m`. PAID/PREPAID/CHARGED_OFF are absorbing sinks.

---

## SECTION C — IFRS 9 ECL ENGINE

### C.1 Staging logic (tight-BNPL toggle + worst-of overrides) — from `ifrs9-first`

A loan's DPD = max DPD across its unpaid installments; the bucket `b` already encodes this. Base stage by bucket and basis (note **[FIX-BKT]** ensures no bucket straddles a boundary):

```
TIGHT:    dpd≥90 → 3 ; dpd≥1 → 2 ; dpd=0 → 1
STANDARD: dpd≥90 → 3 ; dpd≥30 → 2 ; dpd 0–29 → 1
```

| Bucket | DPD | Standard | Tight |
|---|---|---|---|
| CUR | 0 | 1 | 1 |
| D1 | 1–7 | 1 | 2 |
| D8 | 8–29 | 1 | 2 |
| D30 | 30–59 | 2 | 2 |
| D60 | 60–89 | 2 | 2 |
| D90 | 90–119 | 3 | 3 |
| D120 | 120–179 | 3 | 3 |
| D180 | 180–364 | 3 | 3 |
| D365 | 365+ | 3 | 3 |

**Non-DPD overrides — take the WORST stage** (judge "must fix"):
```
stage = max(
  base_stage,
  override_bankruptcy_stage          if bankruptcy  else 0,
  override_fraud_stage               if fraud       else 0,
  override_forborne_stage            if forborne    else 0,
  override_score_deterioration_stage if score_drop  else 0,
)
```
Stage-3 sub-bucket → LGD: `D90/D120 → Substandard (lgd_s3_substandard)`; `D180 → Doubtful (lgd_s3_doubtful)`; `D365 → Loss (lgd_s3_loss)`.

### C.2 Cure / forbearance

- DPD reset from restructuring does **NOT** reset stage; `forborne_flag` tracked independently of DPD.
- Stage may improve only after `clean_months[m] ≥ cure_clean_months` consecutive clean months **and** no active override. Strict/EBA: additionally requires `cure_watch_months_strict` performing-forborne watch.
- `clean_months` resets to 0 on any miss. A forborne loan cannot drop below `override_forborne_stage` until cured.

### C.3 EAD

```
EAD[m,b,t] = Bal[m,b,t] + (accrued_interest[m,b,t] if ead_include_accrued_interest else 0)
```
Outstanding principal of unpaid installments; non-revolving → no undrawn component. Computed only on LIVE buckets.

### C.4 ECL per stage (PD × LGD × EAD × DF) — [FIX-PD], Stage-3 PD=1.0

```
DF = 1/(1+EIR)^t  if ecl_discount_enabled  else 1.0       # short horizon → default 1.0
Stage 1: ECL = pd_12m_stage1     · LGD_stage1 · EAD · DF
Stage 2: ECL = pd_lifetime_stage2 · LGD_stage2 · EAD · DF   # pd_lifetime from B.7 curve
Stage 3: ECL = 1.0 · LGD_subbucket · EAD · DF              # PD forced to 100%
```

### C.5 Macro overlays (scenario-level probability weighting) — from `ifrs9-first`

```
for sc in {base, upside, downside}:
    PD_sc  = PD_base(stage)  · pd_multiplier[sc]
    LGD_sc = LGD_base(bucket)· lgd_multiplier[sc]
    ECL_sc = PD_sc · LGD_sc · EAD · DF
ECL_weighted = Σ_sc scenario_probs[sc] · ECL_sc
ECL_final    = ECL_weighted · (1 + management_overlay_pct) + management_overlay_abs   # per slice; abs allocated pro-rata at portfolio level
```
ECL is computed **per scenario then weighted** (not a scaled point PD). `Σ scenario_probs = 1` (H-VAL).

**Required closing allowance:** `Required_ECL[t] = Σ_m Σ_{b∈LIVE} ECL_final[m,b,t]`.

### C.6 Allowance roll-forward IDENTITY (provision-as-plug) — from `accounting-first`/`ifrs9-first`

Let `A[t]` = ECL allowance (positive contra-asset). Compute `Required_ECL[t]` bottom-up (C.5), then **solve the P&L plug**:
```
WriteOffs[t]              = Σ_m principal charged off in t                       (B.8)
Rec_alw[t]               = RecoveryCash[t]  if recovery_accounting=="reverse_allowance" else 0
ProvisionCharge[t]       = Required_ECL[t] − A[t−1] + WriteOffs[t] − Rec_alw[t]   # the P&L expense (plug)
A[t]                     = A[t−1] + ProvisionCharge[t] − WriteOffs[t] + Rec_alw[t]
                         = Required_ECL[t]    ✓  (ties by construction → H-ALW2, H-ALW1)
```
- `ProvisionCharge` may be negative (release).
- **Charge-off mechanics:** reduce **gross receivable AND allowance** by `WriteOffs[t]` equally; zero incremental P&L beyond prior provisioning. Under-provisioning residual flows automatically through the plug.
- Never accumulate provision independently — always solve as the plug (guarantees H-ALW1/H-ALW2).

### C.7 Recovery accounting policy (configurable)

- **Default `"pnl_other_income"`:** recoveries are cash inflows recognized as a separate P&L "Recovery income" line; `Rec_alw=0`. Allowance untouched.
- **Alt `"reverse_allowance"`:** recovery cash credits the allowance roll-forward (lowers provision). Cash is identical in both; only P&L geography differs. **Recoveries never hit both** P&L and allowance.

### C.8 NPL & coverage outputs

```
Stage3_Balance[t] = Σ_m Σ_{b: stage=3} Bal[m,b,t]
NPL_Ratio[t]      = Stage3_Balance[t] / GrossReceivable[t]
ECL_Coverage[t]   = A[t] / GrossReceivable[t]
Stage3_Coverage[t]= ECL_on_stage3[t] / Stage3_Balance[t]
```

---

## SECTION D — INCOME STATEMENT (monthly + cumulative)

Period `t`. `Net_Income` flows to retained earnings (F) and is the start of the indirect CF (E).

| # | Line | Formula / source |
|---|---|---|
| 1 | Interest income | `Σ accrued interest recognized in t` (0 if apr=0; per `interest_basis`) |
| 2 | Merchant fee income | recognized per `merchant_fee_recognition` (default upfront: full at origination) **[FIX-FEE]** |
| 3 | Consumer fee income | recognized per `consumer_fee_recognition` (default deferred SL over loan life) |
| 4 | Late fee income | per `late_fee_recognition`; default cash basis = collected portion |
| 5 | Recovery income | `RecoveryCash[t]` if `recovery_accounting="pnl_other_income"`, else 0 (C.7) |
| 6 | **= Total revenue** | Σ 1–5 |
| 7 | Funding cost | `− [Debt[t−1]·funding_rate_apr·d/365 + Undrawn[t−1]·funding_commitment_fee·d/365]` (opening balances **[FIX-CIRC]**) |
| 8 | **= Net interest & fee income** | 6 − 7 |
| 9 | Provision (ECL) expense | `− ProvisionCharge[t]` (C.6; non-cash; may be negative=release) |
| 10 | Operating expense | `− [opex_fixed_monthly·(1+gₒ)^t + opex_per_loan_origination·O_t + opex_per_active_loan·Live[t] + opex_pct_of_gmv·GMV_t]` |
| 11 | **= Pre-tax income (EBT)** | 8 + 9 + 10 |
| 12 | Income tax | `− max(0, EBT − NOL_used)·tax_rate` (D.2) |
| 13 | **= Net income** | 11 + 12 |

**D.1 Deferred fee amortization.** Under `deferred_straight_line`, a fee collected at cohort `m`'s origination is recognized as `fee_total_m / months_to_term` over the loan's life; the unrecognized portion is **Deferred Fee Income (liability)** on BS. Under `deferred_eir`, amortize by EIR weighting of outstanding balance. Under `upfront`, full recognition at month `m`, no deferral. Invariant H-FEE: lifetime recognized fees per cohort == total fees collected.

**D.2 Tax with NOL.** Maintain `nol_balance`. If EBT<0: `tax=0`, `nol_balance += −EBT`. If EBT>0: `nol_used = min(nol_balance, EBT)`, `nol_balance −= nol_used`, `tax = (EBT − nol_used)·tax_rate`. Tax cash paid lags by `tax_pay_lag_months` (accrual sits in Tax Payable).

---

## SECTION E — CASH FLOW STATEMENT (direct-authoritative + indirect reconciliation)

**Core design decision (from `accounting-first`).** Operating cash is computed **DIRECTLY** and is authoritative. The indirect statement is presented and **asserted equal** to the direct figure (H-CFO). This is the only robust guarantee of the CF↔BS cash tie-out. Cash is the **single balance-sheet plug** (F).

### E.1 DIRECT operating cash (authoritative)

```
cash_in[t]  = O_t·First_Inst                      # first installments (certain)
            + scheduled_collections[t]            # paid installments k≥2 (B.5 step 2)
            + prepayments_cash[t]                  # full remaining balances prepaid (B.5 step 4)
            + consumer_fees_collected[t]
            + merchant_fee_cash_in_remittance[t]   # netted in remittance, lagged
            + late_fees_collected[t]
            + recoveries_cash[t]                   # RecoveryCash[t]
cash_out[t] = merchant_remittance_paid[t]          # (GMV − merchant_fee), lagged merchant_remit_lag_days
            + opex_paid[t]
            + funding_interest_paid[t]
            + taxes_paid[t]                          # accrual lagged tax_pay_lag_months
CFO_direct[t] = cash_in[t] − cash_out[t]
```
Charge-offs are **non-cash** (gross↓ and allowance↓ net to zero in cash). Note the **lending outflow** is captured as `merchant_remittance_paid − origination collections` — i.e. the receivable build is funded here, not hand-waved.

### E.2 INDIRECT reconciliation (canonical single form) — [FIX-CFO], resolves the cross-cutting double-count

The judge's central cross-cutting error: every candidate added back provision **and** separately adjusted for the allowance change. The canonical form below adds back provision **once**, adjusts for **gross** receivables, and adds back write-offs as a non-cash reduction of gross — and does **NOT** also add the allowance delta.

```
CFO_indirect[t] = Net_Income[t]
                + ProvisionCharge[t]                       # non-cash add-back (ONCE)
                − ΔGrossReceivable[t]                       # = −(Gross[t] − Gross[t−1])
                + WriteOffs[t]                              # non-cash reduction of gross → add back
                + ΔDeferredFeeLiability[t]
                + ΔPayables[t]
                + ΔTaxPayable[t]
                + ΔMerchantPayable[t]
                + (RecoveryCash[t] if recovery_accounting=="reverse_allowance" else 0)
```
Because `WriteOffs` reduce `Gross` non-cash, adding them back cancels their effect, leaving only true cash origination/collection. The **allowance delta is never added separately** when provision is already added back. **Assertion H-CFO:** `CFO_indirect[t] == CFO_direct[t]` to tolerance.

> Why this is correct: `ΔGross = Originations − Collections − Prepayments − WriteOffs`. So `−ΔGross + WriteOffs = −Originations + Collections + Prepayments`, which is exactly the cash lending/collection effect. Provision is added back because it is non-cash and already in NI; the allowance never appears as an independent line.

### E.3 Full statement layout

| Line | Formula |
|---|---|
| Net income | from D |
| + Provision expense (non-cash) | `+ ProvisionCharge[t]` |
| − Δ Gross receivables | `−(Gross[t]−Gross[t−1])` |
| + Write-offs (non-cash) | `+ WriteOffs[t]` |
| + Δ Deferred fee income | `+ ΔDeferredFeeLiability[t]` |
| + Δ Payables / Tax payable / Merchant payable | sum of working-capital deltas |
| + Recovery cash (reverse_allowance policy only) | `+ RecoveryCash[t]` if not already in NI |
| **= CFO** | (asserted == CFO_direct, H-CFO) |
| CFI | 0 (no capex; lending in CFO) |
| Debt draw / (repay) | `+(Debt[t]−Debt[t−1])` (E.4) |
| Equity issuance | `+ΔPaidIn[t]` (= initial_equity at t0) |
| Dividends | `−Dividends[t]` per policy |
| **= CFF** | sum |
| Net change in cash | CFO + CFI + CFF |
| Opening cash | `Cash[t−1]` |
| **= Ending cash** | `Cash[t]` |

### E.4 Funding draw/repay rule — single plug, [FIX-PLUG], [FIX-CIRC]

**Exactly one plug: cash is the residual from CF; debt is a bounded draw/repay rule with strict precedence.** Equity is exogenous. Debt is sized off **opening (t−1)** balances to break the funding circularity (debt interest → P&L → provision → allowance → net receivable).

```
debt_cap[t]       = funding_advance_rate · funding_base[t]
                    where funding_base[t] = GrossReceivable[t−1]  (gross)
                                          or (GrossReceivable[t−1] − A[t−1])  (net_receivable_opening)
# Precedence (resolve in this order):
cash_pre[t]       = Cash[t−1] + CFO[t] + equity_in[t] − dividends[t]   # before financing debt move
# 1) DRAW to restore buffer, capped:
need[t]           = max(0, min_cash_buffer − cash_pre[t])
draw[t]           = min(need[t], max(0, debt_cap[t] − Debt[t−1]))
# 2) Else SWEEP excess cash to repay outstanding debt:
if need[t] == 0 and Debt[t−1] > 0:
    excess[t]     = max(0, cash_pre[t] − min_cash_buffer)
    repay[t]      = min(excess[t], Debt[t−1])
ΔDebt[t]          = draw[t] − repay[t]
Debt[t]           = Debt[t−1] + ΔDebt[t]
Cash[t]           = cash_pre[t] + ΔDebt[t]          # cash is THE residual
```
This guarantees the residual lands in exactly one account (cash), debt stays within its cap, and there is no over-determination. If `debt_cap` is insufficient to hold cash ≥ buffer, cash may fall below buffer (a flagged warning, not an error); it goes negative only if `allow_overdraft=True`.

---

## SECTION F — BALANCE SHEET (EOM, every period)

| Section | Line | Formula |
|---|---|---|
| **Assets** | Cash | `Cash[t]` (from CF, E.4 — the plug) |
| | Gross receivables | `GrossReceivable[t]` (B.4) |
| | Less: ECL allowance (contra) | `− A[t]` (C.6) |
| | **Net receivables** | `GrossReceivable[t] − A[t]` |
| | Accrued interest receivable | `Σ accrued interest` (0 if apr=0) |
| | **Total assets** | Cash + Net receivables + Accrued int |
| **Liabilities** | Funding debt | `Debt[t]` (E.4) |
| | Merchant remittance payable | GMV-net-of-fee owed, unpaid (lag) |
| | Payables (accrued opex) | `Payables[t]` (DPO-based) |
| | Tax payable | `TaxPayable[t]` (lag) |
| | Deferred fee income | `DeferredFeeLiability[t]` (D.1) |
| | **Total liabilities** | sum |
| **Equity** | Paid-in capital | `initial_equity` + issuances |
| | Retained earnings | `RE[t] = RE[t−1] + Net_Income[t] − Dividends[t]` |
| | **Total equity** | Paid-in + RE |

**F.1 Balancing identity (hard invariant H-BS):**
`Total assets[t] ≡ Total liabilities[t] + Total equity[t]`, `|residual| < 1e-6·max(1, TotalAssets)`.

**Articulation summary.** Net income → RE (F). Provision → allowance contra-asset (C.6). Charge-offs reduce gross AND allowance equally. Cash from CF → Cash on BS (E, H-CASH). Because cash is the sole plug and every flow is double-entry booked (one debit, one credit), H-BS reduces to a double-entry consistency check (H-LEDGER).

**Gross vs net.** Gross = outstanding principal of all LIVE installments (incl. past-due not yet written off). Net = Gross − allowance. Charge-off changes Net by 0 (loss pre-provisioned); only under-provision residual hits P&L via the plug.

---

## SECTION G — METRICS CATALOG

**Averaging convention [FIX-AVG]:** all annualized ratios use **average-of-opening-and-closing** balances in the denominator: `avg_X[t] = (X[t−1]+X[t])/2`. Annualization factor = 12 for monthly periods. Applied uniformly.

| Metric | Formula |
|---|---|
| GMV (period / cumulative) | `Σ_m GMV_m` |
| Loans originated | `O_t` (period), `Σ O_m` (cum) |
| Active loans | count with ≥1 unpaid installment |
| AOV | `GMV / loans` |
| Take rate | `(merchant+consumer+late+interest income) / GMV` |
| Net revenue per loan | `Net_credit_adj_revenue[t] / Live[t]` |
| Portfolio yield (annualized) | `12·(interest+fee income) / avg_GrossReceivable` |
| Net interest margin (NIM) | `12·(interest income − funding cost) / avg_GrossReceivable` **[FIX-AVG]** |
| Risk-adjusted yield | yield − annualized charge-off rate |
| NPL ratio | `Stage3_Balance / GrossReceivable` (∈[0,1]) |
| ECL coverage ratio | `A[t] / GrossReceivable[t]` |
| Stage-3 coverage | `ECL_on_stage3 / Stage3_Balance` |
| Stage distribution | `Stage_k_balance / GrossReceivable`, k=1,2,3 |
| Charge-off rate (annualized) | `12·WriteOffs[t] / avg_GrossReceivable` |
| Net charge-off rate | `12·(WriteOffs − RecoveryCash) / avg_GrossReceivable` |
| Recovery rate (cohort-matched) **[FIX-REC]** | `Σ RecoveryCash[c] / Σ WriteOffs[c]` over cohorts old enough to have fully recovered (age ≥ recovery_lag); simple cumulative ratio labeled a **lower-bound run-rate** |
| Roll rate (b→b+1) | realized transition fraction per month |
| Cure rate (realized) | `cured_balance[t] / opening_delinquent[t]` |
| Cost of risk | `12·ProvisionCharge / avg_GrossReceivable` |
| Cost of funds (effective) | `12·funding_cost / avg_Debt` |
| Opex ratio | `Opex[t] / Total_revenue[t]` |
| Net margin | `Net_Income / Total_revenue` |
| ROA / ROR | `12·Net_Income / avg_TotalAssets` / `12·Net_Income / avg_GrossReceivable` |
| ROE | `12·Net_Income / avg_Equity` |
| Leverage | `Debt / Equity` |
| Funding utilization | `Debt[t] / debt_cap[t]` |
| Vintage loss curve | `cum_WriteOffs[m,a] / (O_m·OV_m)` by age a |
| Vintage delinquency curve | `delinquent_balance[m,a] / origination_balance[m]` |
| Stage migration matrix | period-over-period stage transition % |

Cohort/vintage tables produced by pivoting `Bal[m,b,t]` on `(cohort, age=t−m)`.

---

## SECTION H — INVARIANTS / TEST ASSERTIONS

Run after each period and a full pass at end. Tolerance `tol = 1e-6` absolute or relative as noted. IDs in **bold** are the articulation "big six".

| ID | Assertion |
|---|---|
| **H-BS** | `\|Assets[t] − (Liab[t]+Equity[t])\| < tol·max(1,Assets[t])` ∀t |
| **H-CASH** | `EndingCash_CF[t] == Cash_BS[t]` ∀t |
| **H-CFO** | `CFO_indirect[t] == CFO_direct[t]` ∀t (the canonical-CF check) |
| **H-ALW1** | `A[t] == A[t−1] + ProvisionCharge[t] − WriteOffs[t] + Rec_alw[t]` ∀t |
| **H-ALW2** | `A[t] == Required_ECL[t]` ∀t (bottom-up tie) |
| **H-COH** | `Σ_m Σ_{b∈LIVE} Bal[m,b,t] == GrossReceivable[t]` ∀t |
| H-CONS | per cohort: `Σ_b Bal[m,b,t] == Σ_b Bal[m,b,t−1] + Origination[m,t]` (principal conservation, all buckets) |
| H-CONS2 | per cohort lifetime: `Originated_m == Collected_m + Prepaid_m + ChargedOff_m + Outstanding_m` |
| H-ORIG | per loan: `Outstanding_after_orig == OV − inst_principal_1`; first-installment cash == `O_m·inst_principal_1` with zero delinquency **[FIX-ORIG]** |
| H-RE | `RE[t] == RE[t−1] + Net_Income[t] − Dividends[t]` ∀t |
| H-CASHCONT | `Cash[t] == Cash[t−1] + NetChangeCash[t]`; `Cash[t] == initial_cash + Σ_{j≤t} NetChange[j]` |
| H-NET | `NetReceivable[t] == GrossReceivable[t] − A[t]` ∀t |
| H-STAGE | `Stage1+Stage2+Stage3 balance == GrossReceivable[t]` ∀t |
| H-S3PD | Stage-3 ECL slices use PD=1.0 exactly (`ECL == LGD_subbucket·EAD·DF`) |
| H-CO | no LIVE balance in any bucket whose lower DPD edge `≥ charge_off_dpd` **[FIX-CO]** |
| H-ROLL | every `roll_matrix` row sums to 1.0; all state vectors ≥ 0 |
| H-WO | `WriteOffs[t] ≤ Σ eligible charge-off-bucket balance` ∀t |
| H-REC | `Σ_{j≤t} RecoveryCash[j] ≤ recovery_rate·Σ_{j≤t} WriteOffs[j]`; recoveries never hit both P&L and allowance |
| H-FEE | per cohort: `Σ lifetime recognized fees == Σ fees collected` **[FIX-FEE]** |
| H-DEBT | `Debt[t] ≤ debt_cap[t]`; `Debt[t] ≥ 0`; exactly one of draw/repay per period |
| H-FUND-CIRC | funding sized off opening (t−1) balances only (no same-period net-receivable dependency) **[FIX-CIRC]** |
| H-PD | `pd_lifetime_stage2 == 1 − ∏(1−marginal_default_curve[remaining])` (cohort↔ECL PD consistency) **[FIX-PD]** |
| H-NAN | all statement cells finite, never NaN; cash ≥ 0 unless `allow_overdraft` |
| H-NPL | `0 ≤ NPL_ratio[t] ≤ 1`; `0 ≤ ECL_coverage ≤ 1` |
| H-CUM | cumulative statements == running sum of monthly |
| H-TAX | `tax ≥ 0` when EBT>0; NOL reduces tax monotonically |
| H-LEDGER | double-entry: every flow books to exactly one debit and one credit (catches single-sided bookings) |
| **H-VAL** (config) | `Σ installment_weights == 1`; `Σ scenario_probs == 1`; all PD/LGD/roll-rates ∈ [0,1]; fees ≥ 0; `initial_cash ≤ initial_equity` |

A failing invariant is a hard pytest failure. **H-BS, H-CASH, H-CFO, H-ALW1, H-ALW2, H-COH** are the big-six articulation checks. Parametrize tests across a scenario grid: {zero-default, high-default}, {biweekly, monthly}, {upfront, deferred fees}, {apr=0, apr>0}, {standard, tight staging}, {charge_off=120, charge_off=365}, {recovery pnl, recovery reverse}.

---

## SECTION I — RECOMMENDED PYTHON MODULE / DATA-STRUCTURE LAYOUT

### I.1 Package layout
```
bnpl_engine/
  config.py          # EngineConfig dataclass (Section A) + validate() (H-VAL); presets
  schedule.py        # per-loan schedule, EIR solver, event table (B.1–B.3)
  origination.py     # O_m driver, GMV (B.2)
  state_machine.py   # cohort×bucket roll-forward (B.4–B.9) — CORE
  ifrs9.py           # staging, sub-buckets, PD/LGD/EAD, ECL, overlays, allowance roll-fwd (C)
  income_statement.py # Section D (incl. deferred-fee amortization, NOL tax)
  cashflow.py        # direct cash (authoritative) + indirect reconciliation + funding rule (E)
  balance_sheet.py   # BS assembly; cash plug; double-entry ledger (F)
  metrics.py         # Section G (avg-balance convention)
  engine.py          # Orchestrator: per-period fixed-order loop, wires modules
  invariants.py      # Section H assertions + double-entry ledger check
  ledger.py          # tiny double-entry ledger (debit/credit booking) backing H-LEDGER
  export.py          # to_excel(), to_streamlit_frames()
  tests/             # pytest: per-invariant + golden-master scenario grid
```

### I.2 Core DataFrame schemas

**`state_long` (tidy, canonical state):**
| col | dtype | note |
|---|---|---|
| `cohort` | int | origination month m |
| `month` | int | reporting month t |
| `bucket` | category | enum from B.4 |
| `loan_count` | float64 | fractional loans |
| `balance` | float64 | principal in bucket |
| `accrued_interest` | float64 | |
| `stage` | int8 | 1/2/3 (derived) |
| `sub_bucket` | category | Substandard/Doubtful/Loss/None |
| `forborne` | bool | |
| `clean_months` | int | cure counter |
| `ecl` | float64 | ECL_final for slice |

**`events` (immutable, built once):** `[cohort, installment_k, due_month, scheduled_principal, scheduled_interest]` (k≥2 only enter delinquency machinery).

**`ecl_long`:** `[cohort, month, bucket, ead, pd, lgd, df, ecl_base, ecl_weighted, ecl_final]`.

**`statements` (wide, indexed by month):** sub-frames `income_statement`, `cash_flow` (with both `cfo_direct` and `cfo_indirect` columns for H-CFO), `balance_sheet`; plus `cumulative_*` variants.

**`portfolio_summary` (indexed by month):** `gross_receivable, allowance, net_receivable, stage1/2/3_bal, npl_ratio, ecl_coverage, writeoffs, recoveries, provision_charge, debt, debt_cap, cash, live_loans`.

**`metrics` (indexed by month)** + **`vintage`** (cohort × age pivot).

### I.3 Class design

```python
@dataclass(frozen=True)
class EngineConfig: ...                      # Section A; validate() → H-VAL

class ScheduleBuilder:                       # schedule.py
    def build_events(self, cfg) -> DataFrame # events table (k=1 flagged certain, excluded from roll)

class StateMachine:                          # state_machine.py
    def originate(self, t): ...              # B.5 step 0  [FIX-ORIG]
    def step(self, t) -> StepResult         # B.5 steps 1–9; returns paid, missed, writeoffs, recov_sched, late_fees

class IFRS9Engine:                           # ifrs9.py
    def assign_stage(self, bucket, overrides, clean_months) -> int        # C.1 worst-of
    def required_ecl(self, state, t) -> float                              # C.4 + C.5 overlay
    def roll_allowance(self, A_prev, required, writeoffs, rec_alw) -> AllowanceResult  # C.6 plug

class StatementBuilder:                      # income/cashflow/balance_sheet
    def income_statement(self, t) -> dict    # D
    def cash_flow(self, t) -> dict           # E: direct (authoritative) + indirect; funding rule E.4
    def balance_sheet(self, t) -> dict       # F: cash plug; assert H-BS via ledger

class Engine:
    def run(self) -> Results:
        for t in range(T+1):
            self.state.originate(t)
            step      = self.state.step(t)
            required  = self.ifrs9.required_ecl(self.state, t)
            alw       = self.ifrs9.roll_allowance(A_prev, required, step.writeoffs, step.rec_alw)
            pnl       = self.builder.income_statement(t)     # uses alw.provision_charge
            cf        = self.builder.cash_flow(t)            # direct authoritative; funding rule; cash plug
            bs        = self.builder.balance_sheet(t)        # cash := cf.ending_cash
            self.invariants.assert_period(t)                 # big-six + per-period subset
        return Results(...)

@dataclass
class Results:
    state_long: pd.DataFrame
    income_statement: pd.DataFrame
    cash_flow: pd.DataFrame
    balance_sheet: pd.DataFrame
    portfolio_summary: pd.DataFrame
    metrics: pd.DataFrame
    vintage: pd.DataFrame
    def assert_invariants(self): ...         # full Section H
    def to_excel(self, path): ...
```

### I.4 Per-period execution order (dependency-correct)
```
originate → step (pay/roll/charge-off/recover/late-fee) → ifrs9.required_ecl
→ ifrs9.roll_allowance (provision plug) → income_statement → cash_flow (direct authoritative,
funding draw/repay off OPENING balances, cash plug) → balance_sheet (reads cf cash) → invariants
```

### I.5 Implementation notes
- **Vectorize across cohorts:** state is `(n_cohorts × n_buckets)`; roll-matrix application is `state @ T` per month (NumPy). Charge-off/recovery as side vectors.
- **Provision is always the plug** (C.6) off a bottom-up `Required_ECL`; never accumulate independently → guarantees H-ALW1/H-ALW2.
- **Cash is computed directly and is authoritative;** indirect CF is a checked reconciliation (H-CFO). This is the single most important tie-out decision.
- **Exactly one BS plug = cash;** debt is a bounded draw/repay rule off **opening** balances (breaks funding circularity, [FIX-CIRC]); equity exogenous. A double-entry ledger (`ledger.py`) backs H-LEDGER and makes H-BS hold by construction.
- **Scenario simulation** = re-instantiate `EngineConfig` and re-run `Engine`; cache by config hash for Streamlit. Widgets bind to `EngineConfig` fields.
- All balances float64; round only at export.

---

## CONSOLIDATED PARAMETER TABLE

| Param | Default | Units | Section |
|---|---|---|---|
| n_installments | 4 | # | A.1 |
| cadence_days / cadence_mode | 14 / biweekly | d / enum | A.1 |
| installment_weights | [0.25×4] | vector | A.1 |
| first_inst_at_origination | True | bool | A.1 |
| apr / interest_basis / day_count | 0.00 / simple / ACT/365 | rate / enum | A.1 |
| merchant_fee_pct / merchant_fee_recognition | 0.040 / upfront | frac / enum | A.2 |
| consumer_fee_mode / _fixed / _pct / _recognition | none / 0 / 0 / deferred_SL | enum/ccy/frac | A.2 |
| late_fee_amount / _mode / _cap / _collection_rate / _recognition | 7.00 / per_missed / 28 / 0.40 / cash_basis | ccy/enum/frac | A.2 |
| loans_origination_t0 | 1000 | # | A.3 |
| origination_growth_mode / _mom / _vector | compound / 0.05 / None | enum/frac | A.3 |
| seasonality | [1.0×12] | frac | A.3 |
| avg_order_value / aov_growth_mom | 250 / 0.00 | ccy/frac | A.3 |
| horizon_months | 36 | # | A.3 |
| dpd_bucket_edges | [0,1,8,30,60,90,120,180,365,∞] | d | A.4 |
| roll_matrix | see B.6 | matrix | A.4 |
| first_payment_default_rate / base_miss_rate | 0.03 / 0.02 | frac | A.4 |
| marginal_default_curve | see B.7 | vector | A.4 |
| charge_off_dpd / acceleration_on_default | 120 / True | d / bool | A.4 |
| recovery_rate / _lag_months / _curve / _accounting | 0.15 / 6 / None / pnl_other_income | frac/mo/enum | A.4 |
| prepayment_rate | 0.00 | frac/mo | A.4 |
| staging_basis | tight_bnpl | enum | A.5 |
| dpd_stage1_max_standard / _stage2_max_standard | 29 / 89 | d | A.5 |
| dpd_sicr_trigger_tight / _stage2_max_tight | 1 / 89 | d | A.5 |
| dpd_stage3_min | 90 | d | A.5 |
| s3_substandard_max / s3_doubtful_max | 179 / 364 | d | A.5 |
| cure_clean_months / forbearance_policy / cure_watch_months_strict | 12 / permissive / 24 | mo/enum | A.5 |
| override_bankruptcy/fraud/forborne/score_stage | 3 / 3 / 2 / 2 | stage | A.5 |
| pd_12m_stage1 | 0.03 | frac | A.6 |
| lgd_stage1 / _stage2 | 0.70 / 0.72 | frac | A.6 |
| lgd_s3_substandard / _doubtful / _loss | 0.70 / 0.875 / 1.00 | frac | A.6 |
| eir_annual / ecl_discount_enabled / ead_include_accrued_interest | None / False / True | rate/bool | A.6 |
| scenario_probs (base/up/down) | 0.50 / 0.20 / 0.30 | frac | A.7 |
| pd_multiplier base/up/down | 1.00 / 0.85 / 1.40 | mult | A.7 |
| lgd_multiplier base/up/down | 1.00 / 0.95 / 1.15 | mult | A.7 |
| management_overlay_pct / _abs | 0.00 / 0 | frac/ccy | A.7 |
| funding_advance_rate / funding_base | 0.85 / net_receivable_opening | frac/enum | A.8 |
| funding_rate_apr / funding_commitment_fee | 0.09 / 0.005 | rate/yr | A.8 |
| min_cash_buffer | 250_000 | ccy | A.8 |
| initial_equity / initial_cash | 5_000_000 / 5_000_000 | ccy | A.8 |
| dividend_policy / dividend_payout_pct | none / 0.00 | enum/frac | A.8 |
| opex_fixed_monthly / _growth_mom | 150_000 / 0.00 | ccy/frac | A.8 |
| opex_per_loan_origination / _per_active_loan / _pct_of_gmv | 3.50 / 0.25 / 0.00 | ccy/frac | A.8 |
| payables_days / merchant_remit_lag_days | 30 / 2 | d | A.8 |
| tax_rate / tax_loss_carryforward / tax_pay_lag_months | 0.25 / True / 0 | frac/bool/mo | A.8 |

## CONSOLIDATED INVARIANTS / TEST LIST

Big-six articulation: **H-BS** (BS balances), **H-CASH** (CF ending cash == BS cash), **H-CFO** (indirect == direct CFO), **H-ALW1** (allowance roll-forward ties), **H-ALW2** (allowance == bottom-up ECL), **H-COH** (cohort sum == portfolio gross).
Supporting: H-CONS / H-CONS2 (principal conservation), H-ORIG (first-installment treatment), H-RE (NI→RE), H-CASHCONT (cash continuity), H-NET (net = gross − allowance), H-STAGE (stage balances partition gross), H-S3PD (Stage-3 PD=1.0), H-CO (no live balance past charge-off edge), H-ROLL (matrix rows sum to 1), H-WO (write-off ≤ eligible), H-REC (recoveries bounded, not double-booked), H-FEE (lifetime fees recognized == collected), H-DEBT (debt within cap, single draw/repay), H-FUND-CIRC (funding off opening balances), H-PD (Stage-2 lifetime PD == roll-curve-derived), H-NAN, H-NPL, H-CUM, H-TAX, H-LEDGER (double-entry), H-VAL (config validation). Tolerance 1e-6 abs/rel. Test grid: {zero/high default} × {biweekly/monthly} × {upfront/deferred fees} × {apr 0/>0} × {standard/tight} × {charge-off 120/365} × {recovery pnl/reverse}.

## RECOMMENDED MODULE LAYOUT (schemas)

Modules: `config, schedule, origination, state_machine, ifrs9, income_statement, cashflow, balance_sheet, metrics, engine, invariants, ledger, export, tests/` (I.1). Core frames: `state_long` (cohort×month×bucket: loan_count, balance, accrued_interest, stage, sub_bucket, forborne, clean_months, ecl), immutable `events`, `ecl_long`, wide `statements` (income/cash_flow incl. cfo_direct+cfo_indirect/balance_sheet + cumulative), `portfolio_summary`, `metrics`, `vintage`. Classes: `EngineConfig` (frozen, validates), `ScheduleBuilder`, `StateMachine`, `IFRS9Engine`, `StatementBuilder`, `Engine.run()` looping the fixed per-period order (originate → step → required_ecl → roll_allowance → income_statement → cash_flow[direct authoritative + funding off opening balances + cash plug] → balance_sheet → invariants), `Results` bundling frames with `assert_invariants()` and `to_excel()`. Key disciplines: provision-as-plug, direct-cash-authoritative with indirect reconciliation, single cash plug + bounded debt rule off opening balances, double-entry ledger backing H-BS/H-LEDGER, full vectorization via `state @ roll_matrix`.