"""
IFRS 9 engine — staging, ECL = PD x LGD x EAD x DF, and the allowance
roll-forward. This module is the *source of truth* for provisioning and is a
faithful implementation of docs/ifrs9_source_of_truth.md / Section C of the spec.

Key disciplines:
  * DPD bucket edges sit exactly on staging/sub-bucket thresholds  [FIX-BKT]
  * staging is the WORST of the DPD-based stage and any override   (C.1)
  * Stage 3 PD is forced to 1.0                                    (H-S3PD)
  * Stage 2 lifetime PD derived from the same marginal default curve the
    cohort engine uses, so realised charge-offs and ECL are consistent [FIX-PD]
  * allowance is solved as the P&L "plug" off a bottom-up required ECL, so
    allowance[t] == required_ecl[t] by construction              (H-ALW1/2)
"""
from __future__ import annotations

from dataclasses import dataclass
from math import prod
from typing import Dict, Tuple

from .config import EngineConfig

# Live DPD buckets in worsening order, plus absorbing states.
LIVE_BUCKETS = ["CUR", "D1", "D8", "D30", "D60", "D90", "D120", "D180", "D365"]
ABSORBING = ["PAID", "PREPAID", "CHARGED_OFF"]
ALL_BUCKETS = LIVE_BUCKETS + ABSORBING

# Lower DPD edge of each live bucket (used for staging & charge-off routing).
BUCKET_LOWER_DPD: Dict[str, int] = {
    "CUR": 0, "D1": 1, "D8": 8, "D30": 30, "D60": 60,
    "D90": 90, "D120": 120, "D180": 180, "D365": 365,
}
# Next worse live bucket (for roll "forward").
NEXT_BUCKET: Dict[str, str] = {
    "CUR": "D1", "D1": "D8", "D8": "D30", "D30": "D60", "D60": "D90",
    "D90": "D120", "D120": "D180", "D180": "D365", "D365": "CHARGED_OFF",
}


def stage_for_bucket(bucket: str, cfg: EngineConfig) -> int:
    """Base IFRS 9 stage for a DPD bucket under the configured staging basis."""
    if bucket not in BUCKET_LOWER_DPD:
        return 1
    dpd = BUCKET_LOWER_DPD[bucket]
    if cfg.staging_basis == "tight_bnpl":
        if dpd >= cfg.dpd_stage3_min:
            return 3
        if dpd >= cfg.dpd_sicr_trigger_tight:   # 1+ DPD -> SICR
            return 2
        return 1
    # standard
    if dpd >= cfg.dpd_stage3_min:
        return 3
    if dpd > cfg.dpd_stage1_max_standard:       # 30+ DPD -> Stage 2
        return 2
    return 1


def sub_bucket_for(bucket: str) -> str | None:
    """Stage-3 NPL sub-classification."""
    if bucket in ("D90", "D120"):
        return "Substandard"
    if bucket == "D180":
        return "Doubtful"
    if bucket == "D365":
        return "Loss"
    return None


def lgd_for_bucket(bucket: str, stage: int, cfg: EngineConfig) -> float:
    if stage == 1:
        return cfg.lgd_stage1
    if stage == 2:
        return cfg.lgd_stage2
    sub = sub_bucket_for(bucket)
    if sub == "Substandard":
        return cfg.lgd_s3_substandard
    if sub == "Doubtful":
        return cfg.lgd_s3_doubtful
    if sub == "Loss":
        return cfg.lgd_s3_loss
    return cfg.lgd_s3_substandard


def lifetime_pd_stage2(cfg: EngineConfig) -> float:
    """Stage-2 lifetime PD derived from the marginal default curve [FIX-PD].

    pd_lifetime = 1 - prod(1 - marginal_pd) over the FULL curve. Overridable.

    NOTE (documented approximation): this is a single portfolio-wide *full-remaining-
    life* PD applied to all Stage-2 exposure, not an age-aware PD that shortens as a
    cohort amortises. Because it uses the full curve it is a conservative upper bound
    (loans partway through their life have less remaining life and lower true lifetime
    PD), so Stage-2 ECL is, if anything, slightly over-provisioned — the prudent
    direction. Set ``pd_lifetime_stage2_override`` to pin an exact value.
    """
    if cfg.pd_lifetime_stage2_override is not None:
        return cfg.pd_lifetime_stage2_override
    curve = cfg.marginal_default_curve or [0.0]
    surv = prod(1.0 - max(0.0, min(1.0, m)) for m in curve)
    return max(0.0, min(1.0, 1.0 - surv))


def base_pd_for_stage(stage: int, cfg: EngineConfig, pd_life2: float) -> float:
    if stage == 1:
        return cfg.pd_12m_stage1
    if stage == 2:
        return pd_life2
    return 1.0  # Stage 3 defaulted  (H-S3PD)


@dataclass
class AllowanceResult:
    provision_charge: float   # P&L expense (may be negative = release)
    allowance_close: float    # == required_ecl by construction
    write_offs: float
    rec_alw: float


