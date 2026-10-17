"""
Immutable release/compatibility metadata (Ask AI context/access plan, Phase
2 task 2A, "RAG N/N-1 manifest-bundle compatibility + immutable release
metadata").

Exposes exactly four fields — CI/deployment verification evidence only, and
a CONTROLLER-PINNED cross-repo contract the sibling Billing release-gate
script consumes verbatim:

    {
      "record_access_schema_versions": [2],
      "record_context_schema_versions": [1],
      "accepted_manifest_hashes": ["sha256:...", ...],
      "bundle_set_id": "bset:<64 lowercase hex>"
    }

Two surfaces read this SAME document, byte-identical:
  - ``GET /capabilities`` (``app/health/router.py``, alongside ``/healthz``/
    ``/readyz`` — same unauthenticated, no-request-context pattern), and
  - ``python -m app.policy.compatibility --json`` (this module's ``__main__``).

Invariant 12 (binding, task-2A-brief.md): this metadata is deployment
evidence only. Nothing in request handling (``app/api/``, ``app/services/``,
``app/policy/record_executor.py``, ...) may read ANY function in this module
as a per-request downgrade or authorization oracle — grep confirms no such
import exists outside this module, ``app/health/router.py``, and this
module's own tests.

Schema-version sources (NOT hardcoded guesses — each mirrors the ``Literal``
a real validator actually enforces, cited so the two can never silently
drift apart):
  - ``record_access_schema_versions``: ``app/auth/record_access.py``'s
    ``RecordAccess.schema_version: Literal[2]`` — the ONLY v2 ``record_access``
    JWT claim shape ``app/auth/jwt.py`` accepts.
  - ``record_context_schema_versions``: ``app/models/record_context.py``'s
    ``RecordContext.version: Literal[1]`` — the ONLY ``record_context``
    request-body shape ``app/models/record_context.py`` accepts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

from app.policy.manifest_loader import accepted_manifest_hashes

# Mirrors app/auth/record_access.py's RecordAccess.schema_version: Literal[2]
# — the literal type IS the enforcement; this tuple is read-only metadata
# about that fact, never a second place schema-version acceptance is decided.
RECORD_ACCESS_SCHEMA_VERSIONS: tuple[int, ...] = (2,)

# Mirrors app/models/record_context.py's RecordContext.version: Literal[1].
RECORD_CONTEXT_SCHEMA_VERSIONS: tuple[int, ...] = (1,)


def compute_bundle_set_id(accepted_hashes: tuple[str, ...] | list[str]) -> str:
    """CONTROLLER-PINNED algorithm, identical on the Billing side: the
    SHA-256 hex digest of the UTF-8 string formed by joining the ``accepted``
    hash strings (exactly as they appear, e.g. ``sha256:abc...``) with a
    single comma, in ``index.json`` order. Emitted as lowercase hex, prefixed
    ``bset:`` -> ``bset:<64 hex chars>``."""
    joined = ",".join(accepted_hashes)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"bset:{digest}"


def build_compatibility_document() -> dict[str, object]:
    """The compatibility document as a plain dict — exactly the four
    controller-pinned keys, nothing else. No user, permission, record, or
    database data; ``accepted_manifest_hashes()`` is the ONLY manifest_loader
    call this module makes (never ``load_manifest()`` itself, which would
    pull in full resource/field definitions this endpoint must never leak)."""
    accepted = accepted_manifest_hashes()
    return {
        "record_access_schema_versions": list(RECORD_ACCESS_SCHEMA_VERSIONS),
        "record_context_schema_versions": list(RECORD_CONTEXT_SCHEMA_VERSIONS),
        "accepted_manifest_hashes": list(accepted),
        "bundle_set_id": compute_bundle_set_id(accepted),
    }


def render_compatibility_document() -> str:
    """The canonical JSON TEXT of ``build_compatibility_document()`` — the
    SAME serialization both ``/capabilities`` and ``python -m
    app.policy.compatibility --json`` emit as their response/stdout body, so
    the two surfaces are byte-identical (asserted directly by
    ``tests/api/test_capabilities.py``), not merely value-equal."""
    return json.dumps(build_compatibility_document(), sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.policy.compatibility",
        description="Print the immutable RAG release-compatibility document (deployment "
        "evidence only — never read this in request handling).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the compatibility document as JSON (the only supported output form)",
    )
    args = parser.parse_args(argv)
    if not args.as_json:
        parser.error("--json is required")
    print(render_compatibility_document())
    return 0


if __name__ == "__main__":
    sys.exit(main())
