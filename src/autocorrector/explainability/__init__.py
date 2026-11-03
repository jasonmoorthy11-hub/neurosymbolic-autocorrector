"""Explainability module — structured correction reports.

Provides ExplainabilityEngine that builds reports explaining why corrections
were made, which rules triggered, source document citations, and confidence scoring.
"""

from autocorrector.explainability.engine import ExplainabilityEngine

__all__ = ["ExplainabilityEngine"]
