<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

/**
 * THE canonical-serialization + digest recipe for `record_context` (Task B4 / R3-R4) —
 * Billing's mirror of RAG's `app/rag/auth/...`/`app/models/record_context.py::
 * canonical_json_bytes()`/`compute_record_context_digest()` (mcp-rag-qa-ask-uplift,
 * Task A3, read-only). MUST reproduce those functions byte-for-byte; verified against
 * the golden fixture (`tests/fixtures/record_context_wire.json`, mirrored from the RAG
 * worktree) by RecordContextDigestTest.
 *
 * Recipe parity, Python -> PHP:
 *
 *  - `sort_keys=True` (recursive) -> recursively `ksort()` every associative
 *    (string-keyed) array before encoding; PHP has no built-in recursive-ksort flag.
 *  - `separators=(",", ":")` -> PHP's json_encode already has no whitespace around
 *    `,`/`:` by default; no flag needed.
 *  - `ensure_ascii=False` -> `JSON_UNESCAPED_UNICODE`. Without it PHP \uXXXX-escapes
 *    non-ASCII by default, diverging from Python's literal UTF-8 output.
 *  - (not a json.dumps kwarg, but PHP-specific) PHP's json_encode ALSO escapes '/' as
 *    '\/' by default, which Python's json.dumps never does -> `JSON_UNESCAPED_SLASHES`
 *    is required too, or any value containing '/' (invoice refs like "2024/001", a
 *    link_key, a URL in a field value) digest-mismatches RAG's canonical bytes with
 *    both repos' test suites green (the exact silent-401 trap the fixture's
 *    slash_and_unicode_example exists to catch).
 *  - Empty JSON objects: an empty PHP array `[]` and an empty map are the same value
 *    in PHP, but `json_encode([])` produces the JSON ARRAY `[]`, not `{}` — the same
 *    wire-shape bug Task B3 fixed for AiAccessPolicy::snapshot()'s `resources` field.
 *    Callers building a `record_context` value (see TrustedRecordContextRecord) MUST
 *    cast an empty `fields` map to `new \stdClass` before it reaches this class; this
 *    class preserves \stdClass instances as opaque objects (json_encode({}) => "{}")
 *    rather than attempting to canonicalize/sort them.
 *  - Array (list) element order is preserved as-is — only object key NAMES are
 *    sorted, never list element order (`records` order matters).
 */
final class RecordContextDigest
{
    /**
     * The exact canonical UTF-8 byte sequence RAG hashes for a given `record_context`
     * value — the digest pre-image. Also the measurement RAG's total-bytes cap (16384)
     * uses, so build-time cap enforcement (RecordContextResolver) MUST use this same
     * function to stay in lockstep with RAG's own measurement.
     *
     * @param  array<array-key, mixed>  $data
     */
    public static function canonicalJsonBytes(array $data): string
    {
        return json_encode(
            self::canonicalize($data),
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
        );
    }

    /**
     * `"sha256:" + hex` of canonicalJsonBytes($data) — the exact value the JWT
     * `record_context_digest` claim must equal for a given `record_context` body.
     *
     * @param  array<array-key, mixed>  $data
     */
    public static function compute(array $data): string
    {
        return 'sha256:'.hash('sha256', self::canonicalJsonBytes($data));
    }

    /**
     * Recursively sorts associative (string-keyed) array keys; leaves list
     * (sequential int-keyed) arrays' element ORDER untouched. \stdClass instances
     * (deliberate {} markers — see class docblock) pass through unsorted/unchanged,
     * since an object built for this purpose is always empty.
     */
    private static function canonicalize(mixed $value): mixed
    {
        if ($value instanceof \stdClass) {
            return $value;
        }

        if (! is_array($value)) {
            return $value;
        }

        if (array_is_list($value)) {
            return array_map(self::canonicalize(...), $value);
        }

        ksort($value);

        return array_map(self::canonicalize(...), $value);
    }
}
