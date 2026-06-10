"""
EngineConfig — every configurable input for the BNPL financial model.

Mirrors Section A / the consolidated parameter table of docs/model_spec.md.
Every threshold, rate and assumption is a named field with a default, so the
app and scenario tooling can flex any of them.

Design rules honoured here:
  * one frozen-ish dataclass (use `.scenario(**overrides)` to derive variants)
  * `.validate()` enforces config invariant H-VAL
  * defaults follow the IFRS 9 source of truth (docs/ifrs9_source_of_truth.md)
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace, asdict
from typing import Optional, List, Dict

# Average days per calendar month (used to map a biweekly/custom cadence onto
# monthly reporting periods). 365.25 / 12.
DPM = 30.4375


@dataclass
class EngineConfig:
    # ----- A.1 Product / schedule -----
    n_installments: int = 4
    cadence_days: int = 14
    cadence_mode: str = "biweekly"          # biweekly | monthly | custom_days
    installment_weights: Optional[List[float]] = None  # principal split; sums to 1
    first_inst_at_origination: bool = True
    apr: float = 0.00                       # 0 = interest-free pay-in-N
    interest_basis: str = "simple"          # simple | effective
    day_count: str = "ACT/365"

    # ----- A.2 Revenue / fees -----
    merchant_fee_pct: float = 0.040
    merchant_fee_recognition: str = "upfront"          # upfront | deferred_straight_line
    consumer_fee_mode: str = "none"                    # fixed | pct | none
    consumer_fee_fixed: float = 0.00
    consumer_fee_pct: float = 0.00
    consumer_fee_recognition: str = "deferred_straight_line"  # upfront | deferred_straight_line
    late_fee_amount: float = 7.00
    late_fee_mode: str = "per_missed_installment"      # per_missed_installment | none
    late_fee_cap_per_loan: float = 28.00
    late_fee_collection_rate: float = 0.40
    late_fee_recognition: str = "cash_basis"           # cash_basis | accrual

    # ----- A.3 Origination / growth -----
    loans_origination_t0: float = 1000
    origination_growth_mode: str = "compound"          # compound | linear | explicit_vector
    origination_growth_mom: float = 0.05
    origination_vector: Optional[List[float]] = None
    seasonality: Optional[List[float]] = None          # 12-vector multipliers
    avg_order_value: float = 250.00
    aov_growth_mom: float = 0.00
    horizon_months: int = 36

    # ----- A.4 Credit / roll-rate / recovery -----
    # Per delinquent bucket: (cure, stay, forward, charge_off) fractions summing to 1.
    # Buckets below the charge-off edge only. "forward" auto-routes to charge-off
    # when the next bucket's lower DPD edge >= charge_off_dpd.
    roll_matrix: Optional[Dict[str, Dict[str, float]]] = None
    first_payment_default_rate: float = 0.03   # P(first post-origination installment missed)
    base_miss_rate: float = 0.02               # baseline P(a scheduled installment missed)
    marginal_default_curve: Optional[List[float]] = None  # by months-on-book
    charge_off_dpd: int = 120                  # DPD at which an unpaid balance is written off
    acceleration_on_default: bool = True
    recovery_rate: float = 0.15
    recovery_lag_months: int = 6
    recovery_curve: Optional[List[float]] = None
    recovery_accounting: str = "pnl_other_income"   # pnl_other_income | reverse_allowance
    prepayment_rate: float = 0.00

    # ----- A.5 IFRS 9 staging -----
    staging_basis: str = "tight_bnpl"          # standard | tight_bnpl
    dpd_stage1_max_standard: int = 29
    dpd_stage2_max_standard: int = 89
    dpd_sicr_trigger_tight: int = 1
    dpd_stage2_max_tight: int = 89
    dpd_stage3_min: int = 90
    s3_substandard_max: int = 179
    s3_doubtful_max: int = 364
    cure_clean_months: int = 12
    forbearance_policy: str = "permissive"     # permissive | strict_eba
    cure_watch_months_strict: int = 24
    override_bankruptcy_stage: int = 3
    override_fraud_stage: int = 3
    override_forborne_stage: int = 2
    override_score_deterioration_stage: int = 2
    # Non-DPD override OVERLAY: fraction of the *performing* (Stage-1) book flagged
    # with each condition regardless of DPD. The flagged slice is floored at the
    # corresponding override stage (worst-of), feeding ECL and the NPL/stage tables.
    # Default 0 -> overrides inactive and the model behaves purely DPD-driven.
    override_forborne_fraction: float = 0.00
    override_score_deterioration_fraction: float = 0.00
    override_fraud_fraction: float = 0.00
    override_bankruptcy_fraction: float = 0.00

    # ----- A.6 IFRS 9 PD / LGD / EAD / discounting -----
    pd_12m_stage1: float = 0.03
    lgd_stage1: float = 0.70
    lgd_stage2: float = 0.72
    lgd_s3_substandard: float = 0.70
    lgd_s3_doubtful: float = 0.875
    lgd_s3_loss: float = 1.00
    eir_annual: Optional[float] = None
    ecl_discount_enabled: bool = False
    ead_include_accrued_interest: bool = True
    # explicit Stage-2 lifetime PD override; if None it is derived from the
    # marginal_default_curve (keeps cohort engine and ECL consistent, [FIX-PD])
    pd_lifetime_stage2_override: Optional[float] = None

    # ----- A.7 Macro overlays -----
    scenario_prob_base: float = 0.50
    scenario_prob_upside: float = 0.20
    scenario_prob_downside: float = 0.30
    pd_multiplier_base: float = 1.00
    pd_multiplier_upside: float = 0.85
    pd_multiplier_downside: float = 1.40
    lgd_multiplier_base: float = 1.00
    lgd_multiplier_upside: float = 0.95
    lgd_multiplier_downside: float = 1.15
    management_overlay_pct: float = 0.00
    management_overlay_abs: float = 0.00

    # ----- A.8 Funding / capital / opex / tax -----
    funding_advance_rate: float = 0.85
    funding_base: str = "net_receivable_opening"   # gross_receivable | net_receivable_opening
    funding_rate_apr: float = 0.09
    funding_commitment_fee: float = 0.005
    min_cash_buffer: float = 250_000.0
    initial_equity: float = 5_000_000.0
    initial_cash: float = 5_000_000.0
    allow_overdraft: bool = False
    dividend_policy: str = "none"                   # none | payout_pct
    dividend_payout_pct: float = 0.00
    opex_fixed_monthly: float = 150_000.0
    opex_fixed_growth_mom: float = 0.00
    opex_per_loan_origination: float = 3.50
    opex_per_active_loan: float = 0.25
    opex_pct_of_gmv: float = 0.00
    payables_days: int = 30           # opex DPO; modelled as a single <=1-month carry
    #   (a DPO > ~30d is capped at one month — opex payable holds at most one month of opex)
    merchant_remit_lag_days: int = 2  # informational: at monthly resolution a sub-month lag
    #   is settled in-period, so no merchant-remittance payable is carried
    tax_rate: float = 0.25
    tax_loss_carryforward: bool = True
    tax_pay_lag_months: int = 0       # informational: tax is paid in-period (no tax-payable lag wired)

    # ------------------------------------------------------------------
    def __post_init__(self):
        if self.installment_weights is None:
            n = max(1, int(self.n_installments))
            self.installment_weights = [1.0 / n] * n
        if self.seasonality is None:
            self.seasonality = [1.0] * 12
        if self.marginal_default_curve is None:
            # illustrative BNPL shape — early peak (Section B.7)
            self.marginal_default_curve = [0.015, 0.020, 0.018, 0.010, 0.005, 0.002]
        if self.roll_matrix is None:
            self.roll_matrix = default_roll_matrix()
        if self.cadence_mode == "monthly":
            self.cadence_days = 30
        elif self.cadence_mode == "biweekly":
            self.cadence_days = 14

    # ----- scenario helpers -----
    def scenario(self, **overrides) -> "EngineConfig":
        """Return a copy with the given fields overridden (for scenario runs)."""
        return replace(self, **overrides)

    def to_dict(self) -> dict:
        return asdict(self)

    # ----- validation: config invariant H-VAL -----
    def validate(self) -> "EngineConfig":
        errs = []
        w = self.installment_weights
        if len(w) != self.n_installments:
            errs.append(f"installment_weights len {len(w)} != n_installments {self.n_installments}")
        if abs(sum(w) - 1.0) > 1e-9:
            errs.append(f"installment_weights sum {sum(w):.6f} != 1.0")
        if any(x < 0 for x in w):
            errs.append("installment_weights must be non-negative")
        sp = self.scenario_prob_base + self.scenario_prob_upside + self.scenario_prob_downside
        if abs(sp - 1.0) > 1e-9:
            errs.append(f"scenario probabilities sum {sp:.6f} != 1.0")
        for name in ("merchant_fee_pct", "consumer_fee_pct", "recovery_rate",
                     "prepayment_rate", "base_miss_rate", "first_payment_default_rate",
                     "pd_12m_stage1", "lgd_stage1", "lgd_stage2", "lgd_s3_substandard",
                     "lgd_s3_doubtful", "lgd_s3_loss", "funding_advance_rate",
                     "late_fee_collection_rate", "tax_rate", "dividend_payout_pct",
                     "override_forborne_fraction", "override_score_deterioration_fraction",
                     "override_fraud_fraction", "override_bankruptcy_fraction"):
            v = getattr(self, name)
            if not (0.0 <= v <= 1.0):
                errs.append(f"{name}={v} must be in [0,1]")
        if self.n_installments < 1:
            errs.append("n_installments must be >= 1")
        if self.horizon_months < 1:
            errs.append("horizon_months must be >= 1")
        if self.initial_cash > self.initial_equity + 1e-6:
            errs.append("initial_cash must be <= initial_equity")
        if self.staging_basis not in ("standard", "tight_bnpl"):
            errs.append(f"unknown staging_basis {self.staging_basis}")
        if self.seasonality is not None and len(self.seasonality) != 12:
            errs.append(f"seasonality must have length 12, got {len(self.seasonality)}")
        if self.apr <= -1.0:
            errs.append("apr must be > -1.0")
        if self.eir_annual is not None and self.eir_annual <= -1.0:
            errs.append("eir_annual must be > -1.0")
        # roll-matrix rows sum to 1 (H-ROLL, config-time check)
        for b, row in self.roll_matrix.items():
            s = sum(row.values())
            if abs(s - 1.0) > 1e-9:
                errs.append(f"roll_matrix[{b}] sums to {s:.6f} != 1.0")
            if any(v < 0 for v in row.values()):
                errs.append(f"roll_matrix[{b}] has negative probability")
        if errs:
            raise ValueError("EngineConfig.validate() failed:\n  - " + "\n  - ".join(errs))
        return self


def default_roll_matrix() -> Dict[str, Dict[str, float]]:
    """Default monthly transition fractions for delinquent buckets (Section B.6).

    Keys are the *live* delinquent buckets below the charge-off edge. Each row maps
    to {cure, stay, forward, charge_off} summing to 1.0. `forward` auto-converts to
    a charge-off when the next bucket would be at/over `charge_off_dpd`.
    `cure` returns the balance to performing (collected as cash).
    """
    return {
        "D1":   {"cure": 0.40, "stay": 0.15, "forward": 0.45, "charge_off": 0.00},
        "D8":   {"cure": 0.28, "stay": 0.22, "forward": 0.50, "charge_off": 0.00},
        "D30":  {"cure": 0.16, "stay": 0.24, "forward": 0.60, "charge_off": 0.00},
        "D60":  {"cure": 0.10, "stay": 0.20, "forward": 0.70, "charge_off": 0.00},
        "D90":  {"cure": 0.05, "stay": 0.25, "forward": 0.70, "charge_off": 0.00},
        "D120": {"cure": 0.02, "stay": 0.18, "forward": 0.00, "charge_off": 0.80},
        "D180": {"cure": 0.02, "stay": 0.18, "forward": 0.80, "charge_off": 0.00},
    }


# ---- presets --------------------------------------------------------------
def preset_default() -> EngineConfig:
    """Calibrated baseline: 4-installment biweekly interest-free pay-in-4 for a
    growing early-stage book that reaches profitability as it scales. Used as the
    app's starting scenario. (Raw `EngineConfig()` keeps the spec's illustrative
    defaults.)"""
    return EngineConfig(
        loans_origination_t0=2500,
        origination_growth_mom=0.06,
        avg_order_value=220.0,
        merchant_fee_pct=0.05,
        consumer_fee_mode="fixed",
        consumer_fee_fixed=1.50,
        opex_fixed_monthly=55_000.0,
        opex_per_loan_origination=2.50,
        base_miss_rate=0.025,
        first_payment_default_rate=0.04,
        recovery_rate=0.20,
        horizon_months=36,
    ).validate()


def preset_monthly_interest() -> EngineConfig:
    """Monthly cadence, interest-bearing variant."""
    return EngineConfig(
        cadence_mode="monthly", apr=0.18, consumer_fee_mode="pct",
        consumer_fee_pct=0.01,
    ).validate()


PRESETS = {
    "Default (pay-in-4, biweekly, interest-free)": preset_default,
    "Monthly, interest-bearing": preset_monthly_interest,
}
