<?php

declare(strict_types=1);

namespace App\Helpers;

class DurationHelper
{
    /**
     * Compact label used by the dashboard production-attention sections.
     * Mirrors the thresholds previously inlined in home.blade.php:
     * <24h → "Nh", <1w → "Nd Nh", <~1mo → "Nw Nd", else "N mo".
     */
    public static function compact(int $hours): string
    {
        if ($hours < 24) {
            return $hours.'h';
        }

        if ($hours < 168) {
            return (int) floor($hours / 24).'d '.($hours % 24).'h';
        }

        if ($hours < 730) {
            return (int) floor($hours / 168).'w '.(int) floor(($hours % 168) / 24).'d';
        }

        return (int) floor($hours / 730).' mo';
    }

    /** Long label used by Ready for Pickup: "N hours" / "N days". */
    public static function long(int $hours): string
    {
        return $hours < 24
            ? $hours.' hours'
            : (int) floor($hours / 24).' days';
    }
}
