"""The error every experiment-harness refusal raises."""

from __future__ import annotations


class RagExperimentError(RuntimeError):
    """The experiment cannot produce trustworthy evidence."""
