<?php

declare(strict_types=1);

namespace App\Helpers;

use Illuminate\Support\Facades\Log;

class BadgeHelper
{
    /**
     * Map BadgeHelper colour vocabulary -> MCP status kind.
     * Anything not listed here falls back to 'neutral' with a Log::warning so
     * unmapped colours surface during development. Keep in sync with the
     * `<x-mcp.status>` kind aliases.
     */
    private const COLOR_MAP = [
        'success'   => 'success',
        'danger'    => 'danger',
        'warning'   => 'warning',
        'info'      => 'info',
        'cyan'      => 'info',     // preserves "not green" intent for IN PROGRESS
        'sky'       => 'sky',      // PARTIAL PAID, INVOICED
        'violet'    => 'violet',   // SUBMITTED
        'primary'   => 'primary',  // FINISHED, READY — indigo brand accent
        'secondary' => 'neutral',
    ];

    private const FALLBACK = ['bg' => '#e2e8f0', 'text' => '#334155'];

    /**
     * Render an MCP status pill HTML string. Output matches <x-mcp.status>
     * markup so callsites still using `{!! $model->getStatus() !!}` pick up
     * the new design system without per-callsite edits.
     */
    public static function render(string $label, string $color, ?string $icon = null): string
    {
        $kind = self::COLOR_MAP[$color] ?? null;
        if ($kind === null) {
            Log::warning("BadgeHelper: unmapped colour '{$color}' for label '{$label}', falling back to neutral");
            $kind = 'neutral';
        }

        $iconHtml = $icon ? '<i class="fa ' . e($icon) . '"></i> ' : '';

        return '<span class="mcp-status mcp-status--' . $kind . '">'
            . $iconHtml . e($label) . '</span>';
    }

    /**
     * Get the resolved color pair for a palette key.
     *
     * Used by Department badges and the legacy <x-badge> view component, both
     * of which still need raw hex pairs (Department palette is runtime-driven
     * and not feasible to enumerate as static MCP classes).
     *
     * @return array{bg: string, text: string}
     */
    public static function palette(string $color): array
    {
        return config("badges.colors.{$color}") ?? config('badges.colors.secondary') ?? self::FALLBACK;
    }
}
