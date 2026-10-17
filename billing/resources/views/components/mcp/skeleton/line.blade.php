@props([
    'variant' => 'line',
    'width' => null,
])

@php
    $classes = [
        'mcp-skeleton',
        'mcp-skeleton--' . $variant,
    ];

    if ($width !== null && $width !== '') {
        $classes[] = 'mcp-skeleton--' . $width;
    }
@endphp

<span {{ $attributes->class($classes) }} aria-hidden="true"></span>
