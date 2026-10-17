@props([
    'variant' => 'secondary',
    'size' => null,
    'icon' => null,
    'href' => null,
    'type' => 'button',
])

@php
    $classes = collect([
        'mcp-btn',
        'mcp-btn--'.$variant,
        $size ? 'mcp-btn--'.$size : null,
    ])->filter()->implode(' ');
@endphp

@if ($href)
    <a href="{{ $href }}" {{ $attributes->merge(['class' => $classes]) }}>
        @if ($icon) <i class="fa {{ $icon }}"></i> @endif
        {{ $slot }}
    </a>
@else
    <button type="{{ $type }}" {{ $attributes->merge(['class' => $classes]) }}>
        @if ($icon) <i class="fa {{ $icon }}"></i> @endif
        {{ $slot }}
    </button>
@endif
