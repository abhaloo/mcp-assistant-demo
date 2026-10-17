@props([
    'priority' => false,
])

<div {{ $attributes->class([
    'mcp-mobile-card',
    'mcp-mobile-card--priority' => $priority,
]) }}>
    {{ $slot }}
</div>
