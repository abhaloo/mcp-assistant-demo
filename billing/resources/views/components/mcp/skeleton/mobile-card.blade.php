<div {{ $attributes->class('mcp-skeleton-mobile-card') }} aria-hidden="true">
    <div class="mcp-skeleton-mobile-card__head">
        <x-mcp.skeleton.line variant="badge" />
        <x-mcp.skeleton.line variant="circle" />
    </div>

    <div class="mcp-skeleton-mobile-card__title">
        <x-mcp.skeleton.line variant="title" width="w75" />
    </div>

    <div class="mcp-skeleton-mobile-card__meta">
        <x-mcp.skeleton.line width="w50" />
        <x-mcp.skeleton.line width="w25" />
    </div>

    <div class="mcp-skeleton-mobile-card__amounts">
        <x-mcp.skeleton.line width="w25" />
        <x-mcp.skeleton.line width="w25" />
    </div>

    <div class="mcp-skeleton-mobile-card__actions">
        <x-mcp.skeleton.line variant="btn" />
        <x-mcp.skeleton.line variant="btn" />
    </div>
</div>
