"""Learning Service (spec §20-25, §57, §73-78). Milestone M8.

Trade evaluation, counterfactual evaluation (what rejected candidates / WAITs would
have done), metrics (spec §24), pattern detection, and knowledge updates. Separates
good decision from good outcome (spec §23); avoids overfitting (spec §74).
"""

from apm.learning.evaluator import LearningService

__all__ = ["LearningService"]
