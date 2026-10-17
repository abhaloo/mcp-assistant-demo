<?php

declare(strict_types=1);

namespace Tests\Support\AskConcurrency;

/**
 * One client's observed Ask stream for a concurrency wave role.
 */
final class StreamObservation
{
    /**
     * @param  list<string>  $namedEvents
     */
    public function __construct(
        public readonly string $role,
        public readonly string $principalKey,
        public readonly int $userId,
        public readonly string $clientRunId,
        public readonly int $httpStatus,
        public readonly ?string $threadId,
        public readonly ?string $exchangeId,
        public readonly ?string $doneRunId,
        public readonly bool $sawToken,
        public readonly bool $sawDone,
        public readonly bool $sawTerminalError,
        public readonly bool $hangSuspected,
        public readonly array $namedEvents,
        public readonly float $elapsedSeconds,
    ) {}

    public function reachedTerminal(): bool
    {
        return $this->sawDone || $this->sawTerminalError;
    }
}
