@props([
    'icon' => 'fa-filter',
    'active' => false,
    'badge' => null,
    'showChevron' => true,
])

<button type="button" @class([
    'mcp-filter-btn',
    'mcp-filter-btn--active' => $active,
]) {{ $attributes }}>
    <i class="fa {{ $icon }}"></i>
    {{ $slot }}
    @if ($active && $badge)
        <span class="mcp-filter-btn__badge">{{ $badge }}</span>
    @endif
    @if ($showChevron)
        <i class="fa fa-chevron-down mcp-icon--xxs"></i>
    @endif
</button>
