"""
Invariant / tie-out tests across a scenario grid (spec Section H test matrix).

Every scenario must satisfy all articulation invariants: the balance sheet
balances, CF ending cash == BS cash, indirect == direct CFO, the allowance
roll-forward ties, allowance == required ECL, stages partition gross, etc.
"""
import itertools

import numpy as np
import pytest

from bnpl_model import EngineConfig, run_model
from bnpl_model.invariants import assert_invariants


# ---- scenario grid ----------------------------------------------------------
GRID = dict(
    default=[("low", dict(base_miss_rate=0.01, first_payment_default_rate=0.02)),
             ("high", dict(base_miss_rate=0.08, first_payment_default_rate=0.15))],
    cadence=[("biweekly", dict(cadence_mode="biweekly")),
             ("monthly", dict(cadence_mode="monthly"))],
    fees=[("upfront", dict(consumer_fee_mode="fixed", consumer_fee_fixed=5.0,
                           consumer_fee_recognition="upfront")),
          ("deferred", dict(consumer_fee_mode="pct", consumer_fee_pct=0.02,
                            consumer_fee_recognition="deferred_straight_line"))],
    apr=[("zero", dict(apr=0.0)), ("interest", dict(apr=0.18))],
    staging=[("standard", dict(staging_basis="standard")),
             ("tight", dict(staging_basis="tight_bnpl"))],
    chargeoff=[("co120", dict(charge_off_dpd=120)),
               ("co365", dict(charge_off_dpd=365))],
    recovery=[("pnl", dict(recovery_accounting="pnl_other_income")),
              ("reverse", dict(recovery_accounting="reverse_allowance"))],
)


def _merge(combo):
    cfg = dict(horizon_months=18)
    label = []
    for axis, (name, params) in combo.items():
        cfg.update(params)
        label.append(f"{axis}={name}")
    return "|".join(label), cfg


def _all_combos():
    axes = list(GRID.keys())
    # full cartesian product is 128 combos — sample a representative, deterministic subset
    choices = [GRID[a] for a in axes]
    combos = list(itertools.product(*choices))
    # take every 3rd combo for speed plus the two extremes
    sel = combos[::3] + [tuple(c[0] for c in choices), tuple(c[-1] for c in choices)]
    out = []
    for combo in sel:
        d = {axes[i]: combo[i] for i in range(len(axes))}
        out.append(_merge(d))
    return out


@pytest.mark.parametrize("label,cfg_kwargs", _all_combos())
def test_invariants_hold(label, cfg_kwargs):
    res = run_model(EngineConfig(**cfg_kwargs))
    assert_invariants(res)
    # all invariant rows must pass
    assert res.invariant_report["passed"].all(), \
        f"{label}: failed {res.invariant_report[~res.invariant_report['passed']]['id'].tolist()}"


def test_first_installment_excluded_and_receivable_net():
    """Gross receivable booked at origination == loans*(OV - first installment).

    Use a 45-day cadence so installment 2 matures in month 1, leaving month-0
    closing gross equal to the booked-at-origination amount (nothing collected yet).
    """
    cfg = EngineConfig(horizon_months=1, loans_origination_t0=1000, avg_order_value=200,
                       installment_weights=[0.25, 0.25, 0.25, 0.25],
                       cadence_mode="custom_days", cadence_days=45,
                       base_miss_rate=0.0, first_payment_default_rate=0.0, prepayment_rate=0.0)
    res = run_model(cfg)
    gross0 = res.portfolio.loc[0, "gross_receivable"]
    expected = 1000 * (200 - 0.25 * 200)   # OV - first installment principal = 150 each
    assert gross0 == pytest.approx(expected, rel=1e-6)


def test_npl_ratio_in_bounds_and_positive_with_defaults():
    res = run_model(EngineConfig(horizon_months=24, base_miss_rate=0.05))
    npl = res.portfolio["npl_ratio"]
    assert (npl >= 0).all() and (npl <= 1).all()
    assert npl.iloc[-1] > 0   # some loans should reach Stage 3 over 24 months


def test_zero_default_has_no_writeoffs():
    res = run_model(EngineConfig(horizon_months=12, base_miss_rate=0.0,
                                 first_payment_default_rate=0.0))
    assert res.portfolio["write_offs"].sum() == pytest.approx(0.0, abs=1e-9)
    assert res.portfolio["npl_ratio"].max() == pytest.approx(0.0, abs=1e-9)


def test_loans_originated_exact():
    cfg = EngineConfig(horizon_months=6, loans_origination_t0=1000,
                       origination_growth_mom=0.10, origination_growth_mode="compound")
    res = run_model(cfg)
    assert res.portfolio.loc[0, "loans_originated"] == pytest.approx(1000)
    assert res.portfolio.loc[3, "loans_originated"] == pytest.approx(1000 * 1.1 ** 3)


