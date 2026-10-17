<div {{ $attributes->class('mcp-skeleton-kpi') }} aria-hidden="true">
    <div class="mcp-skeleton-kpi__top">
        <x-mcp.skeleton.line width="w50" />
        <x-mcp.skeleton.line variant="circle" />
    </div>

    <x-mcp.skeleton.line variant="block" class="mcp-skeleton-kpi__value" width="w50" />

    <x-mcp.skeleton.line variant="block" class="mcp-skeleton-kpi__spark" />

    <div class="mcp-skeleton-kpi__foot">
        <x-mcp.skeleton.line variant="badge" />
        <x-mcp.skeleton.line width="w25" />
    </div>
</div>
