<?php

declare(strict_types=1);

namespace App\Support;

use InvalidArgumentException;

final class AskAiClock
{
    public static function multiplier(): float
    {
        $value = (float) config('ask_ai.budget_multiplier', 1);

        if ($value <= 0) {
            throw new InvalidArgumentException('ask_ai.budget_multiplier must be greater than 0');
        }

        return $value;
    }

    public static function scaleMs(int $baseMs): int
    {
        return (int) round($baseMs * self::multiplier());
    }

    public static function scaleSeconds(int $baseSeconds): int
    {
        return (int) round($baseSeconds * self::multiplier());
    }
}
