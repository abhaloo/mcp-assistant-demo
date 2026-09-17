"""Receipt generation and query metadata extraction for Business Query adapters."""

from __future__ import annotations

import hashlib
from typing import Any

from app.business_query.journaling.canonical_json import canonical_json_bytes
from app.business_query.outcomes import RecordRef

RECORD_REF_CUSTOMER_ID = "__bq_record_ref_customer_id"
RECORD_REF_INVOICE_ID = "__bq_record_ref_invoice_id"
RESERVED_SIDECAR_COLUMNS = frozenset(
    {"__bq_total_row_count", RECORD_REF_CUSTOMER_ID, RECORD_REF_INVOICE_ID}
)


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def split_record_refs(rows: list[dict[str, Any]]) -> tuple[RecordRef, ...]:
    """Pop the reserved bindable-id sidecar column(s) out of every row and
    mint RecordRef entries from them (ADR 0053 Decision 4).

    Runs BEFORE declared_result_members, which raises "executor returned
    an undeclared result member" on any key it does not recognize — the
    sidecar columns must be gone from every row by the time that check
    runs, not just row 0.
    """
    refs: list[RecordRef] = []
    for row_index, row in enumerate(rows):
        customer_id = row.pop(RECORD_REF_CUSTOMER_ID, None)
        if customer_id is not None:
            refs.append(
                RecordRef(
                    resource="customer",
                    record_id=int(customer_id),
                    label=row.get("invoice.customer_name"),
                    row_index=row_index,
                )
            )
        invoice_id = row.pop(RECORD_REF_INVOICE_ID, None)
        if invoice_id is not None:
            refs.append(
                RecordRef(
                    resource="invoice",
                    record_id=int(invoice_id),
                    row_index=row_index,
                )
            )
    return tuple(refs)
