<?php

declare(strict_types=1);

namespace App\Rules;

use Closure;
use Illuminate\Contracts\Validation\ValidationRule;

/**
 * T-0.2: a correlation ID must be exactly 32 lowercase hex characters.
 *
 * All-zeros is rejected explicitly — a bare character-class regex accepts
 * it, and the browser's old generator (F27) could silently mint it.
 *
 * Anchored with \A…\z, not ^…$: in PCRE, `$` also matches immediately
 * before a trailing newline, so `/^[a-f0-9]{32}$/` would accept
 * "<32 hex chars>\n". (In this app's HTTP path, the global TrimStrings
 * middleware happens to strip a trailing newline before validation runs —
 * but that is incidental protection at a different layer, not a substitute
 * for a correctly anchored pattern here.)
 */
final class CorrelationId implements ValidationRule
{
    public function validate(string $attribute, mixed $value, Closure $fail): void
    {
        if (! is_string($value) || ! preg_match('/\A[a-f0-9]{32}\z/', $value)) {
            $fail('The :attribute must be exactly 32 lowercase hexadecimal characters.');

            return;
        }

        if ($value === str_repeat('0', 32)) {
            $fail('The :attribute must not be an all-zero value.');
        }
    }
}
