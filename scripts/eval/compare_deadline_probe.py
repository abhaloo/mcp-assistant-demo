"""Compare three-arm deadline probe outputs into score and spend tables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


_REASON_BUCKETS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("clock mismatch",), "clock_unproven"),
    (("budget_exceeded",), "budget_exceeded"),
    (("price_source_missing",), "price_source_missing"),
    (("wall_abort", "arm wall"), "wall_abort"),
    (("timeouterror", "deadlineexpired", "deadline_exceeded"), "timeout_like"),
)


def _reason_bucket(reason: str | None) -> str | None:
    if not reason:
        return None
    lowered = reason.lower()
    for needles, bucket in _REASON_BUCKETS:
        if any(needle in lowered for needle in needles):
            return bucket
    return None


def _arm_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    trajectory_pass = sum(1 for row in rows if row.get("trajectory") == "pass")
    latencies = [int(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    p50 = sorted(latencies)[len(latencies) // 2] if latencies else None
    buckets = {
        "timeout_like": 0,
        "clock_unproven": 0,
        "budget_exceeded": 0,
        "price_source_missing": 0,
        "wall_abort": 0,
    }
    for row in rows:
        bucket = _reason_bucket(row.get("reason"))
        if bucket in buckets:
            buckets[bucket] += 1
    return {
        "count": total,
        "trajectory_pass_rate": trajectory_pass / total if total else 0.0,
        "p50_latency_ms": p50,
        "unproven": total - trajectory_pass,
        **buckets,
    }


def compare(
    current_dir: Path,
    plus50_dir: Path,
    unbounded_dir: Path,
    spend_ledger: Path | None = None,
) -> dict[str, Any]:
    arms = {
        "current": current_dir,
        "plus50": plus50_dir,
        "unbounded": unbounded_dir,
    }
    arm_rows = {name: _load_results(path / "results.jsonl") for name, path in arms.items()}
    arm_summary = {name: _load_summary(path / "summary.json") for name, path in arms.items()}
    metrics = {name: _arm_metrics(rows) for name, rows in arm_rows.items()}

    all_metrics = {
        "count": sum(metrics[name]["count"] for name in metrics),
        "current": metrics["current"],
        "plus50": metrics["plus50"],
        "unbounded": metrics["unbounded"],
    }
    comparison = {
        "strata": {"all": all_metrics},
        "timeout_like": {name: metrics[name]["timeout_like"] for name in metrics},
        "clock_unproven": {name: metrics[name]["clock_unproven"] for name in metrics},
        "budget_exceeded": {name: metrics[name]["budget_exceeded"] for name in metrics},
        "price_source_missing": {name: metrics[name]["price_source_missing"] for name in metrics},
        "wall_abort": {name: metrics[name]["wall_abort"] for name in metrics},
        "spent_usd": {name: arm_summary[name].get("spent_usd") for name in metrics},
        "budget_exceeded_count": {
            name: arm_summary[name].get("budget_exceeded_count", metrics[name]["budget_exceeded"])
            for name in metrics
        },
        "capture_status": {name: arm_summary[name].get("capture_status") for name in metrics},
    }
    if spend_ledger is not None and spend_ledger.is_file():
        comparison["campaign_ledger"] = json.loads(spend_ledger.read_text(encoding="utf-8"))
    return comparison


def _markdown_table(comparison: dict[str, Any]) -> str:
    all_row = comparison["strata"]["all"]
    lines = [
        "| Arm | n | traj pass | p50 ms | timeout-like | "
        "clock-unproven | budget_exceeded | spent_usd |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ("current", "plus50", "unbounded"):
        metrics = all_row[arm]
        lines.append(
            f"| {arm} | {metrics['count']} | {metrics['trajectory_pass_rate']:.2f} | "
            f"{metrics['p50_latency_ms']} | {comparison['timeout_like'][arm]} | "
            f"{comparison['clock_unproven'][arm]} | {comparison['budget_exceeded'][arm]} | "
            f"{comparison['spent_usd'][arm]} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare deadline probe arm outputs.")
    parser.add_argument("--current", required=True)
    parser.add_argument("--plus50", required=True)
    parser.add_argument("--unbounded", required=True)
    parser.add_argument("--spend-ledger", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    spend_ledger = Path(args.spend_ledger) if args.spend_ledger.strip() else None
    comparison = compare(
        Path(args.current),
        Path(args.plus50),
        Path(args.unbounded),
        spend_ledger=spend_ledger,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    markdown = _markdown_table(comparison)
    (out_path.parent / "comparison.md").write_text(markdown, encoding="utf-8")
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
