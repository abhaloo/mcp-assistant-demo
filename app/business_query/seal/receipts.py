"""Receipt generation and query metadata extraction for Business Query adapters."""

from __future__ import annotations

import hashlib
from typing import Any

from app.business_query.journaling.canonical_json import canonical_json_bytes
from app.business_query.outcomes import RecordRef

RECORD_REF_CUSTOMER_ID = "__bq_record_ref_customer_id"
RECORD_REF_INVOICE_ID = "__bq_record_ref_invoice_id"
RECORD_LABEL_PREFIX = "__bq_record_label_"
RESERVED_SIDECAR_COLUMNS = frozenset(
    {"__bq_total_row_count", RECORD_REF_CUSTOMER_ID, RECORD_REF_INVOICE_ID}
)


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _clean_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


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
        label_keys = [k for k in list(row.keys()) if k.startswith(RECORD_LABEL_PREFIX)]
        labels: dict[str, str | None] = {}
        for key in label_keys:
            value = row.pop(key, None)
            resource = key.removeprefix(RECORD_LABEL_PREFIX)
            labels[resource] = _clean_label(value)

        customer_id = row.pop(RECORD_REF_CUSTOMER_ID, None)
        if customer_id is not None:
            cust_label = row.get("invoice.customer_name")
            if cust_label is None:
                cust_label = labels.get("customer")
            refs.append(
                RecordRef(
                    resource="customer",
                    record_id=int(customer_id),
                    label=cust_label,
                    row_index=row_index,
                )
            )
        invoice_id = row.pop(RECORD_REF_INVOICE_ID, None)
        if invoice_id is not None:
            refs.append(
                RecordRef(
                    resource="invoice",
                    record_id=int(invoice_id),
                    label=labels.get("invoice"),
                    row_index=row_index,
                )
            )
        sidecar_keys = [k for k in list(row.keys()) if k.startswith("__bq_record_ref_")]
        for key in sorted(sidecar_keys):
            value = row.pop(key, None)
            if value is not None:
                resource = key.removeprefix("__bq_record_ref_").removesuffix("_id")
                refs.append(
                    RecordRef(
                        resource=resource,
                        record_id=int(value),
                        label=labels.get(resource),
                        row_index=row_index,
                    )
                )
    return tuple(refs)
