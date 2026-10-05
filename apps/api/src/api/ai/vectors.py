"""Vector helpers shared by retrieval and clustering."""

from __future__ import annotations

import numpy as np


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalisation; zero rows pass through unchanged."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return matrix / norms