def test_config_validation_rejects_bad_weights():
    with pytest.raises(ValueError):
        EngineConfig(installment_weights=[0.5, 0.4]).validate()   # sums to 0.9
    with pytest.raises(ValueError):
        EngineConfig(scenario_prob_base=0.6, scenario_prob_upside=0.6,
                     scenario_prob_downside=0.6).validate()


def test_config_validation_rejects_bad_seasonality():
    with pytest.raises(ValueError):
        EngineConfig(seasonality=[1.0, 1.0, 1.0]).validate()      # not length 12


# ---- regression tests for review fixes --------------------------------------
def test_override_overlay_raises_npl_and_ecl():
    """Non-DPD overrides reclassify performing balance and lift NPL + coverage."""
    base = run_model(EngineConfig(horizon_months=24))
    ovr = run_model(EngineConfig(horizon_months=24, override_forborne_fraction=0.10,
                                 override_fraud_fraction=0.05))
    assert ovr.portfolio["npl_ratio"].iloc[-1] > base.portfolio["npl_ratio"].iloc[-1]
    assert ovr.portfolio["ecl_coverage"].iloc[-1] > base.portfolio["ecl_coverage"].iloc[-1]
    assert_invariants(ovr)


def test_deferred_fee_not_drained_at_horizon():
    """Deferred consumer fee stays as a liability while loans are still live."""
    cfg = EngineConfig(horizon_months=12, consumer_fee_mode="fixed", consumer_fee_fixed=20.0,
                       consumer_fee_recognition="deferred_straight_line", merchant_fee_pct=0.0,
                       cadence_mode="monthly")
    res = run_model(cfg)
    dfi = res.balance_sheet["deferred_fee_income"]
    # the last month must NOT spike fee income nor zero the liability (the old bug)
    assert dfi.iloc[-1] > 0
    cons = res.income_statement["consumer_fee_income"]
    assert cons.iloc[-1] < 1.6 * cons.iloc[-2]     # no terminal dump


def test_equity_in_cff_and_cash_reconciles_from_zero():
    res = run_model(EngineConfig(horizon_months=12))
    cf = res.cash_flow
    assert cf["equity_issuance"].iloc[0] == pytest.approx(res.config.initial_cash)
    assert cf["opening_cash"].iloc[0] == pytest.approx(0.0)
    # cumulative net change in cash equals final balance-sheet cash
    assert cf["net_change_cash"].cumsum().iloc[-1] == pytest.approx(
        res.balance_sheet["cash"].iloc[-1], rel=1e-6)


def test_cashneg_flag_is_feasibility_not_articulation():
    """A thin-equity scenario flags H-CASHNEG but still passes articulation."""
    res = run_model(EngineConfig(initial_equity=200_000, initial_cash=200_000,
                                 loans_origination_t0=5000, opex_fixed_monthly=20_000,
                                 horizon_months=24))
    rep = res.invariant_report
    assert rep[rep["category"] == "articulation"]["passed"].all()   # tie-outs hold
    assert not rep[rep["id"] == "H-CASHNEG"]["passed"].iloc[0]      # feasibility flagged
    assert_invariants(res, articulation_only=True)                  # does not raise


def _single_cohort_12m(accel: bool):
    """One 12-installment monthly cohort, aged long enough to fully resolve."""
    T = 24
    return run_model(EngineConfig(
        horizon_months=T, n_installments=12, cadence_mode="monthly",
        origination_growth_mode="explicit_vector", origination_vector=[1000] + [0] * (T - 1),
        base_miss_rate=0.025, first_payment_default_rate=0.04, prepayment_rate=0.0,
        charge_off_dpd=120, acceleration_on_default=accel))


def test_acceleration_only_forfeits_defaulting_loans_remaining_principal():
    """Acceleration charges off the defaulting loans' OWN remaining principal, not a
    slice of the performing book. Each defaulting loan has >= 1 installment in arrears,
    so its whole-loan loss is at most (N-1) x its charged-off arrears: the accelerated
    write-off can never exceed (N-1) x the no-acceleration write-off. (The old pro-rata
    formula blew through this bound: ~15.8% vintage loss vs a ~2.4% ceiling.)"""
    no_acc = _single_cohort_12m(False).portfolio["write_offs"].sum()
    acc = _single_cohort_12m(True).portfolio["write_offs"].sum()
    assert no_acc > 0
    assert acc > no_acc                     # remaining schedule exists at charge-off
    assert acc <= no_acc * (12 - 1) + 1e-6


def test_acceleration_is_noop_when_loan_matures_before_charge_off():
    """Pay-in-4 biweekly matures in ~6 weeks, long before 120 DPD: nothing to accelerate."""
    kw = dict(horizon_months=18, n_installments=4, cadence_mode="biweekly", charge_off_dpd=120)
    on = run_model(EngineConfig(acceleration_on_default=True, **kw)).portfolio["write_offs"].sum()
    off = run_model(EngineConfig(acceleration_on_default=False, **kw)).portfolio["write_offs"].sum()
    assert on == pytest.approx(off, rel=1e-12)
