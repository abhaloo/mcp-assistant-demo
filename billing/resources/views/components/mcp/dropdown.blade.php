@props([
    'variant' => 'outlined',
    'icon' => null,
    'menuId',
    'menuLabel',
    'align' => 'start',
    'size' => 'xs',
])

@php
    $btnVariant = match ($variant) {
        'ghost' => 'ghost',
        'chip' => 'chip',
        default => 'secondary',
    };
    $sizeClass = $variant === 'chip' ? '' : ' mcp-btn--'.$size;
    $hasLabelSlot = isset($label);
    $labelSlot = $hasLabelSlot ? $label : $slot;
    $menuSlot = $menu ?? ($hasLabelSlot ? $slot : '');
@endphp

<div
    data-mcp-dropdown
    {{ $attributes->class([
        'mcp-dropdown',
        'mcp-dropdown--end' => $align === 'end',
    ]) }}
>
    <button type="button"
            class="mcp-btn mcp-btn--{{ $btnVariant }}{{ $sizeClass }} mcp-dropdown__trigger"
            data-mcp-dropdown-trigger
            aria-haspopup="menu"
            aria-expanded="false"
            aria-controls="{{ $menuId }}"
            aria-label="{{ $menuLabel }}">
        @if ($icon)
            <i class="fa {{ $icon }}" aria-hidden="true"></i>
        @endif
        <span class="mcp-dropdown__label">{{ $labelSlot }}</span>
        <i class="fa fa-chevron-down mcp-dropdown__caret" aria-hidden="true"></i>
    </button>
    <div id="{{ $menuId }}"
         class="mcp-dropdown__menu"
         role="menu"
         aria-label="{{ $menuLabel }}"
         hidden
         data-mcp-dropdown-menu>
        {{ $menuSlot }}
    </div>
</div>
