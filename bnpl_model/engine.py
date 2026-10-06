"""
BNPL cohort engine + three-statement assembly (Sections B, D, E, F of the spec).

A deterministic, monthly-period, cohort/vintage roll-forward. For each reporting
month it:
  1. originates a new cohort (gross receivable booked NET of the first installment,
     which is certain cash and excluded from the delinquency machine)  [FIX-ORIG]
  2. ages every live cohort through the DPD state machine (due -> pay/miss ->
     roll/cure/charge-off -> prepay -> recover)
  3. computes the bottom-up required ECL and rolls the loss allowance via the
     provision-as-plug identity (delegated to IFRS9Engine)
  4. assembles the Income Statement, Cash Flow (direct = authoritative, with an
     indirect reconciliation), and Balance Sheet
  5. checks the per-period articulation invariants

Cash is the single balance-sheet plug; debt is a bounded draw/repay rule sized off
*opening* balances (breaks the funding circularity, [FIX-CIRC]).

Simplifications (documented, do not affect the accounting tie-outs):
  * interest income is recognised = collected in-period (no accrued-interest
    receivable); irrelevant when apr = 0.
  * loan counts: `loans_originated` is exact; active/stage loan counts are
    balance-weighted estimates (financials are driven by exact principal balances).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Dict, List

import numpy as np
import pandas as pd

from .config import EngineConfig, DPM
from .ifrs9 import (
    IFRS9Engine, LIVE_BUCKETS, BUCKET_LOWER_DPD, NEXT_BUCKET, sub_bucket_for,
)

# delinquent live buckets (everything past CUR), in worsening order
DELINQ_BUCKETS = [b for b in LIVE_BUCKETS if b != "CUR"]
BIDX = {b: i for i, b in enumerate(LIVE_BUCKETS)}
NLB = len(LIVE_BUCKETS)


@dataclass
class Results:
    config: EngineConfig
    income_statement: pd.DataFrame
    cash_flow: pd.DataFrame
    balance_sheet: pd.DataFrame
    portfolio: pd.DataFrame          # portfolio_summary (stages, ECL, NPL, debt, cash...)
    stage_detail: pd.DataFrame       # per-period x bucket: balance, stage, ECL
    vintage: pd.DataFrame            # cohort x age cumulative loss / outstanding
    metrics: pd.DataFrame            # filled by metrics.py
    invariant_report: pd.DataFrame   # filled by invariants.py

    def to_excel(self, path: str):
        from .export import to_excel
        to_excel(self, path)


class Engine:
    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg.validate()
        self.ifrs9 = IFRS9Engine(self.cfg)
        self.T = int(cfg.horizon_months)
        self._precompute()

    # ------------------------------------------------------------------ setup
    def _precompute(self):
        cfg = self.cfg
        T = self.T
        w = np.asarray(cfg.installment_weights, dtype=float)
        N = cfg.n_installments

        # installment due-month offset for k = 1..N (k=1 at origination, offset 0)
        self.due_offset = [floor((k) * cfg.cadence_days / DPM) for k in range(N)]  # k index 0..N-1
        # offset for installment index j (0-based): day = j*cadence_days
        self.due_offset = [floor(j * cfg.cadence_days / DPM) for j in range(N)]
        # principal weight of installments 2..N (0-based j=1..N-1) relative to P_rem
        p_rem_weight = w[1:].sum() if N > 1 else 0.0
        max_age = max(self.due_offset) if N > 1 else 0
        self.max_due_age = max_age
        self.loan_span_months = max_age + 1

        # sched_frac[a] = fraction of P_rem maturing at age a (installments 2..N)
        sched = np.zeros(max_age + 1)
        for j in range(1, N):                       # installments 2..N
            a = self.due_offset[j]
            if p_rem_weight > 0:
                sched[a] += w[j] / p_rem_weight
        self.sched_frac = sched
        # g[a] = hazard fraction of *remaining* CUR principal due at age a
        g = np.zeros(max_age + 1)
        for a in range(max_age + 1):
            remaining = sched[a:].sum()
            g[a] = (sched[a] / remaining) if remaining > 1e-12 else 0.0
        self.g = g
        self.first_due_age = int(np.argmax(sched > 0)) if sched.sum() > 0 else 0
        # rem_after[a] = contractual fraction of P_rem still scheduled AFTER age a's dues
        # (the CUR principal a single loan still holds once age a has been billed)
        self.rem_after = np.array([sched[a + 1:].sum() for a in range(max_age + 1)])

        # ---- per-installment miss rate, blended (principal-weighted) by age ----
        # The first POST-ORIGINATION installment (j=1, i.e. installment 2) carries the
        # first-payment-default rate; later installments use base_miss_rate modulated by
        # the marginal default curve indexed by *installment position* (so the curve's
        # tail isn't dead) — base_miss sets the level, the curve sets the relative shape.
        curve = cfg.marginal_default_curve or [1.0]
        ref = curve[0] if curve[0] > 0 else 1.0

        def _inst_rate(j):
            if j == 1:
                return float(np.clip(cfg.first_payment_default_rate, 0.0, 0.99))
            shape = curve[min(j, len(curve) - 1)] / ref
            return float(np.clip(cfg.base_miss_rate * shape, 0.0, 0.99))

        miss_by_age = np.zeros(max_age + 1)
        for a in range(max_age + 1):
            num = den = 0.0
            for j in range(1, N):
                if self.due_offset[j] == a:
                    num += w[j] * _inst_rate(j)
                    den += w[j]
            miss_by_age[a] = (num / den) if den > 1e-12 else 0.0
        self.miss_by_age = miss_by_age

        # ---- late-fee cap: share of late-fee events that survive the per-loan cap ----
        # Misses per loan K ~ Poisson-binomial over installments 2..N (rates above).
        # A loan pays at most cap/fee events, so the capped share is E[min(K,k)]/E[K].
        self.late_fee_cap_factor = self._late_fee_cap_factor(
            [_inst_rate(j) for j in range(1, N)])

        # origination counts O[m]
        O = np.zeros(T)
        if cfg.origination_growth_mode == "explicit_vector" and cfg.origination_vector:
            v = list(cfg.origination_vector)
            for m in range(T):
                O[m] = v[m] if m < len(v) else (v[-1] if v else 0)
        else:
            for m in range(T):
                seas = cfg.seasonality[m % len(cfg.seasonality)] if cfg.seasonality else 1.0
                if cfg.origination_growth_mode == "linear":
                    base = cfg.loans_origination_t0 * (1 + cfg.origination_growth_mom * m)
                else:  # compound
                    base = cfg.loans_origination_t0 * (1 + cfg.origination_growth_mom) ** m
                O[m] = max(0.0, base * seas)
        self.O = O

        # average order value per cohort
        self.OV = np.array([cfg.avg_order_value * (1 + cfg.aov_growth_mom) ** m for m in range(T)])
        self.w0 = w[0]
        self.per_loan_after_orig = self.OV * (1.0 - self.w0)        # outstanding per loan after orig
        self.GMV = O * self.OV
        self.merchant_fee_total = cfg.merchant_fee_pct * self.GMV   # cohort merchant fee $

        # consumer fee per loan
        if cfg.consumer_fee_mode == "fixed":
            cf_per_loan = np.full(T, cfg.consumer_fee_fixed)
        elif cfg.consumer_fee_mode == "pct":
            cf_per_loan = cfg.consumer_fee_pct * self.OV
        else:
            cf_per_loan = np.zeros(T)
        self.consumer_fee_total = O * cf_per_loan

        # ---- fee recognition schedules (recognised income per period) ----
        self.recog_merchant = np.zeros(T)
        self.recog_consumer = np.zeros(T)
        span = self.loan_span_months
        for m in range(T):
            self._spread_fee(self.recog_merchant, m, self.merchant_fee_total[m],
                             cfg.merchant_fee_recognition, span)
            self._spread_fee(self.recog_consumer, m, self.consumer_fee_total[m],
                             cfg.consumer_fee_recognition, span)
        # cumulative fee cash realised at origination month m = merchant + consumer total
        self.fee_cash_at_m = self.merchant_fee_total + self.consumer_fee_total

    def _spread_fee(self, arr, m, total, mode, span):
        T = self.T
        if total == 0:
            return
        if mode == "deferred_straight_line":
            per = total / span
            for a in range(span):
                t = m + a
                if t < T:
                    arr[t] += per
                # recognition past the horizon simply falls off; the unrecognised
                # remainder correctly stays in the deferred-fee liability at T (fixes
                # terminal-month revenue/liability overstatement).
        else:  # upfront
            if m < T:
                arr[m] += total

    def _late_fee_cap_factor(self, rates: List[float]) -> float:
        cfg = self.cfg
        mean = sum(rates)
        if mean <= 1e-12 or cfg.late_fee_amount <= 0:
            return 1.0
        k_cap = max(0.0, cfg.late_fee_cap_per_loan) / cfg.late_fee_amount
        dist = np.zeros(len(rates) + 1)        # dist[k] = P(K = k)
        dist[0] = 1.0
        for r in rates:
            dist[1:] = dist[1:] * (1.0 - r) + dist[:-1] * r
            dist[0] *= (1.0 - r)
        capped = sum(p * min(k, k_cap) for k, p in enumerate(dist))
        return float(capped / mean)

    def _rem_per_loan(self, m: int, a: int) -> float:
        """Contractual CUR principal one loan of cohort m still holds once age a has
        been billed (net of prepayment)."""
        if a >= len(self.rem_after):
            return 0.0
        return float(self.per_loan_after_orig[m] * self.rem_after[a]
                     * (1.0 - self.cfg.prepayment_rate) ** (a + 1))

    def _exposure_buckets(self, m: int, a: int, row: np.ndarray) -> np.ndarray:
        """Re-cut a cohort's bucket balances to the instrument level for staging/ECL.

        The roll machine only moves the *missed installment* into D1+; the same loan's
        not-yet-due principal stays in CUR. IFRS 9 stages the whole exposure, so each
        delinquent bucket also carries its loans' remaining CUR principal
        (arrears / avg installment ~ loans in the bucket). Gross is unchanged."""
        out = row.copy()
        cur = row[BIDX["CUR"]]
        N = self.cfg.n_installments
        if cur <= 1e-12 or N <= 1:
            return out
        avg_inst = self.per_loan_after_orig[m] / (N - 1)
        rem = self._rem_per_loan(m, a)
        if avg_inst <= 1e-9 or rem <= 0:
            return out
        idx = [BIDX[b] for b in DELINQ_BUCKETS]
        linked = row[idx] / avg_inst * rem
        tot = linked.sum()
        if tot > cur:
            linked *= cur / tot
        out[idx] += linked
        out[BIDX["CUR"]] -= linked.sum()
        return out

    def _miss_rate(self, age: int) -> float:
        """Principal-weighted blended miss rate for the installments maturing at this
        cohort age (precomputed in _precompute; FPD applies only to installment 2)."""
        if 0 <= age < len(self.miss_by_age):
            return float(self.miss_by_age[age])
        return 0.0

    # ------------------------------------------------------------------ run
    def run(self) -> Results:
        cfg = self.cfg
        T = self.T
        co_dpd = cfg.charge_off_dpd

        bal = np.zeros((T, NLB))                 # bal[cohort, bucket] principal
        cum_paid = np.zeros(T)
        cum_prepaid = np.zeros(T)
        cum_charged = np.zeros(T)
        cum_originated = np.zeros(T)
        recovery_sched = np.zeros(T + cfg.recovery_lag_months + 2)

        # opening balances / stocks
        allowance_prev = 0.0
        debt_prev = 0.0
        cash_prev = 0.0                          # cash flow reconciles from zero; equity
        paid_in = cfg.initial_cash               # is injected as a t=0 financing inflow
        re_prev = 0.0
        deferred_prev = 0.0
        opex_payable_prev = 0.0
        nol_balance = 0.0

        rows_is, rows_cf, rows_bs, rows_pf, rows_stage = [], [], [], [], []
        vintage_rows = []

        lag_frac = float(np.clip(cfg.payables_days / DPM, 0.0, 1.0))

        for t in range(T):
            gross_open = bal.sum()               # opening gross receivable
            performing_open = bal[:, BIDX["CUR"]].sum()   # balance whose dues are being paid
            # ---------- Step 0: originate cohort t ----------
            O_t = self.O[t]
            cum_originated[t] = O_t
            recv_booked = O_t * self.per_loan_after_orig[t]
            bal[t, BIDX["CUR"]] += recv_booked
            first_inst_cash = O_t * self.OV[t] * self.w0
            consumer_fee_cash = self.consumer_fee_total[t]
            merchant_remit = O_t * self.OV[t] * (1.0 - cfg.merchant_fee_pct)   # cash OUT
            GMV_t = self.GMV[t]

            # ---------- Step 1-6: age every live cohort ----------
            collections = 0.0      # paid installments + cures (cash in, reduces gross)
            prepay_cash = 0.0
            writeoffs = 0.0
            missed_total = 0.0
            late_events = 0.0

            for m in range(t + 1):
                opening = bal[m].copy()
                if opening.sum() <= 1e-12:
                    continue
                a = t - m
                # --- dues from CUR ---
                g_a = self.g[a] if a < len(self.g) else 0.0
                cur = opening[BIDX["CUR"]]
                due = cur * g_a
                mr = self._miss_rate(a)
                paid = due * (1.0 - mr)
                missed = due * mr
                cur_after_due = cur - due
                prepay = cur_after_due * cfg.prepayment_rate
                cur_after = cur_after_due - prepay

                new_b = np.zeros(NLB)
                new_b[BIDX["CUR"]] = cur_after
                wo = 0.0
                cure_cash = 0.0

                # --- roll delinquency buckets (on opening balances) ---
                for b in DELINQ_BUCKETS:
                    bb = opening[BIDX[b]]
                    if bb <= 1e-15:
                        continue
                    lower = BUCKET_LOWER_DPD[b]
                    if lower >= co_dpd:
                        wo += bb                     # at/over charge-off edge
                        continue
                    row = cfg.roll_matrix.get(b)
                    if row is None:
                        new_b[BIDX[b]] += bb
                        continue
                    cure = bb * row.get("cure", 0.0)
                    stay = bb * row.get("stay", 0.0)
                    fwd = bb * row.get("forward", 0.0)
                    co = bb * row.get("charge_off", 0.0)
                    cure_cash += cure
                    new_b[BIDX[b]] += stay
                    nb = NEXT_BUCKET[b]
                    if nb == "CHARGED_OFF" or BUCKET_LOWER_DPD.get(nb, 10**9) >= co_dpd:
                        wo += fwd
                    else:
                        new_b[BIDX[nb]] += fwd
                    wo += co

                # newly missed -> D1
                new_b[BIDX["D1"]] += missed

                # acceleration on default: ONLY the defaulting loans forfeit their own
                # remaining CUR principal. Charged-off arrears / avg installment ~ number
                # of defaulting loans; each still holds the contractual remainder after
                # age a (shrunk by prepayment). Performing loans' CUR is untouched.
                if (cfg.acceleration_on_default and wo > 0 and cfg.n_installments > 1
                        and a < len(self.rem_after)):
                    avg_inst = self.per_loan_after_orig[m] / (cfg.n_installments - 1)
                    if avg_inst > 1e-9:
                        n_default = wo / avg_inst
                        accel = min(n_default * self._rem_per_loan(m, a), new_b[BIDX["CUR"]])
                        new_b[BIDX["CUR"]] -= accel
                        wo += accel

                # late fee events ~ missed principal / avg installment principal
                if cfg.late_fee_mode != "none" and missed > 0 and cfg.n_installments > 1:
                    avg_inst = self.per_loan_after_orig[m] / (cfg.n_installments - 1)
                    if avg_inst > 1e-9:
                        late_events += missed / avg_inst * self.late_fee_cap_factor

                # schedule recovery on this month's write-offs
                if wo > 0 and cfg.recovery_rate > 0:
                    if cfg.recovery_curve:
                        for d, frac in enumerate(cfg.recovery_curve):
                            idx = t + d
                            if idx < len(recovery_sched):
                                recovery_sched[idx] += wo * cfg.recovery_rate * frac
                    else:
                        idx = t + cfg.recovery_lag_months
                        if idx < len(recovery_sched):
                            recovery_sched[idx] += wo * cfg.recovery_rate

                # commit cohort state + cumulative sinks
                bal[m] = new_b
                cum_paid[m] += paid + cure_cash
                cum_prepaid[m] += prepay
                cum_charged[m] += wo

                collections += paid + cure_cash
                prepay_cash += prepay
                writeoffs += wo
                missed_total += missed

            # ---------- aggregate closing state ----------
            # staging works on instrument-level exposure (arrears + the loan's own
            # remaining CUR), not the raw roll-machine buckets
            expo = np.zeros(NLB)
            for m in range(t + 1):
                if bal[m].sum() > 1e-12:
                    expo += self._exposure_buckets(m, t - m, bal[m])
            agg_bucket = {b: expo[BIDX[b]] for b in LIVE_BUCKETS}
            gross_close = sum(agg_bucket.values())

            # recoveries landing this period
            recoveries_cash = recovery_sched[t] if t < len(recovery_sched) else 0.0

            # ---------- IFRS 9: required ECL + allowance roll-forward ----------
            override_fractions = {
                "to3": cfg.override_fraud_fraction + cfg.override_bankruptcy_fraction,
                "to2": cfg.override_forborne_fraction + cfg.override_score_deterioration_fraction,
            }
            req = self.ifrs9.required_ecl(agg_bucket, override_fractions)
            required_ecl = req["total"]
            rec_alw = recoveries_cash if cfg.recovery_accounting == "reverse_allowance" else 0.0
            alw = self.ifrs9.roll_allowance(allowance_prev, required_ecl, writeoffs, rec_alw)
            provision = alw.provision_charge
            allowance = alw.allowance_close

            # ---------- Income statement (accrual) ----------
            # interest accrues (and is collected) on the performing CUR balance only:
            # missed installments in D1+ pay nothing, so they earn no cash interest
            interest_income = (cfg.apr / 12.0) * performing_open if cfg.apr > 0 else 0.0
            merch_income = self.recog_merchant[t]
            cons_income = self.recog_consumer[t]
            late_assessed = late_events * cfg.late_fee_amount
            late_cash = late_assessed * cfg.late_fee_collection_rate
            late_income = late_cash if cfg.late_fee_recognition == "cash_basis" else late_assessed
            # accrual books the assessed fee; the share not collected in-period is
            # written off as uncollectible (no late-fee receivable is carried)
            late_uncollectible = late_income - late_cash
            recovery_income = recoveries_cash if cfg.recovery_accounting == "pnl_other_income" else 0.0
            total_revenue = interest_income + merch_income + cons_income + late_income + recovery_income

            debt_cap_open = cfg.funding_advance_rate * (
                gross_open if cfg.funding_base == "gross_receivable"
                else max(0.0, gross_open - allowance_prev))
            undrawn_open = max(0.0, debt_cap_open - debt_prev)
            funding_cost = debt_prev * cfg.funding_rate_apr / 12.0 + undrawn_open * cfg.funding_commitment_fee / 12.0

            active_loans = float(np.sum(
                np.where(self.per_loan_after_orig > 1e-9,
                         bal.sum(axis=1) / np.where(self.per_loan_after_orig > 1e-9, self.per_loan_after_orig, 1.0),
                         0.0)))
            opex = (cfg.opex_fixed_monthly * (1 + cfg.opex_fixed_growth_mom) ** t
                    + cfg.opex_per_loan_origination * O_t
                    + cfg.opex_per_active_loan * active_loans
                    + cfg.opex_pct_of_gmv * GMV_t)

            ebt = total_revenue - funding_cost - provision - late_uncollectible - opex
            # tax with NOL carryforward
            if ebt <= 0:
                tax = 0.0
                if cfg.tax_loss_carryforward:
                    nol_balance += -ebt
            else:
                if cfg.tax_loss_carryforward:
                    nol_used = min(nol_balance, ebt)
                    nol_balance -= nol_used
                    tax = (ebt - nol_used) * cfg.tax_rate
                else:
                    tax = ebt * cfg.tax_rate
            net_income = ebt - tax

            # dividends
            dividends = (cfg.dividend_payout_pct * net_income
                         if cfg.dividend_policy == "payout_pct" and net_income > 0 else 0.0)

            # ---------- Cash flow (direct, authoritative) ----------
            opex_paid = opex * (1.0 - lag_frac) + opex_payable_prev
            opex_payable = opex * lag_frac
            taxes_paid = tax                       # tax_pay_lag = 0 default
            funding_interest_paid = funding_cost

            cash_in = (first_inst_cash + collections + prepay_cash
                       + consumer_fee_cash + interest_income + late_cash + recoveries_cash)
            cash_out = merchant_remit + opex_paid + funding_interest_paid + taxes_paid
            cfo_direct = cash_in - cash_out

            # equity injected as a financing inflow in period 0 (CF reconciles from zero)
            equity_in = cfg.initial_cash if t == 0 else 0.0

            cash_pre = cash_prev + cfo_direct + equity_in - dividends
            # funding draw/repay (cash is the residual plug)
            need = max(0.0, cfg.min_cash_buffer - cash_pre)
            draw = min(need, max(0.0, debt_cap_open - debt_prev))
            repay = 0.0
            if need <= 1e-9 and debt_prev > 0:
                excess = max(0.0, cash_pre - cfg.min_cash_buffer)
                repay = min(excess, debt_prev)
            d_debt = draw - repay
            debt = debt_prev + d_debt
            cash = cash_pre + d_debt
            # NOTE: if the funding cap can't keep cash >= 0 and allow_overdraft is False,
            # cash is left negative and flagged by invariant H-CASHNEG (undercapitalised).
            cff = d_debt + equity_in - dividends

            # ---------- working-capital deltas ----------
            d_gross = gross_close - gross_open
            d_allow = allowance - allowance_prev
            d_deferred = (deferred_prev_update := self._deferred_liability(t)) - deferred_prev
            d_opex_pay = opex_payable - opex_payable_prev

            # indirect CFO (identity-derived): NI + dAllowance - dGross + dDeferred + dOpexPayable
            cfo_indirect = net_income + d_allow - d_gross + d_deferred + d_opex_pay
            # (recoveries already consistent: pnl_other_income -> in NI; reverse_allowance -> in dAllow)

            # ---------- Balance sheet ----------
            net_receivable = gross_close - allowance
            deferred = deferred_prev_update
            re = re_prev + net_income - dividends
            total_assets = cash + net_receivable
            total_liab = debt + deferred + opex_payable
            total_equity = paid_in + re

            # ---------- stage rollups (override-adjusted, from the IFRS 9 engine) ----------
            stage_bal = req["stage_balance"]
            npl = stage_bal[3] / gross_close if gross_close > 1e-9 else 0.0
            avg_loan_out = gross_close / active_loans if active_loans > 1e-9 else 0.0

            # ---------- record rows ----------
            rows_is.append(dict(
                month=t, interest_income=interest_income, merchant_fee_income=merch_income,
                consumer_fee_income=cons_income, late_fee_income=late_income,
                recovery_income=recovery_income, total_revenue=total_revenue,
                funding_cost=funding_cost, net_interest_fee_income=total_revenue - funding_cost,
                provision_expense=provision, late_fee_uncollectible=late_uncollectible,
                operating_expense=opex, ebt=ebt,
                income_tax=tax, net_income=net_income, dividends=dividends))
            rows_cf.append(dict(
                month=t, net_income=net_income, provision_addback=provision,
                charge_offs_to_allowance=-(writeoffs - rec_alw), d_gross_receivable=-d_gross,
                d_deferred_fee=d_deferred, d_opex_payable=d_opex_pay,
                cfo=cfo_direct, cfo_indirect=cfo_indirect, cfi=0.0,
                debt_draw_repay=d_debt, equity_issuance=equity_in, dividends=-dividends, cff=cff,
                net_change_cash=cfo_direct + cff, opening_cash=cash_prev, ending_cash=cash,
                cash_in=cash_in, cash_out=cash_out))
            rows_bs.append(dict(
                month=t, cash=cash, gross_receivables=gross_close, ecl_allowance=-allowance,
                net_receivables=net_receivable, total_assets=total_assets,
                funding_debt=debt, deferred_fee_income=deferred, opex_payable=opex_payable,
                total_liabilities=total_liab, paid_in_capital=paid_in, retained_earnings=re,
                total_equity=total_equity, balance_check=total_assets - total_liab - total_equity))
            rows_pf.append(dict(
                month=t, loans_originated=O_t, cumulative_loans=cum_originated[:t + 1].sum(),
                active_loans=active_loans, gmv=GMV_t, cumulative_gmv=self.GMV[:t + 1].sum(),
                gross_receivable=gross_close, allowance=allowance, net_receivable=net_receivable,
                stage1_balance=stage_bal[1], stage2_balance=stage_bal[2], stage3_balance=stage_bal[3],
                npl_ratio=npl, ecl_coverage=allowance / gross_close if gross_close > 1e-9 else 0.0,
                provision_charge=provision, write_offs=writeoffs, recoveries=recoveries_cash,
                debt=debt, debt_cap=debt_cap_open, cash=cash,
                required_ecl=required_ecl))
            for b in LIVE_BUCKETS:
                ead = agg_bucket[b]
                ecl_b, stg, pd_b, lgd_b = self.ifrs9.ecl_for_bucket(b, ead)
                est_loans = ead / avg_loan_out if avg_loan_out > 1e-9 else 0.0
                rows_stage.append(dict(
                    month=t, bucket=b, dpd_lower=BUCKET_LOWER_DPD[b], stage=stg,
                    sub_bucket=sub_bucket_for(b) or "", balance=ead, est_loans=est_loans,
                    pd=pd_b, lgd=lgd_b, ecl=ecl_b))

            # roll opening stocks
            allowance_prev = allowance
            debt_prev = debt
            cash_prev = cash
            re_prev = re
            deferred_prev = deferred
            opex_payable_prev = opex_payable

        # vintage table (cohort x age): cumulative loss rate & outstanding
        for m in range(T):
            denom = self.O[m] * self.OV[m]
            if denom <= 1e-9:
                continue
            vintage_rows.append(dict(
                cohort=m, loans=self.O[m], origination_gmv=denom,
                cum_charged_off=cum_charged[m], cum_loss_rate=cum_charged[m] / denom,
                outstanding=bal[m].sum(),
                cum_collected=cum_paid[m], cum_prepaid=cum_prepaid[m]))

        df_is = pd.DataFrame(rows_is).set_index("month")
        df_cf = pd.DataFrame(rows_cf).set_index("month")
        df_bs = pd.DataFrame(rows_bs).set_index("month")
        df_pf = pd.DataFrame(rows_pf).set_index("month")
        df_stage = pd.DataFrame(rows_stage)
        df_vint = pd.DataFrame(vintage_rows)

        results = Results(
            config=self.cfg, income_statement=df_is, cash_flow=df_cf, balance_sheet=df_bs,
            portfolio=df_pf, stage_detail=df_stage, vintage=df_vint,
            metrics=pd.DataFrame(), invariant_report=pd.DataFrame())

        from .metrics import build_metrics
        from .invariants import check_invariants
        results.metrics = build_metrics(results)
        results.invariant_report = check_invariants(results)
        return results

    # deferred fee liability at end of period t = cumulative fee cash realised
    # (at each origination month) minus cumulative fee income recognised.
    def _deferred_liability(self, t: int) -> float:
        cash_realised = self.fee_cash_at_m[:t + 1].sum()
        recognised = self.recog_merchant[:t + 1].sum() + self.recog_consumer[:t + 1].sum()
        return max(0.0, cash_realised - recognised)


def run_model(cfg: EngineConfig) -> Results:
    """Convenience entry point."""
    return Engine(cfg).run()
