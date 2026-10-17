<?php

declare(strict_types=1);

namespace Tests\Support\AskConcurrency;

use InvalidArgumentException;
use RuntimeException;

/**
 * Frozen Ask concurrency oracle inputs (Success Contract fixture.json).
 */
final class AskConcurrencyFixture
{
    /**
     * @param  array<string, mixed>  $data
     */
    private function __construct(private readonly array $data) {}

    public static function fromPath(string $path): self
    {
        $allowedRoot = realpath(base_path('artifacts/ask-concurrency-e2e'));
        $resolved = realpath($path);
        $normalize = static fn (string $p): string => strtolower(str_replace('\\', '/', $p));
        if ($allowedRoot === false || $resolved === false
            || ! str_starts_with($normalize($resolved), $normalize($allowedRoot))) {
            throw new InvalidArgumentException("Ask concurrency fixture path must stay under artifacts/ask-concurrency-e2e: {$path}");
        }
        if (! is_file($resolved)) {
            throw new InvalidArgumentException("Ask concurrency fixture missing: {$path}");
        }

        $raw = file_get_contents($resolved);
        if ($raw === false) {
            throw new RuntimeException("Unable to read Ask concurrency fixture: {$resolved}");
        }

        $decoded = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        if (! is_array($decoded)) {
            throw new RuntimeException('Ask concurrency fixture must be a JSON object.');
        }

        return new self($decoded);
    }

    public function principalUsername(string $principalKey): string
    {
        $username = $this->data['principals'][$principalKey]['username'] ?? null;
        if (! is_string($username) || $username === '') {
            throw new InvalidArgumentException("Unknown principal: {$principalKey}");
        }

        return $username;
    }

    public function principalDepartmentId(string $principalKey): int
    {
        $departmentId = $this->data['principals'][$principalKey]['department_id'] ?? null;
        if (! is_int($departmentId) && ! (is_string($departmentId) && ctype_digit($departmentId))) {
            throw new InvalidArgumentException("Unknown principal department: {$principalKey}");
        }

        return (int) $departmentId;
    }

    public function promptText(string $promptKey): string
    {
        $text = $this->data['prompts'][$promptKey]['text'] ?? null;
        if (! is_string($text) || $text === '') {
            throw new InvalidArgumentException("Unknown prompt: {$promptKey}");
        }

        return $text;
    }

    /**
     * @return list<array{role: string, principal: string, prompt: string}>
     */
    public function assignments(): array
    {
        $rows = $this->data['wave']['assignment'] ?? null;
        if (! is_array($rows) || $rows === []) {
            throw new RuntimeException('fixture wave.assignment is required');
        }

        $out = [];
        foreach ($rows as $index => $row) {
            if (! is_array($row)) {
                throw new RuntimeException("fixture wave.assignment[{$index}] must be an object");
            }

            $role = $row['role'] ?? null;
            $principal = $row['principal'] ?? null;
            $prompt = $row['prompt'] ?? null;
            if (! is_string($role) || $role === ''
                || ! is_string($principal) || $principal === ''
                || ! is_string($prompt) || $prompt === '') {
                throw new RuntimeException("fixture wave.assignment[{$index}] requires non-empty role, principal, prompt");
            }

            // Resolve keys early so malformed maps fail closed here.
            $this->principalUsername($principal);
            $this->promptText($prompt);

            $out[] = [
                'role' => $role,
                'principal' => $principal,
                'prompt' => $prompt,
            ];
        }

        return $out;
    }

    public function threshold(string $key): mixed
    {
        if (! array_key_exists($key, $this->data['thresholds'] ?? [])) {
            throw new InvalidArgumentException("Unknown threshold: {$key}");
        }

        return $this->data['thresholds'][$key];
    }

    public function hangWindowSeconds(): int
    {
        return (int) $this->threshold('hang_only_heartbeat_or_silence_s');
    }

    public function stormMinStreams5xx(): int
    {
        return (int) $this->threshold('storm_min_streams_5xx');
    }

    public function proxyDeadlineSeconds(): int
    {
        return (int) $this->threshold('proxy_deadline_s');
    }
}
