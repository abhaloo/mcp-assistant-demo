<?php

declare(strict_types=1);

namespace App\Support;

/**
 * Reads the turn_outcome frame a streamed v2 turn left behind.
 *
 * The proxy relays bytes it does not parse, but it keeps both the terminal
 * frame and every byte after the first terminal marker, so this reader can
 * recover the budget report after the stride reached the browser.
 */
final class AskV2OutcomeReader
{
    /**
     * The `budget` object of the tail's `turn_outcome` frame, or null.
     *
     * @return array<string, mixed>|null
     */
    public static function budgetFromTerminalTail(string $tail): ?array
    {
        $lines = explode("\n", $tail);
        $searching = false;

        foreach ($lines as $line) {
            $trimmed = trim($line);
            if ($trimmed === 'event: turn_outcome') {
                $searching = true;

                continue;
            }
            if (! $searching || str_starts_with($trimmed, ':')) {
                continue;
            }
            if (str_starts_with($trimmed, 'event:')) {
                $searching = false;

                continue;
            }
            if (! str_starts_with($trimmed, 'data:')) {
                continue;
            }

            $payload = json_decode(substr($trimmed, 5), true);

            if (is_array($payload) && is_array($payload['budget'] ?? null)) {
                return $payload['budget'];
            }

            $searching = false;
        }

        return null;
    }
}
