@props([
    'icon',
    'variant' => null,
    'href' => null,
    'title' => null,
    'label' => null,
    'type' => 'button',
])

@php
    $classes = collect([
        'mcp-icon-btn',
        $variant ? 'mcp-icon-btn--'.$variant : null,
        $label ? 'mcp-icon-btn--with-label' : null,
    ])->filter()->implode(' ');
@endphp

@if ($href)
    <a href="{{ $href }}" @if ($title) title="{{ $title }}" @endif {{ $attributes->merge(['class' => $classes]) }}>
        <i class="fa {{ $icon }}"></i>
        @if ($label)<span class="mcp-icon-btn__label">{{ $label }}</span>@endif
    </a>
@else
    <button type="{{ $type }}" @if ($title) title="{{ $title }}" @endif {{ $attributes->merge(['class' => $classes]) }}>
        <i class="fa {{ $icon }}"></i>
        @if ($label)<span class="mcp-icon-btn__label">{{ $label }}</span>@endif
    </button>
@endif
