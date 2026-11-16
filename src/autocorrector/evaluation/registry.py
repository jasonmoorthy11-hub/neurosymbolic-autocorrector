"""Named evaluation datasets with the scope label each report must carry."""

from __future__ import annotations

from autocorrector.evaluation.dataset import GOLDEN_DATASET, GoldenDataset

DATASETS: dict[str, tuple[GoldenDataset, str]] = {
    "golden": (
        GOLDEN_DATASET,
        "self-authored, in-sample: cases written alongside the code and used during development",
    ),
}

# The blind set is in the repository but not in the wheel (pyproject.toml excludes
# it), so an installed package offers the golden set only.
try:
    from autocorrector.evaluation.heldout import HELDOUT_DATASET
except ModuleNotFoundError as missing:
    if missing.name != "autocorrector.evaluation.heldout":
        raise
else:
    DATASETS["heldout"] = (
        HELDOUT_DATASET,
        "self-authored, held-out: written blind from the policy text and committed "
        "before its first run",
    )


def select_dataset(name: str) -> tuple[GoldenDataset, str]:
    """Return (dataset, scope label) for a registered name; KeyError if unknown."""
    return DATASETS[name]
