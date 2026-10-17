<div
    @if($contextVariant === 'index')
        data-mcp-page-context-owner="jobs"
        data-mcp-context-route="work-order.index"
        data-mcp-context-kind="list"
        data-mcp-context-resource="work_order"
        data-mcp-context-id="jobs:work-order.index"
    @endif
>
    <div class="mcp">
        @if(!$bill)
            @if($customer)
                {{-- Customer-scoped variant (embedded on Customer detail view).
                     Unchanged from prior — user's edit was scoped to the
                     unfiltered Manage Jobs page only. --}}
                <x-mcp.page-header :subtitle="$work_orders->total().' total'">
                    <x-slot:titleSlot>
                        <span class="mcp-page-header__title-soft">Jobs for</span> {{ $customer->name }}
                    </x-slot:titleSlot>
                </x-mcp.page-header>
            @else
                {{-- Unfiltered Manage Jobs index. No eyebrow, no subtitle.
                     "Manage" rendered at weight 500 to emphasize "Jobs" at
                     the heading's full weight 700 — see .mcp-page-header__title-soft. --}}
                <x-mcp.page-header>
                    <x-slot:titleSlot>
                        <span class="mcp-page-header__title-soft">Manage</span> Jobs
                    </x-slot:titleSlot>
                </x-mcp.page-header>
            @endif

            <x-mcp.toolbar>
                <x-mcp.search wireModel="search_keyword" placeholder="Search by job #, customer, title, bill…" />

                {{-- Filters dropdown --}}
                <div class="dropdown">
                    <x-mcp.filter-btn
                        :active="$filter_date_from || $filter_date_to || $filter_bill_number"
                        badge="ON"
                        data-coreui-toggle="dropdown" data-coreui-auto-close="outside">
                        Filters
                    </x-mcp.filter-btn>
                    <div class="dropdown-menu p-3 mcp-filter-menu">
                        <label class="form-label small fw-bold">Date Range</label>
                        <div class="mb-2">
                            <label class="form-label small mb-1">From</label>
                            <input type="date" wire:model.live="filter_date_from" class="form-control form-control-sm">
                        </div>
                        <div class="mb-2">
                            <label class="form-label small mb-1">To</label>
                            <input type="date" wire:model.live="filter_date_to" class="form-control form-control-sm">
                        </div>
                        <hr class="my-2">
                        <label class="form-label small fw-bold">Bill Number</label>
                        <div class="mb-2">
                            <input type="text" wire:model.live="filter_bill_number"
                                class="form-control form-control-sm" placeholder="Search bill...">
                        </div>
                        <div class="d-flex justify-content-end gap-2 mt-2">
                            @if($filter_date_from || $filter_date_to || $filter_bill_number)
                                <button type="button" wire:click="clearAllFilters"
                                    class="btn btn-sm btn-outline-secondary">Clear All</button>
                            @endif
                            <button type="button" wire:click="$refresh" class="btn btn-sm btn-primary">
                                <i class="fa fa-check"></i> Filter
                            </button>
                        </div>
                    </div>
                </div>

                {{-- Sort dropdown --}}
                @php
                    $sortBadge = match (true) {
                        !$sort_field                                                 => null,
                        $sort_field === 'updated_at' && $sort_direction === 'desc'   => 'Updated Desc',
                        $sort_field === 'updated_at' && $sort_direction === 'asc'    => 'Updated Asc',
                        $sort_field === 'created_at' && $sort_direction === 'desc'   => 'Created Desc',
                        $sort_field === 'created_at' && $sort_direction === 'asc'    => 'Created Asc',
                        default                                                       => null,
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
                        <button
                            class="dropdown-item {{ $sort_field === 'created_at' && $sort_direction === 'desc' ? 'active' : '' }}"
                            wire:click="setSort('created_at', 'desc')">
                            Date Descending
                        </button>
                        <button
                            class="dropdown-item {{ $sort_field === 'created_at' && $sort_direction === 'asc' ? 'active' : '' }}"
                            wire:click="setSort('created_at', 'asc')">
                            Date Ascending
                        </button>
                        <button
                            class="dropdown-item {{ $sort_field === 'updated_at' && $sort_direction === 'desc' ? 'active' : '' }}"
                            wire:click="setSort('updated_at', 'desc')">
                            Recently Updated
                        </button>
                        <button
                            class="dropdown-item {{ $sort_field === 'updated_at' && $sort_direction === 'asc' ? 'active' : '' }}"
                            wire:click="setSort('updated_at', 'asc')">
                            Least Recently Updated
                        </button>
                        <div class="dropdown-divider"></div>
                        <button class="dropdown-item {{ !$sort_field ? 'active' : '' }}" wire:click="clearSort">
                            None
                        </button>
                    </div>
                </div>

                <div class="mcp-density-toggle ms-auto"
                     role="group"
                     aria-label="Table density">
                    <button type="button"
                            @class(['mcp-density-toggle__btn', 'is-active' => $density === 'expanded'])
                            aria-pressed="{{ $density === 'expanded' ? 'true' : 'false' }}"
                            wire:click="setDensity('expanded')"
                            title="Expanded — show all columns, scroll if needed"
                            aria-label="Expanded — show all columns">
                        <i class="fa fa-arrows-left-right" aria-hidden="true"></i>
                        <span class="mcp-density-toggle__label">Expanded</span>
                    </button>
                    <button type="button"
                            @class(['mcp-density-toggle__btn', 'is-active' => $density === 'compact'])
                            aria-pressed="{{ $density === 'compact' ? 'true' : 'false' }}"
                            wire:click="setDensity('compact')"
                            title="Compact — fit the viewport, hide dates"
                            aria-label="Compact — fit the viewport">
                        <i class="fa fa-compress" aria-hidden="true"></i>
                        <span class="mcp-density-toggle__label">Compact</span>
                    </button>
                </div>
            </x-mcp.toolbar>
        @endif

        {{-- ==================== DESKTOP TABLE ==================== --}}
        <div class="d-none d-md-block">
            <x-mcp.card flush>
                <div data-dual-scroll>
                    <div class="mcp-table-scroll-top" data-dual-scroll-top aria-hidden="true">
                        <div class="mcp-table-scroll-top__rail"></div>
                    </div>
                    <div class="mcp-table-wrap" data-dual-scroll-wrap wire:loading.class="mcp-skeleton-refreshing" wire:loading.attr="aria-busy">
                    <table @class([
                        'mcp-table mcp-table--jobs mcp-table--sticky-first mcp-table--sticky-last',
                        'mcp-table--compact' => $density === 'compact',
                    ])>
                        <thead>
                            <tr>
                                <th style="width:130px">Job</th>
                                <th style="width:110px">Order</th>
                                <th style="width:180px">Bill</th>
                                @if($bill)
                                    <th>Description</th>
                                @else
                                    <th style="width:200px">Customer</th>
                                    <th>Description</th>
                                @endif
                                <th style="width:180px">Status</th>
                                <th style="width:150px">Department</th>
                                <th style="width:100px" class="col-created mcp-td--secondary">Created</th>
                                <th style="width:100px" class="col-updated mcp-td--secondary">Updated</th>
                                <th style="width:120px" class="col-creator">Creator</th>
                                <th class="mcp-th--actions-responsive mcp-td--right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            @forelse($work_orders as $work_order)
                                @include('livewire.partials.mcp-jobs-table-row', ['work_order' => $work_order, 'bill' => $bill])
                            @empty
                                <tr>
                                    <td colspan="{{ $bill ? 10 : 11 }}">
                                        <x-mcp.empty title="No jobs found"
                                            hint="Jobs are created from customer orders.">
                                            @if(!$bill && Route::has('customer-orders.index'))
                                                <x-slot:action>
                                                    <x-mcp.btn variant="primary" size="sm" icon="fa-folder"
                                                        :href="route('customer-orders.index')">
                                                        Go to orders
                                                    </x-mcp.btn>
                                                </x-slot:action>
                                            @endif
                                        </x-mcp.empty>
                                    </td>
                                </tr>
                            @endforelse
                        </tbody>
                    </table>
                    </div>
                </div>

                @if(!$bill)
                    {{ $work_orders->withQueryString()->links() }}
                    <div class="mcp-perpage">
                        <label for="jobs-perpage" class="mcp-perpage__label">Per page</label>
                        <select id="jobs-perpage" wire:model.live="perPage" class="mcp-perpage__select">
                            <option value="10">10</option>
                            <option value="25">25</option>
                            <option value="50">50</option>
                            <option value="100">100</option>
                        </select>
                    </div>
                @endif
            </x-mcp.card>
        </div>

        {{-- ==================== MOBILE CARDS ==================== --}}
        <div class="d-md-none">
            @forelse($work_orders as $work_order)
                @php($isPriority = $work_order->priority === 'high' && $work_order->department_id != 12)
                <x-mcp.mobile-card
                    :priority="$isPriority"
                    :data-mcp-context-job-id="in_array($contextVariant, ['index', 'customer', 'bill'], true) ? $work_order->id : null"
                >
                    <div class="mcp-mobile-card__head">
                        <x-mcp.status :kind="$work_order->statusKind()" size="sm" :label="$work_order->statusLabel()" />
                        <x-mcp.dept-badge :department="$work_order->department" />
                    </div>

                    <div class="mcp-mobile-card__title">
                        #{{ $work_order->work_number ?? $work_order->id }} — {{ $work_order->title }}
                        @if($isPriority)
                            <x-mcp.status kind="danger" size="sm" label="HIGH PRIORITY" />
                        @endif
                    </div>

                    @if(!$bill && $work_order->customer)
                        <div class="mcp-mobile-card__meta">
                            <span><i class="fa fa-user"></i> {{ $work_order->customer->name }}</span>
                        </div>
                    @endif

                    <div class="mcp-mobile-card__meta">
                        @if($work_order->customerOrder)
                            <span>
                                <i class="fa fa-folder"></i>
                                @if(Route::has('customer-orders.show'))
                                    <a href="{{ route('customer-orders.show', $work_order->customerOrder) }}">
                                        Order #{{ $work_order->customerOrder->order_number }}
                                    </a>
                                @else
                                    Order #{{ $work_order->customerOrder->order_number }}
                                @endif
                            </span>
                        @endif
                        @if($work_order->bill)
                            <span>
                                <i class="fa fa-file-text"></i>
                                @canany(['view invoice', 'view quotation'])
                                    <a href="{{ route('bill.show', $work_order->bill) }}">
                                        {{ $work_order->bill->type }} #{{ $work_order->bill->invoice_number ?? $work_order->bill->id }}
                                    </a>
                                @else
                                    {{ $work_order->bill->type }} #{{ $work_order->bill->invoice_number ?? $work_order->bill->id }}
                                @endcanany
                            </span>
                        @endif
                        <span><i class="fa fa-calendar"></i> {{ $work_order->created_at->format('d M, y') }}</span>
                        @if($work_order->user)
                            <span><i class="fa fa-user-circle"></i> {{ $work_order->user->name }}</span>
                        @endif
                    </div>

                    <div class="mcp-mobile-card__actions">
                        <x-mcp.btn variant="primary" size="sm" icon="fa-eye" :href="route('work-order.show', $work_order)">
                            View
                        </x-mcp.btn>
                        @can('move job')
                            @if($work_order->status == 'PENDING MOVE APPROVAL')
                                <x-mcp.btn variant="danger" size="sm" icon="fa-times"
                                    data-coreui-toggle="modal" :data-coreui-target="'#modal-cancel-move-'.$work_order->id">
                                    Cancel Move
                                </x-mcp.btn>
                            @else
                                <x-mcp.btn variant="secondary" size="sm" icon="fa-edit"
                                    wire:click="editJob({{ $work_order->id }})"
                                    data-coreui-toggle="modal" data-coreui-target="#modal-work_order">
                                    Move
                                </x-mcp.btn>
                            @endif
                        @endcan
                        @can('add job')
                            <x-mcp.btn variant="primary" size="sm" icon="fa-copy"
                                wire:click="openCopyModal({{ $work_order->id }})"
                                data-coreui-toggle="modal" data-coreui-target="#modal-copy-job">
                                Copy
                            </x-mcp.btn>
                        @endcan
                        @can('delete job')
                            <x-mcp.btn variant="danger" size="sm" icon="fa-trash"
                                wire:click="deleteConfirm({{ $work_order->id }})"
                                data-coreui-toggle="modal" data-coreui-target="#modal-delete-job">
                                Delete
                            </x-mcp.btn>
                        @endcan
                    </div>
                </x-mcp.mobile-card>
            @empty
                <x-mcp.empty title="No jobs found"
                    hint="Jobs are created from customer orders.">
                    @if(!$bill && Route::has('customer-orders.index'))
                        <x-slot:action>
                            <x-mcp.btn variant="primary" size="sm" icon="fa-folder"
                                :href="route('customer-orders.index')">
                                Go to orders
                            </x-mcp.btn>
                        </x-slot:action>
                    @endif
                </x-mcp.empty>
            @endforelse

            @if(!$bill)
                <div class="mt-3">
                    {{ $work_orders->withQueryString()->links() }}
                </div>
                <div class="mcp-perpage mcp-perpage--mobile">
                    <label for="jobs-perpage-mobile" class="mcp-perpage__label">Per page</label>
                    <select id="jobs-perpage-mobile" wire:model.live="perPage" class="mcp-perpage__select">
                        <option value="10">10</option>
                        <option value="25">25</option>
                        <option value="50">50</option>
                        <option value="100">100</option>
                    </select>
                </div>
            @endif
        </div>
    </div>

    <!-- Modal for Job Update -->
    <div class="modal fade" wire:ignore.self id="modal-work_order" tabindex="-1" role="dialog" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header py-2">
                    <h5 class="modal-title">@if($isEditing)
                        Update Job
                    @else
                            Add Job
                        @endif</h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    @can('edit job')
                        <div class="mb-4">
                            <div class="form-check form-switch">
                                <input class="form-check-input" wire:model.live="delivered" type="checkbox" role="switch"
                                    id="delivery">
                                <label class="form-check-label" for="delivery">DELIVERED TO CUSTOMER?</label>
                            </div>
                        </div>
                    @endcan

                    <div class="mb-4">
                        <label>Current Department</label>
                        <input type="text" class="form-control form-control-sm"
                            value="{{ $selected_work_order?->department?->name ?? 'None' }}" readonly disabled>
                    </div>

                    <div class="mb-4">
                        <label for="department" class="form-label">Move To <span class="text-danger">*</span></label>
                        <select wire:model.live="department"
                            class="form-select form-select-sm @error('department') is-invalid @enderror"
                            id="department">
                            <option value="">--Select--</option>
                            @foreach($departments as $dept)
                                @if($dept->id != $selected_work_order?->department_id)
                                    <option value="{{ $dept->id }}">{{ $dept->name }}</option>
                                @endif
                            @endforeach
                        </select>
                        @error('department')
                            <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>

                    <div class="mb-4">
                        <label for="priority" class="form-label">Priority</label>
                        <select wire:model.live="priority" class="form-select form-select-sm" id="priority"
                            @if($selected_work_order?->department_id == 12) disabled @endif>
                            <option value="normal">Normal</option>
                            <option value="high">High</option>
                        </select>
                        @if($selected_work_order?->department_id == 12)
                            <small class="text-muted">Priority cannot be changed for delivered jobs</small>
                        @endif
                    </div>

                    <div class="mb-4">
                        <label for="remark" class="form-label">Description</label>
                        <textarea wire:model.live="remark" class="form-control form-control-sm" id="remark"
                            placeholder="Optional notes about this update"></textarea>
                    </div>
                </div>
                <div class="modal-footer py-2">
                    <button type="button" wire:click.prevent="store" class="btn btn-primary btn-sm"
                        wire:loading.attr="disabled" wire:target="store" @if($isEditing && !$this->hasModalChanges())
                        disabled title="Make a change to enable" @endif>
                        <span wire:loading.remove wire:target="store">
                            @if($isEditing)
                                Update
                            @else
                                Add
                            @endif
                        </span>
                        <span wire:loading wire:target="store">
                            <i class="fa fa-spinner fa-spin"></i> Saving...
                        </span>
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal for Copy Job -->
    <div class="modal fade" wire:ignore.self id="modal-copy-job" tabindex="-1" role="dialog" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header py-2 bg-light">
                    <h5 class="modal-title">
                        <i class="fa fa-copy text-primary"></i> Copy Job
                    </h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"
                        wire:click="resetCopyModal"></button>
                </div>
                <div class="modal-body">
                    @if($copy_source_job)
                        {{-- Source Job Info Card --}}
                        <div class="d-flex align-items-start p-2 bg-light rounded mb-3">
                            <div class="flex-shrink-0">
                                <x-badge color="secondary" class="fs-6">#{{ $copy_source_job->work_number ?? $copy_source_job->id }}</x-badge>
                            </div>
                            <div class="flex-grow-1 ms-2">
                                <strong>{{ $copy_source_job->title }}</strong>
                                <div class="small text-muted">
                                    <i class="fa fa-user"></i> {{ $copy_source_job->customer->name ?? 'N/A' }}
                                    @if($copy_source_job->customerOrder)
                                        <span class="mx-1">•</span>
                                        <i class="fa fa-folder"></i> Order #{{ $copy_source_job->customerOrder->order_number }}
                                    @endif
                                </div>
                            </div>
                        </div>

                        {{-- Copy Destination Options --}}
                        <label class="form-label fw-semibold mb-2">Copy destination</label>

                        <div class="d-flex flex-column gap-2 mb-3">
                            {{-- New Order Option (Default) --}}
                            <label
                                class="d-flex align-items-start p-2 border rounded {{ $copy_target === 'new_order' ? 'border-primary bg-primary bg-opacity-10' : '' }}"
                                style="cursor: pointer;">
                                <input class="form-check-input mt-1 me-2" type="radio" wire:model.live="copy_target"
                                    value="new_order" id="copy_new_order">
                                <div>
                                    <strong>Create New Order</strong>
                                    <div class="small text-muted">New Invoice will be created</div>
                                </div>
                            </label>

                            {{-- Existing Order Option --}}
                            <label
                                class="d-flex align-items-start p-2 border rounded {{ $copy_target === 'existing_order' ? 'border-primary bg-primary bg-opacity-10' : '' }}"
                                style="cursor: pointer;">
                                <input class="form-check-input mt-1 me-2" type="radio" wire:model.live="copy_target"
                                    value="existing_order" id="copy_existing_order">
                                <div>
                                    <strong>Add to Existing Order</strong>
                                    <div class="small text-muted">Job added to Existing Order and Invoice</div>
                                </div>
                            </label>
                        </div>

                        {{-- Customer Selection (shown only for new order) --}}
                        @if($copy_target === 'new_order')
                            <div class="p-3 border rounded bg-light">
                                <div class="mb-3">
                                    <div class="form-check form-switch">
                                        <input class="form-check-input" type="checkbox" role="switch"
                                            wire:model.live="is_new_customer" id="copy_is_new_customer">
                                        <label class="form-check-label" for="copy_is_new_customer">New customer?</label>
                                    </div>
                                </div>

                                @if($is_new_customer)
                                    <div class="mb-3">
                                        <label for="copy_new_customer_name" class="form-label">Customer Name <span
                                                class="text-danger">*</span></label>
                                        <input type="text" wire:model.live="new_customer_name" id="copy_new_customer_name"
                                            class="form-control form-control-sm @error('new_customer_name') is-invalid @enderror"
                                            placeholder="Enter customer name">
                                        @error('new_customer_name')
                                            <div class="invalid-feedback">{{ $message }}</div>
                                        @enderror
                                    </div>
                                    <div class="row">
                                        <div class="col-md-6 mb-3">
                                            <label for="copy_new_customer_phone" class="form-label">Phone <small
                                                    class="text-muted">(Optional)</small></label>
                                            <input type="text" wire:model.live="new_customer_phone" id="copy_new_customer_phone"
                                                class="form-control form-control-sm @error('new_customer_phone') is-invalid @enderror"
                                                placeholder="Enter phone number">
                                            @error('new_customer_phone')
                                                <div class="invalid-feedback">{{ $message }}</div>
                                            @enderror
                                        </div>
                                        <div class="col-md-6 mb-3">
                                            <label for="copy_new_customer_email" class="form-label">Email <small
                                                    class="text-muted">(Optional)</small></label>
                                            <input type="email" wire:model.live="new_customer_email" id="copy_new_customer_email"
                                                class="form-control form-control-sm @error('new_customer_email') is-invalid @enderror"
                                                placeholder="Enter email address">
                                            @error('new_customer_email')
                                                <div class="invalid-feedback">{{ $message }}</div>
                                            @enderror
                                        </div>
                                    </div>
                                @else
                                    @livewire('customer-search-component', [], key('copy-customer-search'))
                                    @if($copy_validation_error)
                                        <small class="text-danger d-block mt-2">
                                            <i class="fa fa-exclamation-circle"></i> Please select a customer to continue
                                        </small>
                                    @endif

                                    {{-- Loading indicator while checking for existing orders --}}
                                    <div wire:loading wire:target="onCustomerSelected" class="text-center mt-3">
                                        <div class="spinner-border spinner-border-sm text-primary" role="status">
                                            <span class="visually-hidden">Checking for existing orders...</span>
                                        </div>
                                        <small class="text-muted ms-2">Checking for existing orders...</small>
                                    </div>

                                    {{-- Suggested Existing Orders Banner --}}
                                    @if(count($suggestedExistingOrders) > 0)
                                        <div class="border rounded py-2 px-3 mt-3 mb-0">
                                            <div class="d-flex align-items-center mb-2">
                                                <i class="fa fa-info-circle text-muted me-2"></i>
                                                <small class="fw-semibold text-muted">This customer has active orders</small>
                                            </div>
                                            <div class="d-flex flex-wrap gap-2">
                                                @foreach($suggestedExistingOrders as $suggestedOrder)
                                                    <button type="button" wire:click="selectSuggestedOrder({{ $suggestedOrder->id }})"
                                                        class="btn btn-sm btn-primary">
                                                        Add Job to Existing Order #{{ $suggestedOrder->order_number }}
                                                    </button>
                                                @endforeach
                                            </div>
                                        </div>
                                    @endif
                                @endif
                            </div>
                        @endif

                        {{-- Customer Order Search (shown only for existing order) --}}
                        @if($copy_target === 'existing_order')
                            <div class="p-3 border rounded bg-light">
                                @livewire('customer-order-search-component', [], key('copy-order-search'))
                                @if($copy_validation_error)
                                    <small class="text-danger d-block mt-2">
                                        <i class="fa fa-exclamation-circle"></i> Please select an order to continue
                                    </small>
                                @endif
                            </div>
                        @endif

                        {{-- Job Order Format Selection --}}
                        <div class="mb-3 mt-3">
                            <label class="form-label fw-semibold mb-2">Job Order Format</label>
                            <select wire:model.live="copy_specification_id" class="form-select form-select-sm">
                                @foreach($specifications as $spec)
                                    <option value="{{ $spec->id }}">{{ $spec->name }}</option>
                                @endforeach
                            </select>
                        </div>

                    @else
                        {{-- Loading State --}}
                        <div class="text-center py-4">
                            <div class="spinner-border spinner-border-sm text-primary" role="status">
                                <span class="visually-hidden">Loading...</span>
                            </div>
                            <p class="text-muted mt-2 mb-0">Loading job details...</p>
                        </div>
                    @endif
                </div>
                <div class="modal-footer py-2">
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal"
                        wire:click="resetCopyModal">
                        Cancel
                    </button>
                    @if($copy_source_job)
                        <button type="button" wire:click="copyJob" class="btn btn-primary btn-sm"
                            wire:loading.attr="disabled" wire:target="copyJob,onCustomerSelected">
                            <span wire:loading.remove wire:target="copyJob,onCustomerSelected">
                                <i class="fa fa-copy"></i>
                                @if($copy_target === 'existing_order')
                                    Copy Job to Existing Order
                                @else
                                    Copy Job to New Order
                                @endif
                            </span>
                            <span wire:loading wire:target="copyJob">
                                <i class="fa fa-spinner fa-spin"></i> Copying...
                            </span>
                            <span wire:loading wire:target="onCustomerSelected">
                                <i class="fa fa-spinner fa-spin"></i> Checking...
                            </span>
                        </button>
                    @endif
                </div>
            </div>
        </div>
    </div>

    <!-- Delete Job Modal -->
    <div class="modal fade" wire:ignore.self id="modal-delete-job" tabindex="-1" role="dialog" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header py-2 bg-danger text-white">
                    <h5 class="modal-title">
                        <i class="fa fa-trash me-2"></i> Delete Job
                    </h5>
                    <button type="button" class="btn-close btn-close-white" data-coreui-dismiss="modal"
                        aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    @if($delete_confirm)
                        <p>Are you sure you want to delete job
                            <strong>#{{ $delete_confirm->work_number ?? $delete_confirm->id }}</strong>?
                        </p>
                        <p class="text-muted small mb-0">
                            <i class="fa fa-exclamation-triangle text-warning"></i>
                            This action cannot be undone. All job history and messages will be permanently deleted.
                        </p>
                    @endif
                </div>
                <div class="modal-footer py-2">
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                    <button type="button" wire:click="destroy" class="btn btn-danger btn-sm"
                        data-coreui-dismiss="modal">
                        <i class="fa fa-trash"></i> Yes, Delete
                    </button>
                </div>
            </div>
        </div>
    </div>

    @script
        <script>
            $wire.on('closeModal', () => {
                $('#modal-work_order').modal('hide');
            });

            $wire.on('closeCopyModal', () => {
                var modal = coreui.Modal.getInstance(document.getElementById('modal-copy-job'));
                if (modal) {
                    modal.hide();
                }
            });

            // Reset modal state when closed via backdrop click or X button
            var copyModalEl = document.getElementById('modal-copy-job');
            if (copyModalEl && !copyModalEl.dataset.mcpCopyModalBound) {
                copyModalEl.dataset.mcpCopyModalBound = '1';
                copyModalEl.addEventListener('hidden.coreui.modal', function () {
                    $wire.resetCopyModal();
                });
            }
        </script>
    @endscript
</div>