@props([
    'label',                 // plain text — escaped
    'value',                 // plain text/number — escaped
    'icon' => null,          // fa-* class, e.g. "fa-receipt"
    'text' => false,         // true when value is a string, not a number
    'accent' => null,        // info | violet | warning — left-border tint
])

@php
    $classes = 'mcp-stat';
    if ($accent) {
        $classes .= ' mcp-stat--accent-' . $accent;
    }
@endphp

<div {{ $attributes->merge(['class' => $classes]) }}>
    <div class="mcp-stat__label">
        @if($icon)<i class="fa {{ $icon }}"></i>@endif
        {{ $label }}
    </div>
    <div @class(['mcp-stat__value', 'mcp-stat__value--text' => $text])>{{ $value }}</div>
    @isset($meta)
        <div class="mcp-stat__meta">{{ $meta }}</div>
    @endisset
</div>
