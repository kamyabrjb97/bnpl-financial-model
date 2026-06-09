"""
BNPL Financial Modeling Engine.

A cohort-based BNPL lender model with IFRS 9 ECL provisioning wired into a
fully-articulating three-statement model (P&L, Cash Flow, Balance Sheet).

Quick start:
    from bnpl_model import EngineConfig, run_model
    res = run_model(EngineConfig())
    res.income_statement, res.cash_flow, res.balance_sheet, res.portfolio, res.metrics
    res.to_excel("outputs/model.xlsx")
"""
from .config import EngineConfig, PRESETS, preset_default, default_roll_matrix, DPM
from .engine import Engine, Results, run_model
from .ifrs9 import IFRS9Engine, LIVE_BUCKETS
from .invariants import check_invariants, assert_invariants
from .metrics import build_metrics

__all__ = [
    "EngineConfig", "PRESETS", "preset_default", "default_roll_matrix", "DPM",
    "Engine", "Results", "run_model",
    "IFRS9Engine", "LIVE_BUCKETS",
    "check_invariants", "assert_invariants", "build_metrics",
]

__version__ = "1.0.0"
