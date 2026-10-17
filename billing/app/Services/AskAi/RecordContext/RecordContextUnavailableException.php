<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

use RuntimeException;

/**
 * Task 1B: thrown when a syntactically valid, enabled `record_context` was
 * accepted but ZERO references survived resolution/authorization —
 * `RecordContextResolver::resolve()` returned null. Mapped to a generic 409
 * by AiSearchController: `error: "record_context_unavailable"`.
 *
 * Deliberately generic — the SAME response is used whether the record was
 * stale, missing, dropped as malformed, or unauthorized (Invariant 6:
 * forbidden, missing, and no-longer-readable records stay indistinguishable
 * to the user). This is the explicit-context counterpart to
 * StalePageContextException; unlike that one, it never falls back to
 * page_context or any other ambient context (Invariant 1, Invariant 11) —
 * the controller returns before calling RagClient::stream() or the JSON
 * fallback at all.
 */
final class RecordContextUnavailableException extends RuntimeException {}
