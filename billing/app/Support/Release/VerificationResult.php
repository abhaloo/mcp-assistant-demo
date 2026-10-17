<?php

declare(strict_types=1);

namespace App\Support\Release;

/**
 * Task 2B — the verdict a release-verification gate returns: pure data, no
 * behavior beyond shaping the exit code / wire form. `reasons` is empty iff
 * `passed` is true — every failure is named, never a bare boolean.
 */
final readonly class VerificationResult
{
    /**
     * @param  list<string>  $reasons
     */
    public function __construct(
        public bool $passed,
        public array $reasons = [],
    ) {}

    public function exitCode(): int
    {
        return $this->passed ? 0 : 1;
    }

    /** @return array{passed: bool, reasons: list<string>} */
    public function toArray(): array
    {
        return [
            'passed' => $this->passed,
            'reasons' => $this->reasons,
        ];
    }
}
