<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

/**
 * Binding wire caps (task-B4-brief.md, RAG's rejecting validator) — RagClient's
 * emission must never breach these, so RecordContextResolver enforces them at
 * build time (task-B4-brief.md R2: "Enforce the same caps at build time so
 * Billing never emits a rejectable context").
 *
 * Decision (R4): per-field/label/title/record_id overflow TRUNCATES, deterministically
 * (first N in caller-supplied order; mb_substr for over-length strings) — this class
 * never throws. RecordContextResolver's whole purpose is producing a context that is
 * ALWAYS acceptable to RAG, so silently narrowing a value is preferable to failing an
 * Ask AI request over a caller-side field-list length RecordContextResolver itself
 * controls (none of the ten registry resource types' readable_fields lists currently
 * exceed 10 — see BusinessRecordDefinition::readableFields() — so this path is a
 * defensive backstop, verified directly here rather than only implicitly).
 * `references` count (MAX_RECORDS) and the total-serialized-bytes cap are exceptions:
 * `references` count is rejected structurally by ClientRecordContext (a client
 * sending >20 is malformed input, not a Billing-side overflow to smooth over); total
 * bytes is enforced by RecordContextResolver dropping trailing records (see there),
 * not by this class.
 */
final class RecordContextCaps
{
    public const MAX_RECORDS = 20;

    public const MAX_FIELDS_PER_RECORD = 10;

    public const MAX_FIELD_KEY_LENGTH = 64;

    public const MAX_FIELD_VALUE_LENGTH = 256;

    public const MAX_LABEL_LENGTH = 128;

    public const MAX_TITLE_LENGTH = 256;

    public const MAX_RECORD_ID_LENGTH = 64;

    public const MAX_TOTAL_BYTES = 16384;

    /**
     * @param  list<array{0: string, 1: string}>  $orderedPairs  [key, value] pairs in
     *                                                           registry-declared order; caller has already skipped null/empty values.
     * @return array<string, string>
     */
    public static function boundedFields(array $orderedPairs): array
    {
        $fields = [];
        foreach ($orderedPairs as [$key, $value]) {
            if (count($fields) >= self::MAX_FIELDS_PER_RECORD) {
                break;
            }
            $fields[mb_substr($key, 0, self::MAX_FIELD_KEY_LENGTH)] = mb_substr($value, 0, self::MAX_FIELD_VALUE_LENGTH);
        }

        return $fields;
    }

    public static function boundedLabel(string $label): string
    {
        return mb_substr($label, 0, self::MAX_LABEL_LENGTH);
    }

    public static function boundedTitle(string $title): string
    {
        return mb_substr($title, 0, self::MAX_TITLE_LENGTH);
    }

    public static function boundedRecordId(string $recordId): string
    {
        return mb_substr($recordId, 0, self::MAX_RECORD_ID_LENGTH);
    }
}
