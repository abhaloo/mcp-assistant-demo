@props([
    'rows' => 4,
])

@php
    $rows = max(1, (int) $rows);
@endphp

<div {{ $attributes->class('mcp-skeleton-deflist') }} aria-hidden="true">
    @for ($row = 0; $row < $rows; $row++)
        <x-mcp.skeleton.line class="mcp-skeleton-deflist__label" width="w75" />
        <x-mcp.skeleton.line class="mcp-skeleton-deflist__value" :width="$row % 2 === 0 ? 'w75' : 'w50'" />
    @endfor
</div>
