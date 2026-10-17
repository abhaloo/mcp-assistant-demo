<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\User;
use Illuminate\Support\Facades\Cache;

final class AiCreditsService
{
    public function cacheKey(User $user): string
    {
        return 'ai-credits:'.$user->id.':'.now()->format('Y-m');
    }

    public function monthlyCap(): float
    {
        return (float) config('services.rag.credits.monthly_cap', 1.0);
    }

    public function currentUsage(User $user): float
    {
        return (float) Cache::get($this->cacheKey($user), 0.0);
    }

    public function capPercent(User $user): float
    {
        $cap = $this->monthlyCap();
        if ($cap <= 0) {
            return 0.0;
        }

        return min(100.0, round(($this->currentUsage($user) / $cap) * 100, 1));
    }

    public function isAtCap(User $user): bool
    {
        return $this->currentUsage($user) >= $this->monthlyCap();
    }

    /**
     * What the usage meter paints: how much of the month's allowance the person
     * has used, as a percentage, and the day it rolls over. No amount leaves
     * the server.
     *
     * @return array{pct: float, reset_date: string}
     */
    public function meterPayload(User $user): array
    {
        return [
            'pct' => $this->capPercent($user),
            'reset_date' => $this->resetDate(),
        ];
    }

    /** @return array{cap: float, used: float, reset_date: string, upgrade_url: string|null} */
    public function capPayload(User $user): array
    {
        return [
            'cap' => $this->monthlyCap(),
            'used' => $this->currentUsage($user),
            'reset_date' => $this->resetDate(),
            'upgrade_url' => config('services.rag.credits.upgrade_url'),
        ];
    }

    /** The first day of next month; anchored to this month's first day so a 31st never skips a month. */
    private function resetDate(): string
    {
        return now()->startOfMonth()->addMonth()->toDateString();
    }

    public function incrementFromUsage(User $user, ?array $usage, string $model, int $answerChars = 0): void
    {
        $inputTokens = (int) ($usage['input_tokens'] ?? 0);
        $outputTokens = (int) ($usage['output_tokens'] ?? 0);

        if ($inputTokens === 0 && $outputTokens === 0 && $answerChars > 0) {
            $outputTokens = (int) ceil($answerChars / 4);
        }

        $rates = config('services.rag.credits.model_rates', []);
        $rate = (float) ($rates[$model] ?? $rates['default'] ?? 0.000002);
        $cost = ($inputTokens + $outputTokens) * $rate;

        if ($cost <= 0) {
            return;
        }

        $this->incrementUsd($user, $cost);
    }

    /**
     * One write path for the monthly cap counter — the token estimate on v1
     * and RAG's priced figure on v2 both land here.
     */
    public function incrementUsd(User $user, float $usd): void
    {
        $key = $this->cacheKey($user);

        // Soft monthly cap: the pre-stream isAtCap() check and this post-stream charge
        // are not transactional (accepted for V1 — design spec §10). This lock only stops
        // concurrent charges for the same user from clobbering each other (lost updates).
        Cache::lock($key.':lock', 5)->block(3, function () use ($key, $usd): void {
            $current = (float) Cache::get($key, 0.0);
            Cache::put($key, $current + $usd, now()->endOfMonth());
        });
    }
}
