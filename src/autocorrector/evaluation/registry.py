"""Named evaluation datasets with the scope label each report must carry."""

from __future__ import annotations

from autocorrector.evaluation.dataset import GOLDEN_DATASET, GoldenDataset
from autocorrector.evaluation.heldout import HELDOUT_DATASET

DATASETS: dict[str, tuple[GoldenDataset, str]] = {
    "golden": (
        GOLDEN_DATASET,
        "self-authored, in-sample: cases written alongside the code and used during development",
    ),
    "heldout": (
        HELDOUT_DATASET,
        "self-authored, held-out: written blind from the policy text and committed "
        "before its first run",
    ),
}


def select_dataset(name: str) -> tuple[GoldenDataset, str]:
    """Return (dataset, scope label) for a registered name; KeyError if unknown."""
    return DATASETS[name]
