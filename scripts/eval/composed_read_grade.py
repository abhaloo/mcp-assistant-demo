"""Evaluate and grade CQ1 composed read answers against an oracle.

This grader checks:
1. An invoice table exists (identified by invoice.invoice_number).
2. Its total_row_count equals the number of oracle rows.
3. Shown rows count is at most 50.
4. Shown invoice ids from envelope record_refs are distinct and within oracle ids.
5. Every shown created_at timestamp lies inside the oracle month.
6. Every shown currency equals TZS when a currency column is present.
7. A selection table exists whose first row matches oracle top_month and top_revenue.
8. unanswered_part is null.
"""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

INVOICE_KEY = "invoice.invoice_number"
CREATED_AT_KEY = "invoice.created_at"
REVENUE_KEY = "invoiced_revenue"
CURRENCY_KEYS = ("invoice.currency_code", "currency_code", "currency", "currencies.currency_code")
MAX_SHOWN_ROWS = 50
REVENUE_TOLERANCE = Decimal("0.01")
EXPECTED_CURRENCY = "TZS"


def read_tsv(path: Path) -> list[dict[str, str]]:
    """Read a tab-separated values file into a list of row dictionaries."""
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _extract_envelopes(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract result envelopes from run record or terminal frame."""
    if "envelopes" in rec and isinstance(rec["envelopes"], list):
        return rec["envelopes"]
    bq = rec.get("business_query") or rec.get("turn_outcome", {}).get("business_query")
    if isinstance(bq, dict):
        if "envelopes" in bq and isinstance(bq["envelopes"], list):
            return bq["envelopes"]
        if "envelope" in bq and isinstance(bq["envelope"], dict):
            return [bq["envelope"]]
    if "envelope" in rec and isinstance(rec["envelope"], dict):
        return [rec["envelope"]]
    return []


def _extract_unanswered_part(rec: dict[str, Any]) -> Any:
    """Extract unanswered_part from run record or terminal frame."""
    if "unanswered_part" in rec:
        return rec["unanswered_part"]
    return rec.get("turn_outcome", {}).get("unanswered_part")


def _is_invoice_table(table: dict[str, Any]) -> bool:
    """Check whether a table is the invoice list table."""
    rows = table.get("rows", [])
    if any(INVOICE_KEY in r for r in rows):
        return True
    cols = table.get("columns", [])
    return INVOICE_KEY in cols


def _is_selection_table(table: dict[str, Any]) -> bool:
    """Check whether a table is the time-group selection table."""
    rows = table.get("rows", [])
    cols = table.get("columns", [])
    has_invoice_num = any(INVOICE_KEY in r for r in rows) or (INVOICE_KEY in cols)
    if has_invoice_num:
        return False
    has_rev = any(REVENUE_KEY in r for r in rows) or (REVENUE_KEY in cols)
    has_month = any((CREATED_AT_KEY in r or "created_at" in r) for r in rows) or (
        CREATED_AT_KEY in cols or "created_at" in cols
    )
    return has_rev and has_month


def _find_invoice_record_refs(envelopes: list[dict[str, Any]]) -> list[Any]:
    """Find record_refs for the invoice list from result envelopes."""
    for env in envelopes:
        refs = env.get("record_refs", [])
        if any(
            (
                r.get("resource") == "invoice"
                if isinstance(r, dict)
                else getattr(r, "resource", None) == "invoice"
            )
            for r in refs
        ):
            return list(refs)
    for env in envelopes:
        refs = env.get("record_refs", [])
        if refs:
            return list(refs)
    return []


def _resolve_oracle_targets(
    oracle: list[dict[str, Any]],
    top_month: str | None,
    top_revenue: Decimal | str | float | None,
) -> tuple[str | None, Decimal | None, str | None]:
    """Extract expected month and revenue targets from oracle and arguments."""
    month = top_month
    if month is None:
        if oracle and "top_month" in oracle[0]:
            month = str(oracle[0]["top_month"])
        else:
            return None, None, "missing top_month in oracle and arguments"

    if top_revenue is None:
        if oracle and "top_revenue" in oracle[0]:
            revenue = Decimal(str(oracle[0]["top_revenue"]))
        else:
            return None, None, "missing top_revenue in oracle and arguments"
    else:
        revenue = Decimal(str(top_revenue))
    return month, revenue, None


def _check_outcome(rec: dict[str, Any]) -> str | None:
    """Validate terminal outcome type and unanswered_part state."""
    outcome_type = rec.get("outcome_type")
    if outcome_type is not None and outcome_type != "answered":
        return f"outcome={outcome_type} reason={rec.get('reason_code')}"
    unanswered_part = _extract_unanswered_part(rec)
    if unanswered_part is not None:
        return f"unanswered_part is not null: {unanswered_part!r}"
    return None


def _get_invoice_table(
    tables: list[dict[str, Any]],
    expected_rows: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Find and validate the invoice list table and its row counts."""
    invoice_tables = [t for t in tables if _is_invoice_table(t)]
    if not invoice_tables:
        return None, "no invoice table found"
    table = invoice_tables[0]
    total_rows = table.get("total_row_count")
    if total_rows != expected_rows:
        return None, f"total_row_count={total_rows}, want {expected_rows}"
    shown_rows = table.get("rows", [])
    if len(shown_rows) > MAX_SHOWN_ROWS:
        return None, f"shown rows {len(shown_rows)} > {MAX_SHOWN_ROWS}"
    return table, None


def _check_invoice_ids(
    shown_rows: list[dict[str, Any]],
    envelopes: list[dict[str, Any]],
    oracle: list[dict[str, Any]],
) -> tuple[list[str], str | None]:
    """Match shown rows to envelope record_refs and verify id uniqueness and bounds."""
    invoice_refs = _find_invoice_record_refs(envelopes)
    if len(shown_rows) > 0 and len(invoice_refs) < len(shown_rows):
        return [], f"mismatch: {len(shown_rows)} shown rows but {len(invoice_refs)} record_refs"

    shown_ids: list[str] = []
    for i in range(len(shown_rows)):
        ref = invoice_refs[i]
        rec_id = ref.get("record_id") if isinstance(ref, dict) else getattr(ref, "record_id", None)
        if rec_id is None:
            return [], f"record_ref at index {i} missing record_id"
        shown_ids.append(str(rec_id))

    if len(shown_ids) != len(set(shown_ids)):
        return [], f"duplicate invoice ids: {len(shown_ids) - len(set(shown_ids))} duplicates"

    oracle_ids = {str(row["id"]) for row in oracle if "id" in row}
    outside_ids = set(shown_ids) - oracle_ids
    if outside_ids:
        return [], f"{len(outside_ids)} invoice ids outside oracle ids"
    return shown_ids, None


def _check_shown_rows(shown_rows: list[dict[str, Any]], top_month: str) -> str | None:
    """Verify every shown date and currency. A list that shows no date column is checked
    through its ids alone: every id is one of the oracle's rows from the month."""
    for i, row in enumerate(shown_rows):
        date_key = next((k for k in (CREATED_AT_KEY, "created_at") if k in row), None)
        if date_key is not None:
            created_val = row[date_key]
            if not created_val:
                return f"shown row {i} has an empty created_at"
            if str(created_val)[:7] != top_month:
                return f"shown created_at '{created_val}' outside month {top_month}"

        for curr_key in CURRENCY_KEYS:
            if curr_key in row:
                curr_val = str(row[curr_key])
                if curr_val != EXPECTED_CURRENCY:
                    return f"currency is '{curr_val}', want '{EXPECTED_CURRENCY}'"
    return None


def _validate_selection_row(
    row: dict[str, Any],
    top_month: str,
    top_revenue: Decimal,
) -> str | None:
    """Validate that the selection row matches month and revenue targets."""
    first_month = str(row.get(CREATED_AT_KEY) or row.get("created_at") or "")[:7]
    if first_month != top_month:
        return f"selection month={first_month}, want {top_month}"

    first_rev_raw = row.get(REVENUE_KEY)
    if first_rev_raw is None:
        return "selection row missing invoiced_revenue"
    try:
        first_rev = Decimal(str(first_rev_raw))
    except (ArithmeticError, ValueError):
        return f"selection revenue '{first_rev_raw}' is not valid decimal"

    if abs(first_rev - top_revenue) > REVENUE_TOLERANCE:
        return f"selection revenue {first_rev} not within 0.01 of {top_revenue}"
    return None


def _check_selection_table(
    tables: list[dict[str, Any]],
    top_month: str,
    top_revenue: Decimal,
) -> str | None:
    """Find and validate the time-group selection table."""
    selection_tables = [t for t in tables if _is_selection_table(t)]
    if not selection_tables:
        return "no selection table found"
    sel_rows = selection_tables[0].get("rows", [])
    if not sel_rows:
        return "selection table has no rows"
    return _validate_selection_row(sel_rows[0], top_month, top_revenue)


def _check_run_preconditions(
    rec: dict[str, Any],
    oracle: list[dict[str, Any]],
    top_month: str | None,
    top_revenue: Decimal | str | float | None,
) -> tuple[str | None, Decimal | None, str | None]:
    """Validate oracle targets and turn outcome preconditions."""
    month, revenue, err = _resolve_oracle_targets(oracle, top_month, top_revenue)
    if err or month is None or revenue is None:
        return None, None, err or "invalid oracle targets"
    outcome_err = _check_outcome(rec)
    if outcome_err:
        return None, None, outcome_err
    return month, revenue, None


def grade_cq1(
    rec: dict[str, Any],
    oracle: list[dict[str, Any]],
    *,
    top_month: str | None = None,
    top_revenue: Decimal | str | float | None = None,
) -> tuple[bool, str]:
    """Grade a CQ1 run against oracle expectations.

    Returns (True, detail) on pass or (False, reason) on failure.
    """
    month, revenue, err = _check_run_preconditions(rec, oracle, top_month, top_revenue)
    if err or month is None or revenue is None:
        return False, err or "invalid oracle targets"

    tables = rec.get("tables", [])
    invoice_table, table_err = _get_invoice_table(tables, len(oracle))
    if table_err or invoice_table is None:
        return False, table_err or "no invoice table found"

    shown_rows = invoice_table.get("rows", [])
    envelopes = _extract_envelopes(rec)
    shown_ids, ids_err = _check_invoice_ids(shown_rows, envelopes, oracle)
    if ids_err:
        return False, ids_err

    rows_err = _check_shown_rows(shown_rows, month)
    if rows_err:
        return False, rows_err

    sel_err = _check_selection_table(tables, month, revenue)
    if sel_err:
        return False, sel_err

    return True, f"{month}, {len(shown_ids)} shown of {len(oracle)}"


def main(argv: list[str] | None = None) -> int:
    """Run CQ1 grader CLI over run files and compare with oracle."""
    ap = argparse.ArgumentParser(description="Grade CQ1 composed read evaluation runs.")
    ap.add_argument(
        "--runs", required=True, help="Directory containing .jsonl files or path to single file"
    )
    ap.add_argument(
        "--oracle-cq1", "--oracle", dest="oracle_cq1", required=True, help="Path to oracle TSV"
    )
    ap.add_argument(
        "--oracle-all-time", required=False, default=None, help="Ignored, for CLI compatibility"
    )
    args = ap.parse_args(argv)

    oracle = read_tsv(Path(args.oracle_cq1))
    if not oracle:
        print("FAIL\t\t\t\t\toracle file is empty")
        return 0

    top_month = oracle[0].get("top_month")
    top_rev_raw = oracle[0].get("top_revenue")
    top_revenue = Decimal(str(top_rev_raw)) if top_rev_raw is not None else None

    runs_path = Path(args.runs)
    files = sorted(runs_path.glob("*.jsonl")) if runs_path.is_dir() else [runs_path]

    for file_path in files:
        if not file_path.exists():
            continue
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            case = rec.get("case", "cq1")
            if case != "cq1":
                continue
            ok, detail = grade_cq1(rec, oracle, top_month=top_month, top_revenue=top_revenue)
            status = "PASS" if ok else "MISS"
            label = rec.get("label", "")
            rep = rec.get("rep", 1)
            run_id = rec.get("run_id", "")
            print(f"{status}\t{label}\t{case}\trep={rep}\trun={run_id}\t{detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
