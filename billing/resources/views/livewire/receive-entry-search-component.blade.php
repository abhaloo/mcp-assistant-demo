<div class="position-relative" wire:click.away="closeDropdown">
    {{-- Search Input --}}
    <div class="input-group input-group-sm">
        <span class="input-group-text bg-light">
            <i class="fa fa-search text-muted"></i>
        </span>
        <input
            type="text"
            class="form-control"
            placeholder="{{ $placeholder }}"
            wire:model.live.debounce.300ms="query"
            autocomplete="off"
        />
    </div>

    {{-- Loading State --}}
    <div wire:loading wire:target="query" class="position-absolute w-100 text-muted small py-2 px-3 bg-light border" style="z-index: 1050; top: 100%;">
        <i class="fa fa-spinner fa-spin me-1"></i> Searching...
    </div>

    {{-- Search Results Dropdown --}}
    @if(!empty($query) && strlen($query) >= 2)
        <div class="position-absolute w-100 bg-white border rounded shadow-sm" style="z-index: 1050; top: 100%; max-height: 350px; overflow-y: auto;">
            @if(!empty($results))
                @foreach($results as $entry)
                    <a href="#"
                        wire:click.prevent="selectEntry({{ $entry['id'] }})"
                        wire:key="entry-{{ $entry['id'] }}"
                        class="receive-entry-item d-block px-3 py-2 text-decoration-none border-bottom"
                    >
                        <div class="d-flex justify-content-between align-items-start">
                            <div>
                                <strong class="text-dark">Entry #{{ $entry['id'] }}</strong>
                                @if(!$supplierId)
                                    <x-badge color="light" class="ms-1 border">{{ $entry['supplier_name'] }}</x-badge>
                                @endif
                                <small class="text-muted d-block">
                                    {{ \Carbon\Carbon::parse($entry['inventory_date'])->format('d M Y') }}
                                    @if($entry['warehouse_name'])
                                        &bull; {{ $entry['warehouse_name'] }}
                                    @endif
                                    @if($entry['reference'])
                                        &bull; {{ $entry['reference'] }}
                                    @endif
                                </small>
                                @if($entry['description'])
                                    <small class="text-muted d-block text-truncate" style="max-width: 300px;">
                                        {{ $entry['description'] }}
                                    </small>
                                @endif
                            </div>
                            <x-badge color="info" class="flex-shrink-0 ms-2">
                                {{ $entry['items_count'] }} item(s)
                            </x-badge>
                        </div>
                    </a>
                @endforeach
            @else
                <div class="px-3 py-3 text-muted small text-center">
                    <i class="fa fa-exclamation-circle me-1"></i>
                    @if($supplierId)
                        No unlinked receive entries found for this supplier.
                    @else
                        No unlinked receive entries found matching "{{ $query }}".
                    @endif
                </div>
            @endif
        </div>
    @endif

    @once
    <style>
        .receive-entry-item {
            color: #212529 !important;
            background-color: #fff;
            transition: background-color 0.15s ease;
        }
        .receive-entry-item:hover {
            background-color: #e9ecef;
            color: #212529 !important;
        }
    </style>
    @endonce
</div>
