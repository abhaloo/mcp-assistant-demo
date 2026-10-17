@props([
    'kind' => 'neutral',
    'label',
    'size' => null,
    'noDot' => false,
])

@php
    // Mirrors the colour aliases used by BadgeHelper::render() (Unit 0.5).
    // Keep this map in sync with BadgeHelper::COLOR_MAP.
    $resolvedKind = match ($kind) {
        'cyan'      => 'info',
        'secondary' => 'neutral',
        'info', 'success', 'warning', 'danger', 'neutral', 'sky', 'violet', 'primary' => $kind,
        default     => 'neutral',
    };
@endphp

<span @class([
    'mcp-status',
    'mcp-status--'.$resolvedKind,
    'mcp-status--sm' => $size === 'sm',
    'mcp-status--no-dot' => $noDot,
])>{{ $label }}</span>
