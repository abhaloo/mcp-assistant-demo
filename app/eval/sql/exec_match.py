"""Execution-match scoring for text-to-SQL evaluation.

Compares two result sets (agent vs gold) the way the Spider test-suite eval does:
bag (multiset) semantics by default; row order enforced ONLY when the gold query
has ORDER BY. Adds the float / NULL / type / money-tolerance handling Spider lacks
(our schema is decimal-heavy and the gold queries round money inconsistently).
Pure functions, no DB.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import permutations

_MAX_PERMUTE_COLS = 7

# Money tolerance. The gold queries are INCONSISTENT about ROUND(...,0) — some wrap a
# money total in ROUND(...,0), some don't — so a correct unrounded answer (e.g.
# 5_640_231.47) must still match a whole-number gold (5_640_231). ROUND-to-0 discards at
# most 0.5, so a half-unit absolute floor is the exact width needed to forgive that, and
# nothing wider. Integer-valued cells (row COUNTs) bypass the floor and compare exactly,
# so an off-by-one count still fails, and the "tax off the wrong table" bug — which moves
# a total by thousands — still fails. For true 2-dp business fidelity the cleaner fix is
# making the gold queries consistently ROUND(...,2); until then 0.5 is the floor the golds
# force, and for this decimal-heavy TZS data sub-unit precision is immaterial.
_MONEY_ABS_TOL = 0.5


def order_matters(gold_sql: str) -> bool:
    """ORDER BY in the gold query means row order is part of correctness."""
    return "order by" in gold_sql.lower()


def _is_num(v) -> bool:
    """True for real numerics — bool is excluded (it subclasses int)."""
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _cells_close(a, b) -> bool:
    """One-cell equality: numerics within the money tolerance, everything else exact."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if _is_num(a) and _is_num(b):
        fa, fb = float(a), float(b)
        if fa.is_integer() and fb.is_integer():
            return fa == fb  # counts / whole values compare exactly (off-by-one fails)
        return abs(fa - fb) <= _MONEY_ABS_TOL  # money: forgive the gold's ROUND(...,0)
    if _is_num(a) or _is_num(b):
        return False  # a number never equals a non-number
    return str(a) == str(b)


def _rows_close(r1, r2) -> bool:
    return len(r1) == len(r2) and all(_cells_close(a, b) for a, b in zip(r1, r2))


def _match(a_rows, g_rows, ordered: bool) -> bool:
    """Tolerant row-set equality. Result sets here are tiny, so the unordered O(n^2)
    greedy multiset match is fine and avoids hashing near-equal floats."""
    if ordered:
        return all(_rows_close(a, g) for a, g in zip(a_rows, g_rows))
    remaining = list(g_rows)
    for ar in a_rows:
        for i, gr in enumerate(remaining):
            if _rows_close(ar, gr):
                remaining.pop(i)
                break
        else:
            return False
    return not remaining


def compare(agent_rows, gold_rows, gold_sql, ordered=None) -> dict:
    """Return {'match': bool, 'reason': str}. agent_rows=None => no query produced."""
    if agent_rows is None:
        return {"match": False, "reason": "no_agent_query"}

    is_ordered = order_matters(gold_sql) if ordered is None else bool(ordered)

    if len(agent_rows) != len(gold_rows):
        return {"match": False, "reason": "row_count_mismatch"}

    if _match(agent_rows, gold_rows, is_ordered):
        return {"match": True, "reason": "ordered_equal" if is_ordered else "bag_equal"}

    ncols = len(gold_rows[0]) if gold_rows else 0
    if gold_rows and agent_rows and len(agent_rows[0]) == ncols and ncols <= _MAX_PERMUTE_COLS:
        for perm in permutations(range(ncols)):
            a_perm = [tuple(row[i] for i in perm) for row in agent_rows]
            if _match(a_perm, gold_rows, is_ordered):
                return {"match": True, "reason": "permuted_equal"}

    # Superset tolerance: the agent may answer with MORE columns than the question strictly
    # needs (e.g. "which customer" -> (order_no, name) vs gold (name,)). A user is satisfied as
    # long as every gold column appears with matching values on matching rows. Row COUNT stays
    # strict (the len check above already rejects extra/missing rows).
    a_ncols = len(agent_rows[0]) if agent_rows else 0
    if gold_rows and a_ncols > ncols and a_ncols <= _MAX_PERMUTE_COLS:
        for combo in permutations(range(a_ncols), ncols):
            a_sub = [tuple(row[i] for i in combo) for row in agent_rows]
            if _match(a_sub, gold_rows, is_ordered):
                return {"match": True, "reason": "superset_equal"}

    return {"match": False, "reason": "value_mismatch"}
