"""Scoring for every eval suite: ask_route, business_query, document_rag, parsing, sql.

Three modules are shared across suites and sit at this level: dataset (what each
eval kind includes), run_stats (interval estimates), failure_store (G4 capture).

Only ``__init__`` and ``dataset`` ship in the production image -- the ingest job
imports ``dataset``. The suite subpackages are excluded in .dockerignore.
"""
