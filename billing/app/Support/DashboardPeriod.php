<?php

declare(strict_types=1);

namespace App\Support;

use Carbon\Carbon;

final class DashboardPeriod
{
    public static function normalize(string $period): string
    {
        return in_array($period, ['week', 'month', 'quarter'], true) ? $period : 'month';
    }

    /**
     * Uniform to-date window: end is always end of today; only start is period-specific.
     *
     * @return array{0: Carbon, 1: Carbon}
     */
    public static function window(string $period): array
    {
        $period = self::normalize($period);

        $start = match ($period) {
            'week' => now()->startOfWeek(),
            'quarter' => now()->startOfQuarter(),
            default => now()->startOfMonth(),
        };

        return [$start, now()->endOfDay()];
    }

    /**
     * Same elapsed point one period ago (end of the equivalent "today").
     *
     * @return array{0: Carbon, 1: Carbon}
     */
    public static function previousWindow(string $period): array
    {
        $period = self::normalize($period);

        $start = match ($period) {
            'week' => now()->subWeek()->startOfWeek(),
            'quarter' => now()->subQuarter()->startOfQuarter(),
            default => now()->subMonth()->startOfMonth(),
        };

        $end = match ($period) {
            'week' => now()->subWeek()->endOfDay(),
            'quarter' => now()->subQuarter()->endOfDay(),
            default => now()->subMonth()->endOfDay(),
        };

        return [$start, $end];
    }

    public static function cacheKey(string $period, Carbon $start): string
    {
        $period = self::normalize($period);

        return match ($period) {
            'week' => $start->format('oW'),
            'quarter' => $start->format('Y').'Q'.$start->quarter,
            default => $start->format('Ym'),
        };
    }
}
