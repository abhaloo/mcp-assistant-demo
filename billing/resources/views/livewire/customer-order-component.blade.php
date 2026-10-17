<div>
    <div class="mcp mcp-page--orders">
        <x-mcp.page-header
            eyebrow="Operations"
            :subtitle="$orders->total().' total'">
            <x-slot:titleSlot>
                <span class="mcp-page-header__title-soft">Manage</span> Customer Orders
            </x-slot:titleSlot>
            <x-slot:actions>
                @can('add customer order')
                    <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                        wire:click="create"
                        data-coreui-toggle="modal" data-coreui-target="#modal-order">
                        New Order
                    </x-mcp.btn>
                @endcan
            </x-slot:actions>
        </x-mcp.page-header>

        <x-mcp.toolbar>
            <x-mcp.search size="lg" placeholder="Search by order number or customer" />

            {{-- Filter dropdown --}}
            <div class="dropdown">
                <x-mcp.filter-btn
                    :active="$filter_date_from || $filter_date_to || $filter_status"
                    badge="ON"
                    data-coreui-toggle="dropdown" data-coreui-auto-close="outside">
                    Filter
                </x-mcp.filter-btn>
                <div class="dropdown-menu p-3" style="min-width: 280px;">
                    <label class="form-label small fw-bold">Status</label>
                    <div class="mb-3">
                        <select wire:model.live="filter_status" class="form-select form-select-sm">
                            <option value="">All</option>
                            <option value="NEW">New</option>
                            <option value="IN_PROGRESS">In Progress</option>
                            <option value="FINISHED">Finished</option>
                            <option value="CANCELLED">Cancelled</option>
                        </select>
                    </div>
                    <label class="form-label small fw-bold">Date Range</label>
                    <div class="mb-2">
                        <label class="form-label small mb-1">From</label>
                        <input type="date" wire:model.live="filter_date_from" class="form-control form-control-sm">
                    </div>
                    <div class="mb-2">
                        <label class="form-label small mb-1">To</label>
                        <input type="date" wire:model.live="filter_date_to" class="form-control form-control-sm">
                    </div>
                    <div class="d-flex justify-content-end gap-2 mt-2">
                        @if($filter_date_from || $filter_date_to || $filter_status)
                            <button type="button" wire:click="clearFilters"
                                    class="btn btn-sm btn-outline-secondary">Clear</button>
                        @endif
                        <button type="button" wire:click="$refresh" class="btn btn-sm btn-primary">
                            <i class="fa fa-check"></i> Apply
                        </button>
                    </div>
                </div>
            </div>

            {{-- Sort dropdown --}}
            @php
                $sortBadge = match (true) {
                    !$sort_field => null,
                    $sort_field === 'created_at' && $sort_direction === 'desc' => 'Date Descending',
                    $sort_field === 'created_at' && $sort_direction === 'asc' => 'Date Ascending',
                    default => null,
                };
            @endphp
            <div class="dropdown">
                <x-mcp.filter-btn icon="fa-sort"
                    :active="(bool) $sort_field"
                    :badge="$sortBadge"
                    data-coreui-toggle="dropdown">
                    Sort
                </x-mcp.filter-btn>
                <div class="dropdown-menu">
                    <button class="dropdown-item {{ $sort_field === 'created_at' && $sort_direction === 'desc' ? 'active' : '' }}"
                            wire:click="setSort('created_at', 'desc')">
                        Date Descending
                    </button>
                    <button class="dropdown-item {{ $sort_field === 'created_at' && $sort_direction === 'asc' ? 'active' : '' }}"
                            wire:click="setSort('created_at', 'asc')">
                        Date Ascending
                    </button>
                    <div class="dropdown-divider"></div>
                    <button class="dropdown-item {{ !$sort_field ? 'active' : '' }}"
                            wire:click="clearSort">
                        None
                    </button>
                </div>
            </div>
        </x-mcp.toolbar>

        {{-- DESKTOP TABLE --}}
        <div class="d-none d-md-block">
            <x-mcp.card flush>
                <div class="mcp-table-wrap" wire:loading.class="mcp-skeleton-refreshing" wire:loading.attr="aria-busy">
                    <table class="mcp-table">
                        <thead>
                            <tr>
                                <th>Order #</th>
                                <th>Customer</th>
                                <th>Description</th>
                                <th>Jobs</th>
                                <th>Bills</th>
                                <th>Status</th>
                                <th>Created</th>
                                <th class="mcp-td--right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            @forelse($orders as $order)
                                <tr>
                                    <td>
                                        <a href="{{ route('customer-orders.show', $order) }}" class="mcp-id-link">
                                            #{{ $order->order_number }}
                                        </a>
                                    </td>
                                    <td class="mcp-cell-strong">
                                        <div class="mcp-row-flex">
                                            <x-mcp.avatar :name="$order->customer->name ?? ''" />
                                            <div class="mcp-truncate">{{ $order->customer->name ?? '-' }}</div>
                                        </div>
                                    </td>
                                    <td class="mcp-td--mute">
                                        <div class="mcp-truncate">{{ \Illuminate\Support\Str::limit($order->description, 60) }}</div>
                                    </td>
                                    <td>
                                        <x-mcp.status kind="neutral" size="sm" :label="$order->workOrders->count().' jobs'" no-dot />
                                    </td>
                                    <td>
                                        @if($order->bills->isEmpty())
                                            <x-mcp.status kind="warning" size="sm" label="No quotation" />
                                        @else
                                            <div class="mcp-bills-list">
                                                @foreach($order->bills->take(2) as $bill)
                                                    <x-mcp.bill-chip :bill="$bill" :href="auth()->user()?->canany(['view invoice', 'view quotation']) ? route('bill.show', $bill) : null" />
                                                @endforeach
                                                @if($order->bills->count() > 2)
                                                    <span class="mcp-bills-more">+{{ $order->bills->count() - 2 }}</span>
                                                @endif
                                            </div>
                                        @endif
                                    </td>
                                    <td>{!! $order->status_badge !!}</td>
                                    <td class="mcp-td--mono mcp-td--secondary">{{ $order->created_at->format('d M, y') }}</td>
                                    <td class="mcp-td--right">
                                        <div class="mcp-row-actions">
                                            <x-mcp.icon-btn variant="primary" icon="fa-eye" title="View" label="View"
                                                :href="route('customer-orders.show', $order)" />
                                            @can('delete customer order')
                                                <x-mcp.icon-btn variant="danger" icon="fa-trash" title="Delete" label="Delete"
                                                    wire:click="deleteConfirm({{ $order->id }})"
                                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalOrder" />
                                            @endcan
                                        </div>
                                    </td>
                                </tr>
                            @empty
                                <tr><td colspan="8">
                                    <x-mcp.empty title="No customer orders found"
                                        hint="Create an order to group jobs and billing for a customer.">
                                        @can('add customer order')
                                            <x-slot:action>
                                                <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                                                    wire:click="create"
                                                    data-coreui-toggle="modal" data-coreui-target="#modal-order">
                                                    New order
                                                </x-mcp.btn>
                                            </x-slot:action>
                                        @endcan
                                    </x-mcp.empty>
                                </td></tr>
                            @endforelse
                        </tbody>
                    </table>
                </div>
                {{ $orders->withQueryString()->links() }}
            </x-mcp.card>
        </div>

        {{-- MOBILE CARDS --}}
        <div class="d-md-none">
            @forelse($orders as $order)
                <x-mcp.mobile-card>
                    <div class="mcp-mobile-card__head">
                        <a href="{{ route('customer-orders.show', $order) }}" class="mcp-id-link">#{{ $order->order_number }}</a>
                        {!! $order->status_badge !!}
                    </div>

                    <div class="mcp-mobile-card__title">
                        <div class="mcp-row-flex">
                            <x-mcp.avatar :name="$order->customer->name ?? ''" />
                            <span>{{ $order->customer->name ?? '-' }}</span>
                        </div>
                    </div>

                    @if($order->description)
                        <div class="mcp-mobile-card__meta">
                            <span>{{ \Illuminate\Support\Str::limit($order->description, 80) }}</span>
                        </div>
                    @endif

                    <div class="mcp-mobile-card__meta">
                        <x-mcp.status kind="neutral" size="sm" :label="$order->workOrders->count().' jobs'" no-dot />
                        @if($order->bills->isEmpty())
                            <x-mcp.status kind="warning" size="sm" label="No quotation" />
                        @else
                            @foreach($order->bills->take(2) as $bill)
                                <x-mcp.bill-chip :bill="$bill" :href="auth()->user()?->canany(['view invoice', 'view quotation']) ? route('bill.show', $bill) : null" />
                            @endforeach
                        @endif
                        <span><i class="fa fa-calendar"></i> {{ $order->created_at->format('d M, y') }}</span>
                    </div>

                    <div class="mcp-mobile-card__actions">
                        <x-mcp.btn variant="primary" size="sm" icon="fa-eye"
                            :href="route('customer-orders.show', $order)">View</x-mcp.btn>
                        @can('delete customer order')
                            <x-mcp.btn variant="danger" size="sm" icon="fa-trash"
                                wire:click="deleteConfirm({{ $order->id }})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModalOrder">Delete</x-mcp.btn>
                        @endcan
                    </div>
                </x-mcp.mobile-card>
            @empty
                <x-mcp.empty title="No customer orders found"
                    hint="Create an order to group jobs and billing for a customer.">
                    @can('add customer order')
                        <x-slot:action>
                            <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                                wire:click="create"
                                data-coreui-toggle="modal" data-coreui-target="#modal-order">
                                New order
                            </x-mcp.btn>
                        </x-slot:action>
                    @endcan
                </x-mcp.empty>
            @endforelse

            <div class="mt-3">{{ $orders->withQueryString()->links() }}</div>
        </div>
    </div>

    <div class="modal fade" wire:ignore.self id="modal-order" tabindex="-1" role="dialog" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header py-2">
                    <h5 class="modal-title">Create Customer Order</h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="mb-3">
                        <div class="form-check form-switch">
                            <input class="form-check-input" type="checkbox" role="switch"
                                   wire:model.live="is_new_customer" id="is_new_customer">
                            <label class="form-check-label" for="is_new_customer">New customer?</label>
                        </div>
                    </div>

                    @if($is_new_customer)
                        <div class="mb-3">
                            <label for="new_customer_name" class="form-label">Customer Name <span class="text-danger">*</span></label>
                            <input type="text" wire:model.live="new_customer_name" id="new_customer_name"
                                   class="form-control form-control-sm @error('new_customer_name') is-invalid @enderror"
                                   placeholder="Enter customer name">
                            @error('new_customer_name')
                            <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label for="new_customer_phone" class="form-label">Phone <small class="text-muted">(Optional)</small></label>
                                <input type="text" wire:model.live="new_customer_phone" id="new_customer_phone"
                                       class="form-control form-control-sm @error('new_customer_phone') is-invalid @enderror"
                                       placeholder="Enter phone number">
                                @error('new_customer_phone')
                                <div class="invalid-feedback">{{ $message }}</div>
                                @enderror
                            </div>
                            <div class="col-md-6 mb-3">
                                <label for="new_customer_email" class="form-label">Email <small class="text-muted">(Optional)</small></label>
                                <input type="email" wire:model.live="new_customer_email" id="new_customer_email"
                                       class="form-control form-control-sm @error('new_customer_email') is-invalid @enderror"
                                       placeholder="Enter email address">
                                @error('new_customer_email')
                                <div class="invalid-feedback">{{ $message }}</div>
                                @enderror
                            </div>
                        </div>
                        <div class="alert alert-info py-2 small">
                            <i class="fa fa-info-circle me-1"></i>
                            Remember to update the customer's full details on the <a href="{{ route('customer.index') }}" target="_blank">Customers page</a>.
                        </div>
                    @else
                        <div class="mb-3">
                            @livewire('customer-search-component', key('customer-search-order'))
                            @error('customer_id')
                            <div class="text-danger small mt-1">{{ $message }}</div>
                            @enderror
                        </div>
                    @endif

                    <div class="mb-3">
                        <label for="description" class="form-label">Description</label>
                        <textarea wire:model.live="description" id="description"
                                  class="form-control form-control-sm @error('description') is-invalid @enderror"
                                  rows="3"
                                  placeholder="Brief description of this order..."></textarea>
                        @error('description')
                        <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>
                </div>
                <div class="modal-footer py-2">
                    <button type="button" wire:click.prevent="store" class="btn btn-primary btn-sm"
                            wire:loading.attr="disabled"
                            wire:target="store">
                        <span wire:loading.remove wire:target="store">
                            Create Order
                        </span>
                        <span wire:loading wire:target="store">
                            <i class="fa fa-spinner fa-spin"></i> Creating...
                        </span>
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade" wire:ignore.self id="deleteModalOrder" tabindex="-1" role="dialog" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Delete Confirm</h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <p>Are you sure you want to delete order
                        <strong>{{ $delete_confirm?->order_number }}</strong>?
                    </p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-coreui-dismiss="modal">Cancel</button>
                    <button type="button" wire:click.prevent="destroy()" class="btn btn-danger"
                            data-coreui-dismiss="modal">Yes, Delete
                    </button>
                </div>
            </div>
        </div>
    </div>

    @script
        <script>
            $wire.on('closeModal', () => {
                $('#modal-order').modal('hide');
            });

            // Auto-open modal if create parameter is present in URL (runs post-hydration)
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('create')) {
                var modalEl = document.getElementById('modal-order');
                if (modalEl) {
                    var modal = new coreui.Modal(modalEl);
                    modal.show();
                    window.history.replaceState({}, document.title, window.location.pathname);
                }
            }
        </script>
    @endscript
</div>

