"""Record detail adapters, one module per resource domain.

Each module owns tenant-isolated, permission-gated reads for a single
business resource (customer, order, invoice, quotation, inventory,
planned materials).
"""

from __future__ import annotations
