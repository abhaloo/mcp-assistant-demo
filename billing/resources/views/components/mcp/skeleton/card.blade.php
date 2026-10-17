@props([
    'flush' => false,
    'titleWidth' => 'w50',
])

<div {{ $attributes->class('mcp-skeleton-card') }} aria-hidden="true">
    <div class="mcp-skeleton-card__header">
        <x-mcp.skeleton.line variant="title" :width="$titleWidth" />
    </div>

    <div @class([
        'mcp-skeleton-card__body',
        'mcp-skeleton-card__body--flush' => $flush,
    ])>
        {{ $slot }}
    </div>
</div>
