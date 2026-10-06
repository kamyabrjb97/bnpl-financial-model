"""
API-layer tests for the FastAPI wrapper (server/main.py).

These cover the thin wrapper's behaviour — especially the Stage-2 lifetime PD
method selector (marginal curve vs fixed point) and the editable marginal default
curve — without touching engine logic.
"""
import pytest
from fastapi.testclient import TestClient

from server.main import app
from bnpl_model.config import preset_default
from bnpl_model.ifrs9 import lifetime_pd_stage2

client = TestClient(app)


def _cov(**params):
    params.setdefault("horizon_months", 24)
    r = client.post("/api/run", json=params)
    assert r.status_code == 200, r.text
    return r.json()["kpis"]["final_ecl_coverage"]


def test_schema_exposes_stage2_method_controls():
    d = client.get("/api/schema").json()
    defaults, groups = d["defaults"], d["groups"]
    assert defaults["pd_lifetime_stage2_mode"] == "roll_matrix"
    assert isinstance(defaults["marginal_default_curve"], str)          # editable string
    # seeded fixed value is the derived PD, snapped to the slider step (0.005)
    derived = lifetime_pd_stage2(preset_default())
    assert defaults["pd_lifetime_stage2_override"] == pytest.approx(
        round(round(derived / 0.005) * 0.005, 4))
    ifrs = next(g for g in groups if g["title"].startswith("IFRS 9"))
    keys = [f["key"] for f in ifrs["fields"]]
    assert "pd_lifetime_stage2_mode" in keys
    assert "pd_lifetime_stage2_override" in keys
    assert "marginal_default_curve" in keys


def test_curve_mode_matches_engine_derivation():
    """Curve mode (default curve) must reproduce the engine's curve-derived behaviour."""
    curve_str = client.get("/api/schema").json()["defaults"]["marginal_default_curve"]
    cov_api = _cov(pd_lifetime_stage2_mode="marginal_curve", marginal_default_curve=curve_str)
    # engine with override=None and the curve method selected directly
    cov_engine = _cov(pd_stage2_method="marginal_curve")
    assert cov_api == pytest.approx(cov_engine, rel=1e-9)


def test_fixed_mode_pins_higher_pd_raises_coverage():
    base = _cov(pd_lifetime_stage2_mode="marginal_curve")
    fixed = _cov(pd_lifetime_stage2_mode="fixed", pd_lifetime_stage2_override=0.30)
    assert fixed > base


def test_editing_curve_changes_derived_pd():
    base = _cov(pd_lifetime_stage2_mode="marginal_curve",
                marginal_default_curve="0.015, 0.02, 0.018, 0.01, 0.005, 0.002")
    hotter = _cov(pd_lifetime_stage2_mode="marginal_curve",
                  marginal_default_curve="0.03, 0.04, 0.036, 0.02, 0.01, 0.004")
    assert hotter > base


@pytest.mark.parametrize("bad", [
    "abc, def",          # non-numeric
    "",                  # empty
    "   ",               # whitespace only
    "nan, 0.02",         # NaN slips past float() -> must be rejected
    "inf, 0.02",         # inf
    "-inf, 0.02",        # -inf
    "infinity, 0.02",    # spelled out
    "1e999, 0.02",       # overflow -> inf
    "1.5, 0.02",         # > 1
    "-0.1, 0.02",        # < 0
])
def test_bad_curve_string_returns_400(bad):
    r = client.post("/api/run", json={"marginal_default_curve": bad})
    assert r.status_code == 400, f"{bad!r} should be rejected, got {r.status_code}"


def test_used_pd_echo_matches_engine_in_both_modes():
    """The echoed pd_lifetime_stage2_used = derived in curve mode, = override in fixed."""
    hot = "0.03, 0.04, 0.036, 0.02, 0.01, 0.004"
    derived = lifetime_pd_stage2(preset_default().scenario(
        marginal_default_curve=[0.03, 0.04, 0.036, 0.02, 0.01, 0.004]))
    r_curve = client.post("/api/run", json={"pd_lifetime_stage2_mode": "marginal_curve",
                                            "marginal_default_curve": hot}).json()
    assert r_curve["pd_lifetime_stage2_used"] == pytest.approx(derived, rel=1e-9)
    r_fixed = client.post("/api/run", json={"pd_lifetime_stage2_mode": "fixed",
                                            "pd_lifetime_stage2_override": 0.22}).json()
    assert r_fixed["pd_lifetime_stage2_used"] == pytest.approx(0.22)


def test_curve_drives_cohort_timing_even_in_fixed_mode():
    """In fixed mode the Stage-2 PD is pinned, but the curve still shapes default
    timing in the cohort engine. The engine uses the curve's SHAPE relative to
    curve[0], so two curves with different shapes (differing at later installments)
    must give different realized losses."""
    common = {"pd_lifetime_stage2_mode": "fixed", "pd_lifetime_stage2_override": 0.10,
              "horizon_months": 24}
    # same curve[0]; differ sharply at indices 2,3 (installments #3/#4 miss shaping)
    low = client.post("/api/run", json={**common,
        "marginal_default_curve": "0.02, 0.02, 0.004, 0.002, 0.001, 0.0005"}).json()
    high = client.post("/api/run", json={**common,
        "marginal_default_curve": "0.02, 0.02, 0.09, 0.07, 0.05, 0.03"}).json()
    wo_low = sum(row["write_offs"] for row in low["portfolio"])
    wo_high = sum(row["write_offs"] for row in high["portfolio"])
    assert wo_high > wo_low


def test_toggle_consistency_fixed_matches_curve_derived():
    """Switching from curve mode to a fixed value equal to the curve-derived PD
    leaves the Stage-2 ECL unchanged (what the UI sync guarantees)."""
    hot = "0.03, 0.04, 0.036, 0.02, 0.01, 0.004"
    r_curve = client.post("/api/run", json={"pd_lifetime_stage2_mode": "marginal_curve",
                                            "marginal_default_curve": hot, "horizon_months": 24}).json()
    used = r_curve["pd_lifetime_stage2_used"]
    r_fixed = client.post("/api/run", json={"pd_lifetime_stage2_mode": "fixed",
                                            "pd_lifetime_stage2_override": used,
                                            "marginal_default_curve": hot, "horizon_months": 24}).json()
    assert r_fixed["kpis"]["final_ecl_coverage"] == pytest.approx(
        r_curve["kpis"]["final_ecl_coverage"], rel=1e-9)


def test_run_articulates_in_both_modes():
    for params in ({"pd_lifetime_stage2_mode": "marginal_curve"},
                   {"pd_lifetime_stage2_mode": "fixed", "pd_lifetime_stage2_override": 0.25}):
        rep = client.post("/api/run", json={**params, "horizon_months": 18}).json()["invariants"]
        artic = [iv for iv in rep if iv["category"] == "articulation"]
        assert all(iv["passed"] for iv in artic)


def test_zero_installments_is_a_400_not_a_500():
    r = client.post("/api/run", json={"n_installments": 0})
    assert r.status_code == 400


@pytest.mark.skipif(not __import__("os").path.isdir(__import__("server.main").main.DIST),
                    reason="SPA route only mounted when web/dist is built")
@pytest.mark.parametrize("path", ["/C:/Windows/win.ini", "/..%2F..%2Fserver%2Fmain.py",
                                  "/..%2F..%2FREADME.md"])
def test_spa_route_does_not_serve_files_outside_dist(path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.text.lstrip().lower().startswith("<!doctype html")   # index.html fallback
