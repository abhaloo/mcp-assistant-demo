<div
    class="position-relative"
    id="searchable-dropdown-{{ $this->getId() }}"
    x-data="{ open: @entangle('isOpen').live }"
    @click.away="open = false"
>
    {{-- Label --}}
    @if($label)
        <label for="search-input-{{ $this->getId() }}" class="form-label mb-1">
            {{ $label }} @if($required)<span class="text-danger">*</span>@endif
        </label>
    @endif

    {{-- Search Input with Dropdown Toggle --}}
    <div class="input-group input-group-sm">
        @if($selectedItem)
            <input
                type="text"
                class="form-control bg-light"
                value="{{ $selectedItem['secondary'] ? $selectedItem['display'] . ' (' . $selectedItem['secondary'] . ')' : $selectedItem['display'] }}"
                readonly
            />
            <button type="button" class="btn btn-outline-secondary" wire:click="clearSelection" title="Clear selection">
                <i class="fa fa-times"></i>
            </button>
        @else
            <input
                type="text"
                id="search-input-{{ $this->getId() }}"
                class="form-control"
                placeholder="{{ $placeholder }}"
                wire:model.live.debounce.300ms="query"
                @focus="if (!open) { open = true; $wire.openDropdown() }"
                @click.stop
                autocomplete="off"
            />
            <button
                type="button"
                class="btn btn-outline-secondary"
                @click.stop="open = !open; if (open) { $wire.openDropdown() }"
                title="Show options"
            >
                <i class="fa fa-chevron-down" :class="{ 'fa-rotate-180': open }" style="transition: transform 0.2s;"></i>
            </button>
        @endif
    </div>

    {{-- Dropdown Results (only render if no selection) --}}
    @if(!$selectedItem)
        {{-- Loading State --}}
        <div wire:loading wire:target="query, openDropdown" class="position-absolute w-100 text-muted small py-2 px-3 bg-light border" style="z-index: 1050; top: 100%;">
            <i class="fa fa-spinner fa-spin me-1"></i> Loading...
        </div>

        <div
            x-show="open"
            x-cloak
            x-transition:enter="transition ease-out duration-100"
            x-transition:enter-start="opacity-0 transform scale-95"
            x-transition:enter-end="opacity-100 transform scale-100"
            x-transition:leave="transition ease-in duration-75"
            x-transition:leave-start="opacity-100 transform scale-100"
            x-transition:leave-end="opacity-0 transform scale-95"
            class="searchable-dropdown-results position-absolute bg-white border rounded shadow-sm"
            style="z-index: 1050; top: 100%; min-width: 100%; width: max-content; max-width: 400px; max-height: 320px; overflow-y: auto;"
            @click.stop
        >
            @if(!empty($items))
                @foreach($items as $item)
                    <a href="#"
                        wire:click.prevent="selectItem({{ $item['id'] }})"
                        wire:key="item-{{ $item['id'] }}"
                        class="searchable-dropdown-item d-flex justify-content-between align-items-center px-3 py-2 text-decoration-none border-bottom"
                    >
                        <span class="me-3">{{ $item['display'] }}</span>
                        @if($item['secondary'])
                            <x-badge color="secondary" class="flex-shrink-0">
                                {{ $item['secondary'] }}
                            </x-badge>
                        @endif
                    </a>
                @endforeach

                @if(count($items) >= $limit)
                    <div class="px-3 py-2 text-muted small bg-light">
                        <i class="fa fa-info-circle me-1"></i> Type to search for more...
                    </div>
                @endif
            @else
                <div class="px-3 py-2 text-muted small">
                    @if(!empty($query))
                        <i class="fa fa-exclamation-circle me-1"></i> No results for "{{ $query }}"
                    @else
                        <i class="fa fa-info-circle me-1"></i> No options available
                    @endif
                </div>
            @endif
        </div>
    @endif
</div>

@once
<style>
    [x-cloak] { display: none !important; }
    .searchable-dropdown-item {
        color: #212529 !important;
        background-color: #fff;
        transition: background-color 0.15s ease;
    }
    .searchable-dropdown-item:hover {
        background-color: #e9ecef;
        color: #212529 !important;
    }
</style>
@endonce
