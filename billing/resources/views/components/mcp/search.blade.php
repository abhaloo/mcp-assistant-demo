@props([
    'wireModel' => 'search_keyword',
    'placeholder' => 'Search…',
    'size' => null,
])

<div @class([
    'mcp-search',
    'mcp-search--lg' => $size === 'lg',
])>
    <i class="fa fa-search mcp-search__icon"></i>
    <input type="text" wire:model.live="{{ $wireModel }}"
           class="mcp-search__input"
           placeholder="{{ $placeholder }}"
           {{ $attributes->except(['wire:model.live', 'placeholder']) }}>
</div>
