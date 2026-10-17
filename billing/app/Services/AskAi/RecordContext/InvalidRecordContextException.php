<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

use RuntimeException;

/**
 * Thrown for `record_context` request bodies that cannot be interpreted as the
 * untrusted contract AT ALL (unknown/missing top-level keys, wrong types, an
 * excessive reference count). Mapped to 422 by AiSearchController, mirroring
 * InvalidPageContextException's role for `page_context`.
 *
 * Individual malformed/unauthorized/unknown REFERENCES inside an otherwise
 * valid record_context are NOT this exception's concern — those are dropped
 * silently by RecordContextResolver (task-B4-brief.md R2: "forbidden
 * indistinguishable from missing — no error revealing existence").
 */
final class InvalidRecordContextException extends RuntimeException {}
