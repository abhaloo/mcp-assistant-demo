<?php

declare(strict_types=1);

namespace Tests\Support\AskConcurrency;

/**
 * Typed SSE parse result for Track S judgments (ids + terminals only).
 */
final readonly class ParsedSse
{
    /**
     * @param  list<string>  $namedEvents
     */
    public function __construct(
        public array $namedEvents,
        public ?string $threadId,
        public ?string $exchangeId,
        public ?string $runId,
        public bool $sawToken,
        public bool $sawDone,
        public bool $sawTerminalError,
    ) {}
}
