<?php

declare(strict_types=1);

namespace Tests\Support\AskConcurrency;

/**
 * Parse SSE bytes into id/terminal fields for Track S judgments.
 *
 * Hang detection is wall-clock and belongs on AskConcurrencyWaveRunner.
 */
final class SseStreamParser
{
    private const NON_STREAMABLE_QUERY_TYPE = 'non_streamable_query_type';

    public function parse(string $body): ParsedSse
    {
        $named = [];
        $threadId = null;
        $exchangeId = null;
        $runId = null;
        $sawToken = false;
        $sawDone = false;
        $sawTerminalError = false;
        $currentEvent = null;

        $lines = preg_split("/\r\n|\n|\r/", $body) ?: [];
        foreach ($lines as $line) {
            if (str_starts_with($line, ':')) {
                continue;
            }
            if (str_starts_with($line, 'event:')) {
                $currentEvent = trim(substr($line, strlen('event:')));
                if ($currentEvent !== '') {
                    $named[] = $currentEvent;
                }

                continue;
            }
            if (! str_starts_with($line, 'data:') || $currentEvent === null) {
                continue;
            }

            $payload = trim(substr($line, strlen('data:')));
            $decoded = json_decode($payload, true);
            if (! is_array($decoded)) {
                continue;
            }

            if ($currentEvent === 'token') {
                $sawToken = true;
            }
            if ($currentEvent === 'done') {
                $sawDone = true;
                $threadId = self::stringField($decoded, 'thread_id') ?? $threadId;
                $exchangeId = self::stringField($decoded, 'exchange_id') ?? $exchangeId;
                $runId = self::stringField($decoded, 'run_id') ?? $runId;
            }
            if ($currentEvent === 'error') {
                $error = $decoded['error'] ?? null;
                if ($error !== self::NON_STREAMABLE_QUERY_TYPE) {
                    $sawTerminalError = true;
                }
            }
        }

        return new ParsedSse(
            namedEvents: $named,
            threadId: $threadId,
            exchangeId: $exchangeId,
            runId: $runId,
            sawToken: $sawToken,
            sawDone: $sawDone,
            sawTerminalError: $sawTerminalError,
        );
    }

    /**
     * @param  array<string, mixed>  $decoded
     */
    private static function stringField(array $decoded, string $key): ?string
    {
        $value = $decoded[$key] ?? null;

        return is_string($value) ? $value : null;
    }
}
