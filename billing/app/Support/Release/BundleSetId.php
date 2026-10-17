<?php

declare(strict_types=1);

namespace App\Support\Release;

/**
 * Task 2B — the CONTROLLER-PINNED `bundle_set_id` algorithm, implemented
 * IDENTICALLY on both sides of the shared RAG contract (task-2B-brief.md):
 * the SHA-256 hex digest of the UTF-8 string formed by joining the
 * `accepted` manifest-hash strings (exactly as they appear, e.g.
 * `sha256:abc...`) with a single comma, in `index.json` order. Emitted as
 * lowercase hex with prefix `bset:` -> `bset:<64 hex chars>`.
 *
 * Pure and deterministic by construction — no I/O, no config, order of the
 * input list is semantically significant (it mirrors RAG's `index.json`
 * `accepted` array order, current-then-previous), so callers must NOT sort
 * the list before passing it here.
 *
 * Used by RagCompatibilityVerifier to independently RECOMPUTE the
 * `bundle_set_id` a probed RAG `/capabilities` response (or
 * `python -m app.policy.compatibility --json` document) self-reports, rather
 * than trusting that self-reported value blindly — the same "recompute, don't
 * trust" discipline PolicyManifestExporter applies to its own hash().
 */
final class BundleSetId
{
    /**
     * @param  list<string>  $acceptedHashes  Accepted manifest hash strings, in
     *                                        index.json order (current, then previous).
     */
    public static function compute(array $acceptedHashes): string
    {
        return 'bset:'.hash('sha256', implode(',', $acceptedHashes));
    }
}
