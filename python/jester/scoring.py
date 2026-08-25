"""§7.2 scoring rubric (locked, D-8). Pure functions, fully unit-testable.

All subscores are on a 1-10 scale. ``competition`` is nullable: when the critic
has not verified it it stays None and is imputed as 5 (R29) -- the imputed
value is used verbatim and is never renormalized.

    overall = 0.4*demand + 0.3*feasibility + 0.3*(11 - competition)

When competition is None (imputed = 5):

    overall = 0.4*demand + 0.3*feasibility + 1.8
"""
from typing import Optional

SCORE_MIN = 1.0
SCORE_MAX = 10.0
# Imputed competition when the critic left it unchecked (R29).
IMPUTED_COMPETITION = 5.0


def clip(value: float, lo: float = SCORE_MIN, hi: float = SCORE_MAX) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def compute_overall(
    demand_signal: float,
    feasibility: float,
    competition: Optional[float] = None,
) -> float:
    """Derive the overall score per §7.2. ``competition`` of None is imputed to
    5 (R29) -- the literal imputed term is 0.3*(11 - 5) = 1.8.

    The eval harness asserts that, for an unchecked idea, the overall equals
    ``0.4*demand + 0.3*feasibility + 1.8`` *exactly* -- this function must keep
    that arithmetic intact.
    """
    if competition is None:
        return clip(0.4 * demand_signal + 0.3 * feasibility + 1.8)
    return clip(
        0.4 * demand_signal + 0.3 * feasibility + 0.3 * (11.0 - competition)
    )


def competition_effective(competition: Optional[float]) -> float:
    """The competition value actually used in the formula (R29)."""
    return IMPUTED_COMPETITION if competition is None else competition
