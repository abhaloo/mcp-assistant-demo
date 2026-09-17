"""Merge a staged fragment into the frozen prod Ask E2E suite.

A fragment is authored while `evals/prod_ask_e2e/suite.json` is frozen for
another plan. It carries new cases plus the additions the suite needs around
them (dom_contract keys, release gates, coverage gaps, one revision-history
line, and the oracle SQL to append to tools/derive_oracles.sql). Merging is
refused unless the suite is at the exact prior version the fragment names,
so two plans cannot both claim the same bump.

Usage:
    python scripts/eval/merge_staged_suite.py \
        --fragment evals/prod_ask_e2e/staged/1.18.0-derived-sets.json --write

Without --write the merged suite is validated and summarised only.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
_SUITE_DIR = _REPO / "evals" / "prod_ask_e2e"
SUITE_PATH = _SUITE_DIR / "suite.json"
ORACLE_SQL_PATH = _SUITE_DIR / "tools" / "derive_oracles.sql"


def merge(suite: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    """Return a new suite with the fragment applied; never mutates the inputs."""
    prior = fragment["requires_prior_version"]
    if suite["suite_version"] != prior:
        raise ValueError(
            f"fragment requires_prior_version {prior}, suite is {suite['suite_version']}"
        )
    existing = {c["id"] for c in suite["cases"]}
    incoming = [c["id"] for c in fragment["cases"]]
    duplicates = sorted(existing & set(incoming))
    if duplicates:
        raise ValueError(f"case ids already in suite: {duplicates}")
    overlap = sorted(set(suite["dom_contract"]) & set(fragment["dom_contract_additions"]))
    if overlap:
        raise ValueError(f"dom_contract keys already in suite: {overlap}")

    merged = copy.deepcopy(suite)
    merged["suite_version"] = fragment["target_suite_version"]
    merged["revision_history"].append(fragment["revision_history_entry"])
    merged["dom_contract"].update(fragment["dom_contract_additions"])
    merged["cases"].extend(copy.deepcopy(fragment["cases"]))
    merged["release_gates"].extend(fragment["release_gates_additions"])
    merged["coverage_gaps"]["gaps"].extend(fragment["coverage_gaps_additions"])
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fragment", type=Path, required=True)
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    parser.add_argument("--oracle-sql", type=Path, default=ORACLE_SQL_PATH)
    parser.add_argument("--write", action="store_true", help="write suite.json and append the SQL")
    args = parser.parse_args()

    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    fragment = json.loads(args.fragment.read_text(encoding="utf-8"))
    merged = merge(suite, fragment)
    ids = [c["id"] for c in fragment["cases"]]
    print(
        f"{suite['suite_version']} -> {merged['suite_version']}: "
        f"+{len(ids)} cases {ids}, {len(merged['cases'])} total"
    )
    if not args.write:
        print("dry run; pass --write to apply")
        return
    args.suite.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    with args.oracle_sql.open("a", encoding="utf-8") as handle:
        handle.write("\n" + fragment["oracle_sql_addendum"].rstrip() + "\n")
    print(f"wrote {args.suite} and appended oracle SQL to {args.oracle_sql}")


if __name__ == "__main__":
    main()
