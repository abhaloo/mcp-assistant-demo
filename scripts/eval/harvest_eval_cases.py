"""Offline LangSmith harvest: turn recent production traces into an eval dataset.

This is a one-off operator script (run by hand, not in CI). It pulls the last
24h of root runs from the configured LangSmith project and converts them into a
LangSmith test dataset of question/answer examples.

`build_run_filter` is kept PURE (no I/O, no Client) so it can be unit-tested
without a live LangSmith API key or network access. `main` does all the I/O.
"""

from datetime import UTC, datetime, timedelta

from langsmith import Client
from langsmith.beta import convert_runs_to_test

from app.config import settings


def build_run_filter(since_hours: int) -> str:
    """Build a LangSmith trace-query filter bounded to the last `since_hours`.

    Returns a filter string of the form:
        and(gt(start_time, "<iso>"), lt(end_time, "<iso>"))

    Times are computed at call time (not import time) so each invocation reflects
    "now" rather than whenever the module was first imported.
    """
    now = datetime.now(UTC)
    since = now - timedelta(hours=since_hours)
    return f'and(gt(start_time, "{since.isoformat()}"), lt(end_time, "{now.isoformat()}"))'


def main() -> None:
    client = Client()

    runs = list(
        client.list_runs(
            project_name=settings.langsmith_project,
            is_root=True,
            filter=build_run_filter(24),
        )
    )

    convert_runs_to_test(
        runs,
        dataset_name=f"{settings.langsmith_project}-harvested-eval",
        # include_outputs=False: the model's own answer must NOT become reference
        # ground truth — grading an answer against itself is circular.
        include_outputs=False,
        # load_child_runs=False: keep each example to the top-level question/answer
        # pair; we don't want retrieval/sub-step runs as separate examples.
        load_child_runs=False,
    )


if __name__ == "__main__":
    main()
