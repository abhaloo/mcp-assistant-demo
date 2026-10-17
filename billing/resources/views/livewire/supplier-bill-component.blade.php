<div>
    {{-- Header --}}
    <div class="d-flex flex-column flex-md-row align-items-md-center justify-content-between mb-3">
        <h1 class="h3 mb-2 mb-md-0">Manage <strong>Supplier Bills</strong></h1>
        <div class="d-flex gap-2">
            <a href="{{ route('supplier-bill.create', ['type' => 'Bill']) }}"
               class="btn btn-sm btn-primary">
                <span class="fa fa-file-invoice-dollar"></span>
                <span class="d-none d-sm-inline">New</span> Expense Bill
            </a>
            <a href="{{ route('supplier-bill.create', ['is_goods_costing' => true]) }}"
               class="btn btn-sm btn-success">
                <span class="fa fa-boxes"></span>
                <span class="d-none d-sm-inline">New</span> Goods Costing
            </a>
        </div>
    </div>

    {{-- Search --}}
    <div class="mb-3">
        <form class="col-12 col-md-6 col-lg-3 col-xl-4 px-0">
            <div class="input-group">
                <span class="input-group-text"><i class="fa fa-search"></i></span>
                <input type="text" wire:model.live="search_keyword" class="form-control"
                       placeholder="Search by reference, supplier invoice...">
            </div>
        </form>
    </div>

    {{-- ==================== DESKTOP TABLE (hidden on mobile) ==================== --}}
    <div class="card card-body border-0 shadow table-wrapper table-responsive d-none d-md-block">
        <table class="table table-hover table-sm">
            <thead>
            <tr>
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">Entry Id</th>
                <th class="border-gray-200">Type</th>
                <th class="border-gray-200">Supplier</th>
                <th class="border-gray-200">Invoice No.</th>
                <th class="border-gray-200">Post Date</th>
                <th class="border-gray-200 text-end">Total Value</th>
                <th class="border-gray-200 text-end">Balance</th>
                <th class="border-gray-200 ps-5">Payment Status</th>
                <th class="border-gray-200 text-center">Action</th>
            </tr>
            </thead>
            <tbody>
            @forelse($inventories as $inventory)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $inventory->id }}</td>
                    <td>
                        @if($inventory->is_goods_costing)
                            <x-badge color="info" icon="fa-boxes">Goods Costing</x-badge>
                            @if($inventory->linkedReceiveEntries->count() > 0)
                                <small class="text-muted d-block">
                                    {{ $inventory->linkedReceiveEntries->count() }} receive entry(s)
                                </small>
                            @endif
                        @else
                            <x-badge color="secondary" icon="fa-file-invoice-dollar">Expense</x-badge>
                        @endif
                    </td>
                    <td>{{ $inventory->supplier->name ?? '-' }}</td>
                    <td>{{ $inventory->supplier_invoice_no ?? '-' }}</td>
                    <td>{{ $inventory->created_at->format('d-m-Y') }}</td>
                    @php
                        $billAmount = $inventory->amount();
                        $billBalance = $inventory->balance();
                    @endphp
                    <td class="text-end">
                        <strong>{{ number_format($billAmount, 2) }}</strong>
                    </td>
                    <td class="text-end">
                        @if($billBalance > 0)
                            <span class="text-danger">{{ number_format($billBalance, 2) }}</span>
                        @else
                            <span class="text-success">0.00</span>
                        @endif
                    </td>
                    <td class="ps-5">
                        @if($billAmount > 0)
                            @if($billBalance == 0)
                                <x-badge color="success">PAID</x-badge>
                            @elseif($billBalance > 0 && $billBalance < $billAmount)
                                <x-badge color="info">PARTIAL</x-badge>
                            @else
                                <x-badge color="danger">UNPAID</x-badge>
                            @endif
                        @else
                            <x-badge color="secondary">-</x-badge>
                        @endif
                    </td>
                    <td class="text-end">
                        <a href="{{ route('supplier-bill.show', $inventory) }}" class="btn btn-primary btn-sm">
                            <i class="fa fa-eye"></i>
                            Show</a>
                        @can('edit inventory')
                            <a href="{{ route('supplier-bill.create', $inventory) }}" class="btn btn-secondary btn-sm">
                                <i class="fa fa-edit"></i>
                                Edit</a>
                        @endcan
                        @if($inventory->status != 'APPROVED')
                            <a href="#" wire:click="approve({{$inventory->id}})" class="btn btn-success btn-sm">
                                <i class="fa fa-check"></i>
                                Approve</a>

                            @can('delete inventory')
                                <button type="button" class="btn btn-danger btn-sm"
                                        wire:click="deleteConfirm({{$inventory->id}})"
                                        data-coreui-toggle="modal" data-coreui-target="#deleteModalinventory">
                                    <i class="fa fa-trash-alt"></i> Delete
                                </button>
                            @endcan
                        @endif
                    </td>
                </tr>
            @empty
                <tr>
                    <td colspan="10" class="text-center py-4 text-muted">
                        No bills found. Create a new expense bill or goods costing bill.
                    </td>
                </tr>
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $inventories->links() }}
        </div>
    </div>

    {{-- ==================== MOBILE CARD LIST (visible only on mobile) ==================== --}}
    <div class="d-md-none">
        @forelse($inventories as $inventory)
            @php
                $billAmount = $inventory->amount();
                $billBalance = $inventory->balance();
            @endphp
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    {{-- Top row: type badge + payment status --}}
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <div>
                            @if($inventory->is_goods_costing)
                                <x-badge color="info" icon="fa-boxes">Goods Costing</x-badge>
                                @if($inventory->linkedReceiveEntries->count() > 0)
                                    <small class="text-muted ms-1">
                                        ({{ $inventory->linkedReceiveEntries->count() }} RE)
                                    </small>
                                @endif
                            @else
                                <x-badge color="secondary" icon="fa-file-invoice-dollar">Expense</x-badge>
                            @endif
                        </div>
                        <div>
                            @if($billAmount > 0)
                                @if($billBalance == 0)
                                    <x-badge color="success">PAID</x-badge>
                                @elseif($billBalance > 0 && $billBalance < $billAmount)
                                    <x-badge color="info">PARTIAL</x-badge>
                                @else
                                    <x-badge color="danger">UNPAID</x-badge>
                                @endif
                            @else
                                <x-badge color="secondary">-</x-badge>
                            @endif
                        </div>
                    </div>

                    {{-- Supplier name + ID --}}
                    <div class="fw-bold mb-1" style="font-size: 0.95rem;">
                        {{ $inventory->supplier->name ?? '-' }}
                    </div>
                    <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem;">
                        <span><i class="fa fa-hashtag me-1"></i>{{ $inventory->id }}</span>
                        @if($inventory->supplier_invoice_no)
                            <span><i class="fa fa-file-alt me-1"></i>{{ $inventory->supplier_invoice_no }}</span>
                        @endif
                        <span><i class="fa fa-calendar me-1"></i>{{ $inventory->created_at->format('d-m-Y') }}</span>
                    </div>

                    {{-- Amount row --}}
                    <div class="d-flex justify-content-between align-items-center py-2 border-top" style="font-size: 0.85rem;">
                        <div>
                            <span class="text-muted">Total</span>
                            <span class="fw-bold ms-1">{{ number_format($billAmount, 2) }}</span>
                        </div>
                        <div>
                            <span class="text-muted">Balance</span>
                            @if($billBalance > 0)
                                <span class="fw-bold text-danger ms-1">{{ number_format($billBalance, 2) }}</span>
                            @else
                                <span class="fw-bold text-success ms-1">0.00</span>
                            @endif
                        </div>
                    </div>

                    {{-- Actions --}}
                    <div class="d-flex flex-wrap gap-1 pt-2 border-top">
                        <a href="{{ route('supplier-bill.show', $inventory) }}" class="btn btn-primary btn-sm flex-fill">
                            <i class="fa fa-eye"></i> Show
                        </a>
                        @can('edit inventory')
                            <a href="{{ route('supplier-bill.create', $inventory) }}" class="btn btn-secondary btn-sm flex-fill">
                                <i class="fa fa-edit"></i> Edit
                            </a>
                        @endcan
                        @if($inventory->status != 'APPROVED')
                            <a href="#" wire:click="approve({{$inventory->id}})" class="btn btn-success btn-sm flex-fill">
                                <i class="fa fa-check"></i> Approve
                            </a>
                            @can('delete inventory')
                                <button type="button" class="btn btn-danger btn-sm flex-fill"
                                        wire:click="deleteConfirm({{$inventory->id}})"
                                        data-coreui-toggle="modal" data-coreui-target="#deleteModalinventory">
                                    <i class="fa fa-trash-alt"></i> Delete
                                </button>
                            @endcan
                        @endif
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center py-4 text-muted">
                    No bills found. Create a new expense bill or goods costing bill.
                </div>
            </div>
        @endforelse

        <div class="mt-2">
            {{ $inventories->links() }}
        </div>
    </div>

    <!-- Delete Modal -->
    <div wire:ignore.self class="modal fade" id="deleteModalinventory" data-backdrop="false" tabindex="-1" role="dialog"
         aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Delete Confirm </h5>
                </div>
                <div class="modal-body">
                    <p>Are you sure want to delete this bill?</p>
                    @if($delete_confirm && $delete_confirm->is_goods_costing)
                        <div class="alert alert-warning py-2">
                            <small>
                                <i class="fa fa-exclamation-triangle me-1"></i>
                                This will unlink all associated receive entries from this costing bill.
                            </small>
                        </div>
                    @endif
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

    @push('scripts')
        <script>
            Livewire.on('closeModal', () => {
                $('#modal-inventory').modal('hide')
            })
        </script>
    @endpush
</div>
