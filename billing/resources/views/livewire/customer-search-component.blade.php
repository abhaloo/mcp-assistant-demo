<div class="position-relative" wire:click.away="closeDropdown" id="customer-search-{{ $this->getId() }}">
    <label for="customer-input-{{ $this->getId() }}" class="form-label">Customer <span class="text-danger">*</span></label>
    <div class="input-group input-group-sm">
        <input
            type="text"
            id="customer-input-{{ $this->getId() }}"
            class="form-control"
            style="color: #212529 !important;"
            placeholder="Search customer by name or phone..."
            wire:model.live="query"
            autocomplete="off"
        />
        <button type="button" class="btn btn-secondary">
            <i class="fa fa-search"></i>
        </button>
    </div>

    <div wire:loading wire:target="query" class="position-absolute w-100 text-muted small py-2 px-3 bg-light border" style="z-index: 1050; top: 100%;">
        <i class="fa fa-spinner fa-spin me-1"></i> Searching...
    </div>

    @if(!empty($query) && empty($selectedCustomer))
        <div class="customer-search-dropdown position-absolute w-100 bg-white border rounded shadow-sm" style="z-index: 1050; top: 100%; max-height: 280px; overflow-y: auto;">
            @if(!empty($customers))
                @foreach($customers as $i => $customer)
                    <a href="#"
                        wire:click.prevent="selectCustomer({{ $customer['id'] }})"
                        wire:key="customer-{{ $customer['id'] }}"
                        class="customer-search-item d-flex justify-content-between align-items-center px-3 py-2 text-decoration-none border-bottom"
                    >
                        <span class="text-truncate me-2">{{ $customer['name'] }}</span>
                        @if($customer['phone'])
                            <x-badge color="secondary" class="flex-shrink-0">
                                {{ $customer['phone'] }}
                            </x-badge>
                        @endif
                    </a>
                @endforeach
            @else
                <div class="px-3 py-2 text-muted small">
                    <i class="fa fa-exclamation-circle me-1"></i> No customers found
                </div>
            @endif
        </div>
    @endif

    @once
    <style>
        .customer-search-item {
            color: #212529 !important;
            background-color: #fff;
            transition: background-color 0.15s ease;
        }
        .customer-search-item:hover {
            background-color: #e9ecef;
            color: #212529 !important;
        }
    </style>
    @endonce
</div>

