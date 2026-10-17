<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Inventory Entry <strong>#{{$inventory->id}}</strong></h1>
        </div>
        <div class="col">
            <div class="float-end">
                @can('add journal entry')
                    @if($inventory->status == 'APPROVED' && $inventory->type == 'Receive')
                        <button class="btn btn-sm btn-outline-primary d-inline-flex align-items-center"
                                wire:click="openAttachBillModal">
                            <i class="fa fa-link me-2"></i>
                            {{ $inventory->supplierBill ? 'Change Bill' : 'Attach Bill' }}
                        </button>
                    @endif
                @endcan

                <a href="{{ route('inventory.index') }}"
                   class="btn btn-sm btn-secondary d-inline-flex align-items-center ms-2">
                    <i class="fa fa-chevron-left me-2"></i>
                    Back
                </a>
            </div>
        </div>
    </div>

    <div class="card card-body border-0 shadow table-wrapper table-responsive">
        <table class="table bg-gray-100">
            <tr>
                <th>Entry Id</th>
                <th>Warehouse</th>
                <th>Type</th>
                <th>Supplier</th>
                <th>Reference</th>
                <th>Created At</th>
                <th>Description</th>
            </tr>

            <tbody>
            <tr>
                <td>{{ $inventory->id }}</td>
                <td>{{ $inventory->warehouse->name ?? '-' }}</td>
                <td>{{ $inventory->type }}</td>
                <td>{{ $inventory->supplier->name ?? 'N/A' }}</td>
                <td>{{ $inventory->reference }}</td>
                <td>{{ $inventory->created_at->format('d-m-Y H:i:s')}}</td>
                <td>{{ $inventory->description}}</td>
            </tr>
            @if($inventory->type == 'Receive')
            <tr>
                <th>Supplier Bill</th>
                <td colspan="6">
                    @if($inventory->supplierBill)
                        <x-badge color="success">Bill #{{ $inventory->supplierBill->id }}</x-badge>
                        @if($inventory->supplierBill->reference)
                            <span class="ms-1 text-muted">{{ $inventory->supplierBill->reference }}</span>
                        @endif
                        @if($inventory->status == 'APPROVED')
                            <button class="btn btn-sm btn-outline-danger ms-2"
                                    wire:click="detachBill"
                                    onclick="return confirm('Are you sure you want to detach this bill?')">
                                <i class="fa fa-unlink"></i> Detach
                            </button>
                        @endif
                    @else
                        <span class="text-muted">Not attached</span>
                    @endif
                </td>
            </tr>
            @endif
            </tbody>

        </table>
        <h5 class="mt-3">Entry Items</h5>
        <table class="table table-sm">
            <tr class="bg-gray-100">
                <th>Name</th>
                <th>Description</th>
                @can('view inventory costing')
                    <th>Cost</th>
                @endcan
                <th>Quantity</th>
                @can('view inventory costing')
                    <th class="text-end">Subtotal</th>
                @endcan
            </tr>
            <tbody>
            @forelse($inventory->inventoryItems as $item)
                <tr>
                    <td>{{ $item->product->name ?? 'None' }}</td>
                    <td>{{ $item->description }}</td>
                    @can('view inventory costing')
                        <td>{{ number_format($item->cost, 2) }}</td>
                    @endcan
                    <td>{{ $item->quantity }}</td>
                    @can('view inventory costing')
                        <td class="text-end">{{ number_format($item->quantity * $item->cost, 2) }}</td>
                    @endcan
                </tr>
            @empty
            @endforelse
            @can('view inventory costing')
                <tr class="bg-gray-100">
                    <th colspan="4"></th>
                    <th class="fw-bold text-end text-success">{{ number_format($inventory_value, 2) }}</th>
                </tr>
            @endcan
            </tbody>
        </table>
    </div>

    <!-- Attach Bill Modal -->
    @if($showAttachBillModal)
    <div class="modal fade show d-block" tabindex="-1" style="background: rgba(0,0,0,0.5);">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">
                        <i class="fa fa-link me-2"></i>Attach Supplier Bill
                    </h5>
                    <button type="button" class="btn-close" wire:click="closeAttachBillModal"></button>
                </div>
                <div class="modal-body">
                    @if(count($availableBills) > 0)
                        <select wire:model.live="selectedBillId" class="form-select">
                            <option value="">-- Select Bill --</option>
                            @foreach($availableBills as $bill)
                                <option value="{{ $bill->id }}">
                                    Bill #{{ $bill->id }}
                                    @if($bill->reference) - {{ $bill->reference }} @endif
                                    ({{ $bill->inventory_date?->format('d-m-Y') ?? 'No date' }})
                                </option>
                            @endforeach
                        </select>
                    @else
                        <div class="alert alert-info mb-0">
                            <i class="fa fa-info-circle me-2"></i>
                            No available bills found for this supplier. Create a supplier bill first.
                        </div>
                    @endif
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" wire:click="closeAttachBillModal">Cancel</button>
                    @if(count($availableBills) > 0)
                        <button type="button" class="btn btn-primary" wire:click="attachBill" @if(!$selectedBillId) disabled @endif>
                            Attach
                        </button>
                    @endif
                </div>
            </div>
        </div>
    </div>
    @endif
    <!-- End of Attach Bill Modal -->
</div>
