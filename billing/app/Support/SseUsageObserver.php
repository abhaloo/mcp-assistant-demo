<?php

declare(strict_types=1);

namespace App\Support;

/**
 * Stateful observer for a Server-Sent Events (SSE) stream.
 *
 * Ported from AiSearchController's private observeSseBuffer()/isHeartbeatOnly()
 * so the SSE wire-contract observation logic can be unit-tested in isolation.
 *
 * Wire contract (see docs/superpowers/specs/2026-06-12-global-search-rag-integration-design.md §7.2):
 *   : ping                                    ← heartbeat comment
 *   event: token / data: {"d":"<delta>"}      ← answer text deltas
 *   event: sources / data: [...]              ← citation sources (not observed here)
 *   event: done / data: {"model","usage":{...}}
 *   event: error / data: {"error","detail"}
 *
 * Feed raw chunks via feed(); frames may be split arbitrarily across chunks and
 * are reassembled internally before parsing.
 */
final class SseUsageObserver
{
    private string $buffer = '';

    private string $outcome = 'incomplete';

    /** @var array<string, mixed>|null */
    private ?array $usage = null;

    private ?string $model = null;

    private int $answerChars = 0;

    /**
     * Append a raw chunk to the internal buffer and parse every complete
     * "\n\n"-delimited frame, leaving any partial trailing frame buffered.
     */
    public function feed(string $chunk): void
    {
        $this->buffer .= $chunk;

        while (($pos = strpos($this->buffer, "\n\n")) !== false) {
            $eventBlock = substr($this->buffer, 0, $pos);
            $this->buffer = substr($this->buffer, $pos + 2);

            $this->processFrame($eventBlock);
        }
    }

    /**
     * Stream outcome: 'incomplete' (initial), 'done', 'stopped', or 'error:<code>'.
     */
    public function outcome(): string
    {
        return $this->outcome;
    }

    /**
     * Usage array from the done event, or null when absent.
     *
     * @return array<string, mixed>|null
     */
    public function usage(): ?array
    {
        return $this->usage;
    }

    /**
     * Model name from the done event, or null when absent.
     */
    public function model(): ?string
    {
        return $this->model;
    }

    /**
     * Running sum of mb_strlen() across every token delta 'd' seen so far.
     */
    public function answerChars(): int
    {
        return $this->answerChars;
    }

    /**
     * Mark the stream as stopped (used when the browser disconnects mid-stream).
     * A terminal outcome stands: a disconnect in the same chunk as the done
     * frame still delivered a full, billable answer.
     */
    public function markStopped(): void
    {
        if ($this->outcome !== 'incomplete') {
            return;
        }
        $this->outcome = 'stopped';
    }

    /**
     * True for an empty chunk or one whose trimmed content is a comment (':...').
     */
    public static function isHeartbeatOnly(string $chunk): bool
    {
        $trimmed = trim($chunk);

        return $trimmed === '' || str_starts_with($trimmed, ':');
    }

    /**
     * Parse a single complete SSE frame and mutate observer state accordingly.
     */
    private function processFrame(string $eventBlock): void
    {
        if (str_starts_with(trim($eventBlock), ':')) {
            return;
        }

        $eventName = null;
        $dataLines = [];

        foreach (explode("\n", $eventBlock) as $line) {
            if (str_starts_with($line, 'event:')) {
                $eventName = trim(substr($line, 6));
            } elseif (str_starts_with($line, 'data:')) {
                $dataLines[] = trim(substr($line, 5));
            }
        }

        if ($eventName === null || $dataLines === []) {
            return;
        }

        $payload = implode("\n", $dataLines);
        $decoded = json_decode($payload, true);

        if ($eventName === 'token' && is_array($decoded) && isset($decoded['d'])) {
            $this->answerChars += mb_strlen((string) $decoded['d']);
        }

        if ($eventName === 'done' && is_array($decoded)) {
            $this->outcome = 'done';
            $this->model = isset($decoded['model']) ? (string) $decoded['model'] : null;
            $this->usage = is_array($decoded['usage'] ?? null) ? $decoded['usage'] : null;
        }

        if ($eventName === 'error') {
            $this->outcome = 'error:'.(is_array($decoded) ? ($decoded['error'] ?? 'unknown') : 'unknown');
        }
    }
}
