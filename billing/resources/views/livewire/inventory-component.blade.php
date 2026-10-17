<div>
    <div class="d-flex flex-column flex-md-row align-items-md-center justify-content-between mb-3">
        <h1 class="h3 mb-2 mb-md-0">Manage <strong>Inventories</strong></h1>
        <div class="d-flex flex-wrap gap-2">
            <a href="{{ route('inventory.create', ['type' => 'Issue']) }}"
               class="btn btn-sm btn-success">
                <span class="fa fa-dolly"></span> Issue
            </a>
            <a href="{{ route('inventory.create', ['type' => 'Receive']) }}"
               class="btn btn-sm btn-primary">
                <span class="fa fa-clipboard-check"></span> Receive
            </a>
            <a href="{{ route('inventory.create', ['type' => 'Adjustment']) }}"
               class="btn btn-sm btn-warning">
                <span class="fa fa-edit"></span> Adjustment
            </a>
            <a href="{{ route('inventory.create', ['type' => 'Transfer']) }}"
               class="btn btn-sm btn-secondary">
                <span class="fa fa-exchange"></span> Transfer
            </a>
        </div>
    </div>

    <div class="mb-3">
        <form class="col-12 col-md-6 col-lg-3 col-xl-4 px-0">
            <div class="input-group">
                <span class="input-group-text"><i class="fa fa-search"></i></span>
                <input type="text" wire:model.live="search_keyword" class="form-control"
                       placeholder="Search by anything">
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
                <th class="border-gray-200">Warehouse</th>
                <th class="border-gray-200">Type</th>
                <th class="border-gray-200">From</th>
                <th class="border-gray-200">Post Date</th>
                <th class="border-gray-200">Posted By</th>
                <th class="border-gray-200">Job</th>
                @can('view inventory costing')
                    <th class="border-gray-200 text-end">Total Value</th>
                @endcan
                <th class="border-gray-200">Status</th>
                <th class="border-gray-200 text-center">Action</th>
            </tr>
            </thead>
            <tbody>
            @forelse($inventories as $inventory)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $inventory->id }}</td>
                    <td>{{ $inventory->warehouse->name ?? '-' }}</td>
                    <td>
                        @if($inventory->type == 'Receive')
                            <x-badge color="primary">{{ $inventory->type }}</x-badge>
                        @elseif($inventory->type == 'Transfer')
                            <x-badge color="secondary">{{ $inventory->type }}</x-badge>
                        @else
                            <x-badge color="success">{{ $inventory->type }}</x-badge>
                        @endif
                    </td>
                    @if($inventory->type == 'Transfer')
                        <td>{{ $inventory->from?->name }} <span
                                class="fa fa-arrow-right">>></span> {{ $inventory->to?->name }}</td>
                    @else
                        <td>{{ $inventory->supplier->name ?? '-' }}</td>
                    @endif
                    <td>{{ $inventory->created_at->format('d-m-Y') }}</td>
                    <td>
                        <span title="{{ $inventory->created_at->format('d-m-Y H:i') }}">
                            {{ $inventory->createdBy->name ?? 'System' }}
                        </span>
                    </td>
                    <td>
                        @if($inventory->work_order_id)
                            <a target="_blank"
                               href="{{ route('work-order.show', $inventory->work_order_id) }}"> {{$inventory->workOrder->work_number ?? $inventory->workOrder?->id}}</a>
                        @else
                            -
                        @endif
                    </td>
                    @can('view inventory costing')
                        <td class="text-end"><strong>{{ number_format($inventory->amount(), 2) }}</strong></td>
                    @endcan
                    <td>
                        @if($inventory->status == 'APPROVED')
                            <x-badge color="success">APPROVED</x-badge>
                        @else
                            <x-badge color="info">WAITING APPROVAL</x-badge>
                        @endif
                    </td>
                    <td class="text-end">
                        <a href="{{ route('inventory.show', $inventory) }}" class="btn btn-primary btn-sm">
                            <i class="fa fa-eye"></i>
                            Show</a>
                        @can('edit inventory')
                            <a href="{{ route('inventory.create', $inventory) }}" class="btn btn-secondary btn-sm">
                                <i class="fa fa-edit"></i>
                                Edit</a>
                        @endcan
                        @if($inventory->status != 'APPROVED')
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
                    <td colspan="11" class="text-center py-4 text-muted">
                        No inventory entries found.
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
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    {{-- Top row: type badge + status --}}
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <div>
                            @if($inventory->type == 'Receive')
                                <x-badge color="primary">{{ $inventory->type }}</x-badge>
                            @elseif($inventory->type == 'Transfer')
                                <x-badge color="secondary">{{ $inventory->type }}</x-badge>
                            @else
                                <x-badge color="success">{{ $inventory->type }}</x-badge>
                            @endif
                        </div>
                        <div>
                            @if($inventory->status == 'APPROVED')
                                <x-badge color="success">APPROVED</x-badge>
                            @else
                                <x-badge color="info">WAITING APPROVAL</x-badge>
                            @endif
                        </div>
                    </div>

                    {{-- Source/destination --}}
                    <div class="fw-bold mb-1" style="font-size: 0.95rem;">
                        @if($inventory->type == 'Transfer')
                            {{ $inventory->from?->name }} <i class="fa fa-arrow-right small"></i> {{ $inventory->to?->name }}
                        @else
                            {{ $inventory->supplier->name ?? '-' }}
                        @endif
                    </div>

                    {{-- Meta --}}
                    <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem;">
                        <span><i class="fa fa-hashtag me-1"></i>{{ $inventory->id }}</span>
                        <span><i class="fa fa-warehouse me-1"></i>{{ $inventory->warehouse->name ?? '-' }}</span>
                        <span><i class="fa fa-calendar me-1"></i>{{ $inventory->created_at->format('d-m-Y') }}</span>
                        <span><i class="fa fa-user me-1"></i>{{ $inventory->createdBy->name ?? 'System' }}</span>
                        @if($inventory->work_order_id)
                            <a href="{{ route('work-order.show', $inventory->work_order_id) }}" target="_blank" class="text-decoration-none" style="color: #475569;">
                                <i class="fa fa-briefcase me-1"></i>Job {{ $inventory->workOrder->work_number ?? $inventory->workOrder?->id }}
                            </a>
                        @endif
                    </div>

                    {{-- Total value (if permitted) --}}
                    @can('view inventory costing')
                        <div class="py-2 border-top" style="font-size: 0.85rem;">
                            <span class="text-muted">Total Value</span>
                            <span class="fw-bold ms-1">{{ number_format($inventory->amount(), 2) }}</span>
                        </div>
                    @endcan

                    {{-- Actions --}}
                    <div class="d-flex flex-wrap gap-1 pt-2 border-top">
                        <a href="{{ route('inventory.show', $inventory) }}" class="btn btn-primary btn-sm flex-fill">
                            <i class="fa fa-eye"></i> Show
                        </a>
                        @can('edit inventory')
                            <a href="{{ route('inventory.create', $inventory) }}" class="btn btn-secondary btn-sm flex-fill">
                                <i class="fa fa-edit"></i> Edit
                            </a>
                        @endcan
                        @if($inventory->status != 'APPROVED')
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
                    No inventory entries found.
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

    @push('scripts')
        <script>
            Livewire.on('closeModal', () => {
                $('#modal-inventory').modal('hide')
            })
        </script>
    @endpush
</div>
