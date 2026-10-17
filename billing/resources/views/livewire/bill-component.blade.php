<div>
    <div class="mcp">
        @php
            $pageTitle = match ($type) {
                'Invoice' => 'Invoices',
                'Quotation' => 'Quotations',
                'Payable Quotation' => 'Payable Quotations',
                default => \Illuminate\Support\Str::plural($type),
            };
            $idColumnLabel = match ($type) {
                'Invoice' => 'Invoice Id',
                'Quotation' => 'Quotation Id',
                'Payable Quotation' => 'Payable Quotation Id',
                default => $type . ' Id',
            };
            $forColumnLabel = match ($type) {
                'Invoice' => 'Invoice For',
                'Quotation' => 'Quotation For',
                'Payable Quotation' => 'Payable Quotation For',
                default => $type . ' For',
            };
        @endphp

        <x-mcp.page-header :subtitle="$bills->total().' total'">
            <x-slot:titleSlot>
                <span class="mcp-page-header__title-soft">Manage</span> {{ $pageTitle }}
            </x-slot:titleSlot>
            <x-slot:actions>
                @if($type == 'Quotation')
                    @can('add quotation')
                        @if(auth()->user()->role == 'admin' || auth()->user()->role == 'manager')
                            <x-mcp.btn variant="warning" size="sm" icon="fa-list"
                                :href="route('bill.index', ['type' => 'Payable Quotation'])">
                                <span class="d-none d-sm-inline">Payable</span> Quotations
                            </x-mcp.btn>
                        @endif
                        <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                            :href="route('bill.create', ['type' => 'Quotation'])">
                            New Quotation
                        </x-mcp.btn>
                    @endcan
                @elseif($type == 'Invoice')
                    {{-- Invoice creation is now done through Customer Orders --}}
                @else
                    @can('add quotation')
                        <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                            :href="route('bill.create', ['type' => 'Quotation'])">
                            New Quotation
                        </x-mcp.btn>
                    @endcan
                @endif
            </x-slot:actions>
        </x-mcp.page-header>

        <x-mcp.toolbar>
            <x-mcp.search wireModel="search_keyword" placeholder="Search by anything" />

            <div class="dropdown">
                <x-mcp.filter-btn
                    :active="$filter_date_from || $filter_date_to"
                    badge="ON"
                    data-coreui-toggle="dropdown" data-coreui-auto-close="outside">
                    Filter
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
                    <div class="d-flex justify-content-end gap-2 mt-2">
                        @if($filter_date_from || $filter_date_to)
                            <button type="button" wire:click="clearDateFilter"
                                    class="btn btn-sm btn-outline-secondary">Clear</button>
                        @endif
                        <button type="button" wire:click="$refresh" class="btn btn-sm btn-primary">
                            <i class="fa fa-check"></i> Filter
                        </button>
                    </div>
                </div>
            </div>

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

        {{-- ==================== DESKTOP TABLE (hidden on mobile) ==================== --}}
        <div class="d-none d-md-block">
            <x-mcp.card flush>
                <div class="mcp-table-wrap" wire:loading.class="mcp-skeleton-refreshing" wire:loading.attr="aria-busy">
                    <table class="mcp-table mcp-table--sticky-last">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>{{ $idColumnLabel }}</th>
                                <th>Type</th>
                                <th>{{ $forColumnLabel }}</th>
                                <th>Issue Date</th>
                                <th>Currency</th>
                                <th class="mcp-td--right">Total</th>
                                <th class="mcp-td--right">Balance</th>
                                <th>Status</th>
                                <th>Remark</th>
                                <th class="mcp-td--right">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            @forelse($bills as $bill)
                                @include('livewire.partials.mcp-bills-table-row', [
                                    'bill' => $bill,
                                    'index' => $loop->index + 1,
                                ])
                            @empty
                                <tr>
                                    <td colspan="11">
                                        <x-mcp.empty title="No {{ strtolower($pageTitle) }} found"
                                            hint="Try adjusting your search or filters.">
                                            @if($type == 'Quotation')
                                                @can('add quotation')
                                                    <x-slot:action>
                                                        <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                                                            :href="route('bill.create', ['type' => 'Quotation'])">
                                                            New quotation
                                                        </x-mcp.btn>
                                                    </x-slot:action>
                                                @endcan
                                            @endif
                                        </x-mcp.empty>
                                    </td>
                                </tr>
                            @endforelse
                        </tbody>
                    </table>
                </div>
                <div class="px-3 pb-3">
                    {{ $bills->withQueryString()->links() }}
                </div>
            </x-mcp.card>
        </div>

        {{-- ==================== MOBILE CARD LIST (visible only on mobile) ==================== --}}
        <div class="d-md-none">
            @forelse($bills as $bill)
                @php
                    $billBalance = $bill->balance();
                    $billAmount = $bill->amount();
                    $isPriority = $bill->needPayment()
                        || (in_array($bill->type, ['Invoice', 'Payable Quotation']) && ! $bill->has_transactions);
                @endphp
                <x-mcp.mobile-card :priority="$isPriority">
                    <div class="mcp-mobile-card__head">
                        <span class="small fw-semibold text-muted">{{ $bill->type }}</span>
                        <div>
                            {!! $bill->paymentBadge() !!}
                        </div>
                    </div>

                    <div class="mcp-mobile-card__title">
                        {{ $bill->customer->name }}
                        @if($bill->needPayment())
                            <small class="text-danger fw-bold ms-2">FOLLOW UP</small>
                        @endif
                    </div>

                    <div class="mcp-mobile-card__meta">
                        <span><i class="fa fa-hashtag me-1"></i>{{ $bill->invoice_number ?? $bill->id }}</span>
                        <span><i class="fa fa-calendar me-1"></i>{{ $bill->created_at->format('d-m-Y') }}</span>
                        <span><i class="fa fa-coins me-1"></i>{{ $bill->currency->currency_code }}</span>
                    </div>

                    <div class="mcp-mobile-card__meta">
                        <span>
                            <span class="text-muted">Total</span>
                            <span class="fw-bold ms-1">{{ number_format($billAmount, 2) }}</span>
                        </span>
                        <span>
                            <span class="text-muted">Balance</span>
                            @if($billBalance > 0)
                                <span class="fw-bold text-danger ms-1">{{ number_format($billBalance, 2) }}</span>
                            @elseif($billBalance < 0)
                                <span class="fw-bold text-warning ms-1">{{ number_format($billBalance, 2) }}</span>
                            @else
                                <span class="fw-bold text-success ms-1">0.00</span>
                            @endif
                        </span>
                    </div>

                    <div class="mcp-mobile-card__actions">
                        <x-mcp.btn variant="primary" size="sm" icon="fa-eye" :href="route('bill.show', $bill)">
                            Show
                        </x-mcp.btn>
                        @can('add invoice')
                            @if($bill->type != 'Invoice' && $bill->type != 'Payable Quotation')
                                <x-mcp.btn variant="warning" size="sm" icon="fa-share"
                                    data-coreui-toggle="modal" data-coreui-target="#modal-convert-tax-{{ $bill->id }}">
                                    To Tax Invoice
                                </x-mcp.btn>
                                @if(auth()->user()->role == 'admin')
                                    <x-mcp.btn variant="warning" size="sm" icon="fa-file-invoice-dollar"
                                        data-coreui-toggle="modal" data-coreui-target="#modal-convert-payable-{{ $bill->id }}">
                                        To Payable Quotation
                                    </x-mcp.btn>
                                @endif
                            @endif
                        @endcan
                        @if($bill->type == 'Invoice')
                            @can('edit invoice')
                                <x-mcp.btn variant="secondary" size="sm" icon="fa-edit" :href="route('bill.create', $bill)">
                                    Edit
                                </x-mcp.btn>
                            @endcan
                        @else
                            @can('edit quotation')
                                <x-mcp.btn variant="secondary" size="sm" icon="fa-edit" :href="route('bill.create', $bill)">
                                    Edit
                                </x-mcp.btn>
                            @endcan
                        @endif
                    </div>
                </x-mcp.mobile-card>
            @empty
                <x-mcp.empty title="No {{ strtolower($pageTitle) }} found"
                    hint="Try adjusting your search or filters.">
                    @if($type == 'Quotation')
                        @can('add quotation')
                            <x-slot:action>
                                <x-mcp.btn variant="primary" size="sm" icon="fa-plus"
                                    :href="route('bill.create', ['type' => 'Quotation'])">
                                    New quotation
                                </x-mcp.btn>
                            </x-slot:action>
                        @endcan
                    @endif
                </x-mcp.empty>
            @endforelse

            <div class="mt-3">
                {{ $bills->withQueryString()->links() }}
            </div>
        </div>
    </div>

    {{-- Convert confirmation modals (hoisted to root level: container-type/overflow on the
         table card would clip or mis-position them if rendered inside the rows) --}}
    @can('add invoice')
        @foreach($bills as $bill)
            @if($bill->type != 'Invoice' && $bill->type != 'Payable Quotation')
                <x-modal.confirm id="modal-convert-tax-{{ $bill->id }}"
                                 title="Convert to Tax Invoice"
                                 action="{{ route('bill.convert', $bill) }}"
                                 confirmText="Convert">
                    Are you sure you want to convert bill <strong>{{ $bill->invoice_number ?? $bill->id }}</strong> to a Tax Invoice?
                </x-modal.confirm>
                @if(auth()->user()->role == 'admin')
                    <x-modal.confirm id="modal-convert-payable-{{ $bill->id }}"
                                     title="Convert to Payable Quotation"
                                     action="{{ route('bill.convert.payable', $bill) }}"
                                     confirmText="Convert">
                        Are you sure you want to convert bill <strong>{{ $bill->invoice_number ?? $bill->id }}</strong> to a Payable Quotation?
                    </x-modal.confirm>
                @endif
            @endif
        @endforeach
    @endcan

    <!-- Delete Modal -->
    <div wire:ignore.self class="modal fade" id="deleteModalbill" data-backdrop="false" tabindex="-1" role="dialog"
         aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Delete Confirm </h5>
                </div>
                <div class="modal-body">
                    <p>Are you sure want to delete <strong>{{ $delete_confirm? $delete_confirm->name: ''  }}</strong> ?
                    </p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary close-btn" data-coreui-dismiss="modal">Cancel
                    </button>
                    <button type="button" wire:click.prevent="destroy()" class="btn btn-danger close-modal"
                            data-coreui-dismiss="modal">Yes, Delete
                    </button>
                </div>
            </div>
        </div>
    </div>

    @script
        <script>
            $wire.on('closeModal', () => {
                $('#modal-bill').modal('hide');
            });
        </script>
    @endscript
</div>
