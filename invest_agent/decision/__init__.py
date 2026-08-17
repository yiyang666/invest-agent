"""Research-only decision inputs built from the validated strategy registry."""

from .registry import build_decision_input, load_strategy_registry, validate_strategy_registry
from .monthly import build_monthly_research_decision_pack
from .replay import run_decision_replay
from .closure import classify_phase4_closure, run_phase4_closure_audit
from .reporting import build_research_report, render_research_report_markdown
from .explanation import (
    build_grounded_explanation,
    render_grounded_explanation_markdown,
    run_explanation_acceptance,
    validate_grounded_explanation,
)
from .pipeline import build_monthly_research_pipeline, write_immutable

__all__ = [
    "build_decision_input",
    "load_strategy_registry",
    "validate_strategy_registry",
    "build_monthly_research_decision_pack",
    "run_decision_replay",
    "classify_phase4_closure",
    "run_phase4_closure_audit",
    "build_research_report",
    "render_research_report_markdown",
    "build_grounded_explanation",
    "render_grounded_explanation_markdown",
    "run_explanation_acceptance",
    "validate_grounded_explanation",
    "build_monthly_research_pipeline",
    "write_immutable",
]