class IFRS9Engine:
    """Stateless calculator: turns a bucket->balance distribution into ECL, and
    rolls the loss allowance forward via the provision-as-plug identity."""

    def __init__(self, cfg: EngineConfig):
        self.cfg = cfg
        self.pd_life2 = lifetime_pd_stage2(cfg)
        # probability-weighted scenario set
        self.scenarios = [
            ("base", cfg.scenario_prob_base, cfg.pd_multiplier_base, cfg.lgd_multiplier_base),
            ("upside", cfg.scenario_prob_upside, cfg.pd_multiplier_upside, cfg.lgd_multiplier_upside),
            ("downside", cfg.scenario_prob_downside, cfg.pd_multiplier_downside, cfg.lgd_multiplier_downside),
        ]

    def discount_factor(self) -> float:
        # Default off for short-horizon BNPL (DF=1). When enabled, discount the
        # ECL by roughly half the loan term at the effective interest rate. NOTE:
        # for interest-free BNPL (apr=0) with no eir_annual the fallback EIR is 0,
        # so DF=1 even when enabled — set eir_annual to discount fee-driven yield.
        if not self.cfg.ecl_discount_enabled:
            return 1.0
        eir = self.cfg.eir_annual if self.cfg.eir_annual is not None else self.cfg.apr
        base = 1.0 + eir
        if base <= 1e-9:                       # guard EIR <= -1
            return 1.0
        t_years = (self.cfg.n_installments * self.cfg.cadence_days / 365.0) / 2.0
        return 1.0 / (base ** t_years)

    def ecl_for_bucket(self, bucket: str, ead: float) -> Tuple[float, int, float, float]:
        """Return (ecl, stage, pd_used, lgd_used) for one bucket's exposure,
        probability-weighted across macro scenarios and topped up by the
        management overlay percentage."""
        if ead <= 0:
            return 0.0, stage_for_bucket(bucket, self.cfg), 0.0, 0.0
        stage = stage_for_bucket(bucket, self.cfg)
        pd_base = base_pd_for_stage(stage, self.cfg, self.pd_life2)
        lgd_base = lgd_for_bucket(bucket, stage, self.cfg)
        df = self.discount_factor()

        ecl_weighted = 0.0
        for _name, prob, pd_mult, lgd_mult in self.scenarios:
            pd_sc = min(1.0, pd_base * pd_mult) if stage != 3 else 1.0
            lgd_sc = min(1.0, lgd_base * lgd_mult)
            ecl_weighted += prob * (pd_sc * lgd_sc * ead * df)
        ecl_final = ecl_weighted * (1.0 + self.cfg.management_overlay_pct)
        return ecl_final, stage, pd_base, lgd_base

    def required_ecl(self, balances: Dict[str, float],
                     override_fractions: dict | None = None) -> dict:
        """Bottom-up required closing allowance over the live buckets.

        ``override_fractions`` = {"to3": f3, "to2": f2}: the fraction of the
        *performing* (Stage-1) balance that is reclassified to Stage 3 / Stage 2 by
        non-DPD overrides (bankruptcy/fraud -> 3, forbearance/score drop -> 2),
        applied worst-of. Default None -> pure DPD staging.

        Returns total ECL, per-bucket ECL (DPD-driven), and per-stage balance/ECL
        rollups (override-adjusted). The management absolute overlay is allocated
        pro-rata so the stage ECLs reconcile to the total.
        """
        per_bucket: Dict[str, float] = {}
        stage_bal = {1: 0.0, 2: 0.0, 3: 0.0}
        stage_ecl = {1: 0.0, 2: 0.0, 3: 0.0}
        for b in LIVE_BUCKETS:
            ead = balances.get(b, 0.0)
            ecl, stage, _pd, _lgd = self.ecl_for_bucket(b, ead)
            per_bucket[b] = ecl
            stage_bal[stage] += ead
            stage_ecl[stage] += ecl

        # ---- non-DPD override overlay: reclassify a slice of performing balance ----
        if override_fractions:
            s1 = stage_bal[1]
            if s1 > 1e-12:
                s1_ecl_rate = stage_ecl[1] / s1
                ecl3_rate = self.ecl_for_bucket("D90", 1.0)[0]   # Stage-3 substandard per $
                ecl2_rate = self.ecl_for_bucket("D30", 1.0)[0]   # Stage-2 per $
                f3 = min(1.0, max(0.0, override_fractions.get("to3", 0.0)))
                f2 = min(1.0 - f3, max(0.0, override_fractions.get("to2", 0.0)))
                bal3, bal2 = s1 * f3, s1 * f2
                stage_bal[1] -= (bal3 + bal2); stage_bal[3] += bal3; stage_bal[2] += bal2
                stage_ecl[1] -= (bal3 + bal2) * s1_ecl_rate
                stage_ecl[3] += bal3 * ecl3_rate
                stage_ecl[2] += bal2 * ecl2_rate

        base_total = stage_ecl[1] + stage_ecl[2] + stage_ecl[3]
        # management absolute overlay, allocated pro-rata across stages/buckets
        abs_overlay = self.cfg.management_overlay_abs
        if abs_overlay != 0.0 and base_total > 1e-12:
            scale = (base_total + abs_overlay) / base_total
            per_bucket = {b: v * scale for b, v in per_bucket.items()}
            stage_ecl = {s: v * scale for s, v in stage_ecl.items()}
            total = base_total + abs_overlay
        else:
            total = base_total + abs_overlay
        return {
            "total": total,
            "per_bucket": per_bucket,
            "stage_balance": stage_bal,
            "stage_ecl": stage_ecl,
        }

    @staticmethod
    def roll_allowance(allowance_prev: float, required_ecl: float,
                       write_offs: float, rec_alw: float) -> AllowanceResult:
        """Provision-as-plug (C.6).

        provision = required - A_prev + write_offs - rec_alw
        A_close   = A_prev + provision - write_offs + rec_alw  == required   (ties H-ALW1/2)
        """
        provision = required_ecl - allowance_prev + write_offs - rec_alw
        allowance_close = allowance_prev + provision - write_offs + rec_alw
        return AllowanceResult(
            provision_charge=provision,
            allowance_close=allowance_close,
            write_offs=write_offs,
            rec_alw=rec_alw,
        )
