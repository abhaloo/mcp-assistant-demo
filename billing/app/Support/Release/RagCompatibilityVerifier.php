<?php

declare(strict_types=1);

namespace App\Support\Release;

/**
 * Task 2B / PR 2B — the release-verification gate judged against a candidate
 * Billing manifest hash and RAG's reported compatibility/deployment state.
 * Pure input -> verdict, no I/O of its own (see scripts/verify-rag-
 * compatibility.php for the CLI wrapper that gathers real inputs at release
 * time). This is deliberately NOT a `RagClient` capability cache and is
 * never consulted from request handling — Invariant 12: capability metadata
 * is deployment evidence, never a mutable per-request authority.
 *
 * Expected `$input` shape (assembled by the CALLER — `az containerapp
 * revision list`, curling the deployed `/capabilities`, and running
 * `python -m app.policy.compatibility --json` against the candidate image
 * are all impure gathering steps that happen OUTSIDE this class; tests drive
 * this class with recorded JSON fixtures instead, per task-2B-brief.md):
 *
 * ```
 * [
 *   'candidate_manifest_hash' => 'sha256:<hex>',           // the Billing hash being released
 *   'compatibility' => [                                    // python -m app.policy.compatibility --json shape
 *     'record_access_schema_versions' => [2],
 *     'record_context_schema_versions' => [1],
 *     'accepted_manifest_hashes' => ['sha256:<hex>', ...],  // at most two, current-then-previous
 *     'bundle_set_id' => 'bset:<hex>',                      // RAG's self-reported value — recomputed, not trusted
 *   ],
 *   'deployment' => [
 *     'expected_image_digest' => 'sha256:<hex>',            // whichever image THIS release action targets
 *     'revisions' => [                                       // az containerapp revision list, one entry per revision
 *       ['name' => '...', 'image_digest' => 'sha256:<hex>', 'traffic_percent' => 100, 'bundle_set_id' => 'bset:<hex>'],
 *       ...
 *     ],
 *   ],
 *   'capabilities_probe' => ['bundle_set_id' => 'bset:<hex>'], // live GET /capabilities on the deployed app
 * ]
 * ```
 *
 * Checks (every applicable one runs; failures accumulate rather than
 * short-circuiting, so a release operator sees everything wrong at once):
 *
 *  1. `candidate_manifest_hash` must be a member of `accepted_manifest_hashes`.
 *  2. `compatibility.bundle_set_id` must equal BundleSetId::compute() over its
 *     OWN `accepted_manifest_hashes` — defense against a bug in RAG's self-
 *     reported value, not just a pass-through trust. This recomputed value
 *     (not the self-reported one) is what every check below is judged against.
 *  3. The revision whose `image_digest` equals `expected_image_digest` must
 *     receive exactly 100% of traffic.
 *  4. No OTHER revision with nonzero traffic may report a different
 *     `bundle_set_id` than the recomputed one (a stuck/mixed rollout with two
 *     incompatible manifest sets both live).
 *  5. The live `capabilities_probe.bundle_set_id` must equal the recomputed
 *     one — the application-level check container-state-only rollback
 *     verification would miss.
 */
final class RagCompatibilityVerifier
{
    /**
     * @param  array<string, mixed>  $input
     */
    public static function evaluate(array $input): VerificationResult
    {
        $reasons = [];

        $candidateHash = (string) ($input['candidate_manifest_hash'] ?? '');
        $compatibility = is_array($input['compatibility'] ?? null) ? $input['compatibility'] : [];
        $acceptedHashes = array_map(
            static fn (mixed $hash): string => (string) $hash,
            is_array($compatibility['accepted_manifest_hashes'] ?? null) ? $compatibility['accepted_manifest_hashes'] : [],
        );
        $reportedBundleSetId = (string) ($compatibility['bundle_set_id'] ?? '');

        if (! in_array($candidateHash, $acceptedHashes, true)) {
            $reasons[] = "candidate manifest hash [{$candidateHash}] is not in RAG's accepted set [".
                implode(', ', $acceptedHashes).'].';
        }

        // Recompute, never trust the self-reported value blindly (same discipline
        // PolicyManifestExporter applies to its own hash()) — every downstream
        // check below is judged against THIS value, not compatibility.bundle_set_id.
        $expectedBundleSetId = BundleSetId::compute($acceptedHashes);

        if ($reportedBundleSetId !== $expectedBundleSetId) {
            $reasons[] = "compatibility bundle_set_id [{$reportedBundleSetId}] does not match the value ".
                "recomputed from its own accepted_manifest_hashes [{$expectedBundleSetId}].";
        }

        $deployment = is_array($input['deployment'] ?? null) ? $input['deployment'] : [];
        $expectedImageDigest = (string) ($deployment['expected_image_digest'] ?? '');
        $revisions = is_array($deployment['revisions'] ?? null) ? $deployment['revisions'] : [];

        // Security review finding (Task 2B): an empty/absent expected_image_digest
        // must fail closed, never vacuously "match" a revision whose own
        // image_digest is also empty/absent — that would let the 100%-traffic
        // and no-mixed-rollout checks pass without ever validating a real image.
        if ($expectedImageDigest === '') {
            $reasons[] = 'deployment.expected_image_digest is missing or empty.';
        }

        $isExpectedRevision = static function (mixed $revision) use ($expectedImageDigest): bool {
            if ($expectedImageDigest === '' || ! is_array($revision)) {
                return false;
            }

            $imageDigest = (string) ($revision['image_digest'] ?? '');

            return $imageDigest !== '' && $imageDigest === $expectedImageDigest;
        };

        $expectedRevision = null;
        foreach ($revisions as $revision) {
            if ($isExpectedRevision($revision)) {
                $expectedRevision = $revision;
                break;
            }
        }

        if ($expectedImageDigest !== '' && $expectedRevision === null) {
            $reasons[] = "expected image digest [{$expectedImageDigest}] was not found among the reported revisions.";
        } elseif ($expectedRevision !== null) {
            $expectedTraffic = (int) ($expectedRevision['traffic_percent'] ?? 0);
            if ($expectedTraffic !== 100) {
                $reasons[] = "the revision running the expected image digest [{$expectedImageDigest}] does not ".
                    "receive 100% of traffic (has {$expectedTraffic}%).";
            }
        }

        foreach ($revisions as $revision) {
            if (! is_array($revision) || $isExpectedRevision($revision)) {
                continue;
            }

            $traffic = (int) ($revision['traffic_percent'] ?? 0);
            if ($traffic <= 0) {
                continue;
            }

            $revisionBundleSetId = (string) ($revision['bundle_set_id'] ?? '');
            if ($revisionBundleSetId !== $expectedBundleSetId) {
                $name = (string) ($revision['name'] ?? 'unknown');
                $reasons[] = "older revision [{$name}] with a different bundle_set_id [{$revisionBundleSetId}] ".
                    "is receiving nonzero traffic ({$traffic}%).";
            }
        }

        $capabilitiesProbe = is_array($input['capabilities_probe'] ?? null) ? $input['capabilities_probe'] : [];
        $probedBundleSetId = (string) ($capabilitiesProbe['bundle_set_id'] ?? '');

        if ($probedBundleSetId !== $expectedBundleSetId) {
            $reasons[] = "deployed /capabilities bundle_set_id [{$probedBundleSetId}] does not match the ".
                "expected bundle_set_id [{$expectedBundleSetId}].";
        }

        return new VerificationResult($reasons === [], $reasons);
    }
}
