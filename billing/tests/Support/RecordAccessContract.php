<?php

declare(strict_types=1);

namespace Tests\Support;

use Firebase\JWT\JWT;

/**
 * Shared v2 `record_access` claim-shape fixture builder (Task B3 / R4).
 *
 * Single source for the binding v2 contract shape (auditable plan §6.2,
 * task-B3-brief.md) so tests build from here instead of inline-constructing
 * claim arrays in more than one place — mirrors this repo's own
 * multi-lens-review discipline (ADR 0030/0031 in the RAG repo) and the RAG
 * repo's own `tests/contracts.py::v2_record_access()` (created in Task A2,
 * read READ-ONLY at
 * mcp-rag-qa-ask-uplift\tests\contracts.py — confirmed no drift from §6.2 as
 * of this task; see the Task B3 report for the cross-repo comparison).
 *
 * If the wire contract changes, this file and the RAG repo's
 * tests/contracts.py both need updating — keep them in sync.
 */
final class RecordAccessContract
{
    /**
     * A structurally valid v2 `record_access` claim body. Pass overrides to
     * replace individual top-level keys (e.g. a full replacement for
     * `scope_values` or `resources`).
     *
     * @param  array<string, mixed>  $overrides
     * @return array<string, mixed>
     */
    public static function example(array $overrides = []): array
    {
        return array_replace([
            'schema_version' => 2,
            'manifest_hash' => 'sha256:'.str_repeat('a', 64),
            'entity_id' => 1,
            'cross_entity' => false,
            'document_tiers' => ['all', 'sales'],
            'scope_values' => ['department_id' => 4],
            'resources' => [
                'quotation' => [
                    'actions' => ['search', 'read', 'link'],
                    'field_sets' => ['summary', 'detail'],
                ],
                'product' => [
                    'actions' => ['search', 'read'],
                    'field_sets' => ['summary'],
                ],
            ],
        ], $overrides);
    }

    /**
     * The exact top-level key set §6.2 requires — no more, no fewer.
     *
     * @return list<string>
     */
    public static function expectedKeys(): array
    {
        return [
            'schema_version', 'manifest_hash', 'entity_id', 'cross_entity',
            'document_tiers', 'scope_values', 'resources',
        ];
    }

    /**
     * Decode a signed JWT's payload segment down to the RAW JSON STRING
     * `Firebase\JWT\JWT::encode()` actually produced — not a PHP array/object
     * re-decoded from it (Critical review fix, Task B3: `json_decode(...,
     * true)` collapses a JSON object `{}` and a JSON array `[]` to the same
     * PHP `[]`, hiding exactly the wire-shape distinction this fix depends
     * on). This is the single source both AiAccessPolicySnapshotTest and
     * RagClientTest use to prove the `record_access.resources` wire shape at
     * the actual JSON level RAG's strict validator receives, not just at the
     * PHP-array level.
     *
     * @param  string  $jwt  A `header.payload.signature` compact JWT, e.g. from RagClient::mintToken().
     */
    public static function rawJsonPayload(string $jwt): string
    {
        $segments = explode('.', $jwt);

        return JWT::urlsafeB64Decode($segments[1]);
    }
}
