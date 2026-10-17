<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

/**
 * Untrusted `record_context` payload from the browser (Task B4 / R2, contract §
 * "Browser -> Billing (UNTRUSTED — identifiers only)"):
 *
 *   record_context:
 *     version: 1
 *     origin: global_search | explicit_selection
 *     query: bounded string
 *     references:
 *       - resource_type: quotation
 *         record_id: "1234"
 *
 * Client-provided labels, names, totals, statuses, URLs, permissions, entity IDs
 * are never part of this shape at all — only resource_type + record_id per
 * reference. RecordContextResolver reloads the user and re-authorizes every
 * reference; nothing here is trusted.
 *
 * Two different error philosophies, deliberately:
 *
 *  - TOP-LEVEL structural problems (unknown/missing keys, wrong types, an
 *    excessive reference count) throw InvalidRecordContextException -> 422.
 *    These make the payload impossible to interpret as a record_context AT ALL.
 *  - Individual REFERENCE entries that are malformed (wrong/missing keys, a
 *    non-numeric record_id) are silently DROPPED, not a whole-request 422 —
 *    "malformed" is deliberately indistinguishable from "unknown resource_type"
 *    (a semantic concern the resolver owns, not this parser) or "unauthorized"
 *    (task-B4-brief.md R2's "forbidden indistinguishable from missing", extended
 *    here so a client can never learn anything from why a reference vanished).
 */
final readonly class ClientRecordContext
{
    private const ALLOWED_KEYS = ['version', 'origin', 'query', 'references'];

    private const ALLOWED_ORIGINS = ['global_search', 'explicit_selection'];

    /** Mirrors RAG's binding records cap (task-B4-brief.md) — reject outright
     *  rather than silently truncating an excessive/abusive reference count. */
    public const MAX_REFERENCES = 20;

    public const MAX_QUERY_LENGTH = 200;

    /** Task B5 / R0(b): input hardening only — ->has() already short-circuits an
     *  unknown resource_type with no DB hit, so this just caps the untrusted
     *  string before it is even compared against the registry. */
    public const MAX_RESOURCE_TYPE_LENGTH = 64;

    /** BIGINT UNSIGNED's maximum is 20 digits; anything longer is rejected as
     *  malformed before the digit/positivity checks even run. */
    public const MAX_RECORD_ID_LENGTH = 20;

    /**
     * @param  list<ClientRecordReference>  $references
     */
    public function __construct(
        public int $version,
        public string $origin,
        public string $query,
        public array $references,
    ) {}

    /**
     * @param  array<string, mixed>  $payload
     *
     * @throws InvalidRecordContextException
     */
    public static function fromArray(array $payload): self
    {
        $unknown = array_diff(array_keys($payload), self::ALLOWED_KEYS);
        if ($unknown !== []) {
            throw new InvalidRecordContextException('Unknown record_context keys.');
        }

        foreach (self::ALLOWED_KEYS as $key) {
            if (! array_key_exists($key, $payload)) {
                throw new InvalidRecordContextException('Missing record_context key: '.$key);
            }
        }

        $version = self::parseVersion($payload['version']);
        $origin = self::parseOrigin($payload['origin']);
        $query = self::parseQuery($payload['query']);
        $references = self::parseReferences($payload['references']);

        return new self(
            version: $version,
            origin: $origin,
            query: $query,
            references: $references,
        );
    }

    /**
     * @throws InvalidRecordContextException
     */
    private static function parseVersion(mixed $raw): int
    {
        $isNumeric = is_int($raw) || (is_string($raw) && ctype_digit($raw));
        if (! $isNumeric || (int) $raw !== 1) {
            throw new InvalidRecordContextException('Invalid record_context version.');
        }

        return 1;
    }

    /**
     * @throws InvalidRecordContextException
     */
    private static function parseOrigin(mixed $raw): string
    {
        if (! is_string($raw) || ! in_array($raw, self::ALLOWED_ORIGINS, true)) {
            throw new InvalidRecordContextException('Invalid record_context origin.');
        }

        return $raw;
    }

    /**
     * @throws InvalidRecordContextException
     */
    private static function parseQuery(mixed $raw): string
    {
        if (! is_string($raw)) {
            throw new InvalidRecordContextException('Invalid record_context query.');
        }

        $query = trim($raw);
        if ($query === '' || mb_strlen($query) > self::MAX_QUERY_LENGTH) {
            throw new InvalidRecordContextException('Invalid record_context query length.');
        }

        return $query;
    }

    /**
     * @return list<ClientRecordReference>
     *
     * @throws InvalidRecordContextException
     */
    private static function parseReferences(mixed $raw): array
    {
        if (! is_array($raw) || ! array_is_list($raw)) {
            throw new InvalidRecordContextException('Invalid record_context references.');
        }

        if (count($raw) > self::MAX_REFERENCES) {
            throw new InvalidRecordContextException('Too many record_context references.');
        }

        $references = [];
        foreach ($raw as $entry) {
            $reference = self::parseReference($entry);
            if ($reference !== null) {
                $references[] = $reference;
            }
        }

        return $references;
    }

    /**
     * Malformed entries return null (dropped by the caller) rather than throwing —
     * see class docblock.
     */
    private static function parseReference(mixed $entry): ?ClientRecordReference
    {
        if (! is_array($entry)) {
            return null;
        }

        $unknown = array_diff(array_keys($entry), ['resource_type', 'record_id']);
        if ($unknown !== [] || ! array_key_exists('resource_type', $entry) || ! array_key_exists('record_id', $entry)) {
            return null;
        }

        $resourceType = $entry['resource_type'];
        if (! is_string($resourceType) || $resourceType === '' || strlen($resourceType) > self::MAX_RESOURCE_TYPE_LENGTH) {
            return null;
        }

        $recordId = self::normalizeRecordId($entry['record_id']);
        if ($recordId === null) {
            return null;
        }

        return new ClientRecordReference(resourceType: $resourceType, recordId: $recordId);
    }

    /**
     * Task B5 review F3 / B6 R0(b): MAX_RECORD_ID_LENGTH (20, BIGINT
     * UNSIGNED's own digit maximum) admits values that overflow PHP's signed
     * 64-bit PHP_INT_MAX (19 digits) — `(int) $normalized` on such a value
     * does not error, it silently SATURATES to PHP_INT_MAX, which used to be
     * returned as if it were the real id (a wrong-record miss with no error
     * signal, since re-authorization then just denies/allows the WRONG
     * record). Reject instead: a value that round-trips through `(string)
     * (int) $normalized` unchanged is safe to normalize; one that doesn't is
     * dropped like any other malformed reference (`? null` — see class
     * docblock's "malformed indistinguishable from unauthorized").
     */
    private static function normalizeRecordId(mixed $raw): ?string
    {
        if (! is_string($raw) && ! is_int($raw)) {
            return null;
        }

        $normalized = is_int($raw) ? (string) $raw : trim($raw);
        if ($normalized === '' || strlen($normalized) > self::MAX_RECORD_ID_LENGTH) {
            return null;
        }
        if (! ctype_digit($normalized) || (int) $normalized < 1) {
            return null;
        }

        $canonical = (string) (int) $normalized;
        if ($canonical !== $normalized) {
            return null;
        }

        return $canonical;
    }
}
