<?php

declare(strict_types=1);

namespace Tests\Support\AskConcurrency;

/**
 * Result of one Ask concurrency wave.
 */
final class WaveSafetyReport
{
    /**
     * @param  list<StreamObservation>  $observations
     * @param  list<string>  $failures
     */
    public function __construct(
        private readonly array $observations,
        private readonly array $failures,
    ) {}

    public function passed(): bool
    {
        return $this->failures === [];
    }

    public function failureSummary(): string
    {
        if ($this->failures === []) {
            return 'ok';
        }

        return implode('; ', $this->failures);
    }

    /**
     * @return list<StreamObservation>
     */
    public function observations(): array
    {
        return $this->observations;
    }
}
