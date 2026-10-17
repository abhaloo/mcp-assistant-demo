<div class="position-relative" wire:click.away="closeDropdown" id="order-search-{{ $this->getId() }}">
    <label for="order-input-{{ $this->getId() }}" class="form-label">Customer Order <span class="text-danger">*</span></label>
    <div class="input-group input-group-sm">
        <input
            type="text"
            id="order-input-{{ $this->getId() }}"
            class="form-control"
            style="color: #212529 !important;"
            placeholder="Search by order number or customer name..."
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

    @if(!empty($query) && empty($selectedOrder))
        <div class="order-search-dropdown position-absolute w-100 bg-white border rounded shadow-sm" style="z-index: 1050; top: 100%; max-height: 280px; overflow-y: auto;">
            @if(!empty($orders))
                @foreach($orders as $i => $order)
                    <a href="#"
                        wire:click.prevent="selectOrder({{ $order['id'] }})"
                        wire:key="order-{{ $order['id'] }}"
                        class="order-search-item d-block px-3 py-2 text-decoration-none border-bottom"
                    >
                        <div class="d-flex justify-content-between align-items-center">
                            <span class="fw-semibold">Order #{{ $order['order_number'] }}</span>
                            <x-badge color="info" class="flex-shrink-0">
                                {{ $order['work_orders_count'] }} {{ Str::plural('job', $order['work_orders_count']) }}
                            </x-badge>
                        </div>
                        <div class="small text-muted mt-1">
                            <i class="fa fa-user me-1"></i> {{ $order['customer_name'] }}
                        </div>
                    </a>
                @endforeach
            @else
                <div class="px-3 py-2 text-muted small">
                    <i class="fa fa-exclamation-circle me-1"></i> No active orders found
                </div>
            @endif
        </div>
    @endif

    @once
    <style>
        .order-search-item {
            color: #212529 !important;
            background-color: #fff;
            transition: background-color 0.15s ease;
        }
        .order-search-item:hover {
            background-color: #e9ecef;
            color: #212529 !important;
        }
    </style>
    @endonce
</div>
