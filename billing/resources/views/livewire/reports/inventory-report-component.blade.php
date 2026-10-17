<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-4 mb-md-0">
            <h2 class="h4">Inventory Report</h2>
        </div>
    </div>
    <div class="table-settings mb-4">
        <div class="row align-items-center justify-content-between mb-2">
            <form class="col col-md-8">
                <div class="row g-2">
                    <div class="col-6 col-md-auto input-group me-2 fmxw-400">
                        <span class="input-group-text">
                            Start Date
                        </span>
                        <input type="date" wire:model.live="from_date" class="form-control" placeholder="Search here">
                    </div>
                    <div class="col-6 col-md-auto input-group me-2 fmxw-400">
                        <span class="input-group-text">
                            End Date
                        </span>
                        <input type="date" wire:model.live="to_date" class="form-control" placeholder="Search here">
                    </div>
                    <div class="col-6 col-md-auto input-group me-2 fmxw-400">
                        <span class="input-group-text">
                            Type
                        </span>
                        <select wire:model.live="type" class="form-control">
                            <option value="">-- Choose Type --</option>
                            <option value="Receive">Receive</option>
                            <option value="Issue">Issue</option>
                            <option value="Transfer">Transfer</option>
                        </select>
                    </div>
                    <div class="col-12 col-md-auto input-group">
                        <button type="button" class="btn btn-info" wire:click="filter">
                            <i class="fa fa-filter"></i> Filter
                        </button>
                    </div>
                </div>
            </form>

            <div class="col">
                <div class="float-end">
                    <button type="button" wire:click="exportExcel" class="btn btn-primary">
                        <i class="fa fa-file-excel"></i> Excel
                    </button>
                </div>
            </div>
        </div>
    </div>
    <div class="card card-body border-0 shadow table-wrapper table-responsive d-none d-md-block">
        <table class="table table-hover table-sm">
            <thead>
            <tr>
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">Type</th>
                <th class="border-gray-200">Inventory Id</th>
                <th class="border-gray-200">From</th>
                <th class="border-gray-200">Post Date</th>
                <th class="border-gray-200">Reference</th>
                <th class="border-gray-200 text-end">Total</th>
            </tr>
            </thead>
            <tbody>
            @forelse($inventories as $inventory)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $inventory->id }}</td>
                    <td>{{ $inventory->type }}</td>
                    <td>{{ $inventory->supplier->name?? '-' }}</td>
                    <td>{{ $inventory->created_at->format('d-m-Y') }}</td>
                    <td>{{ $inventory->reference }}</td>
                    <td class="text-end">{{ number_format($inventory->amount(), 2) }}</td>
                </tr>
            @empty
            @endforelse
            </tbody>
            <tfoot>
            <tr>
                <td colspan="7" style="text-align: right">
                    <b>Total</b> : {{ number_format($total, 2) }}
                </td>
            </tr>
            </tfoot>
        </table>
    </div>

    {{-- Mobile View --}}
    <div class="d-md-none">
        @forelse($inventories as $inventory)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span class="badge bg-info">{{ $inventory->type }}</span>
                        <span class="fw-bold text-success">{{ number_format($inventory->amount(), 2) }}</span>
                    </div>
                    <div class="fw-bold mb-1">{{ $inventory->supplier->name ?? '-' }}</div>
                    <div class="small text-muted">
                        <i class="fa fa-hashtag me-1"></i>{{ $inventory->id }}
                        <i class="fa fa-calendar ms-2 me-1"></i>{{ $inventory->created_at->format('d-m-Y') }}
                        @if($inventory->reference)
                            <i class="fa fa-file-alt ms-2 me-1"></i>{{ $inventory->reference }}
                        @endif
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 text-center py-4 text-muted">
                    No inventory records found.
                </div>
            </div>
        @endforelse

        @if($inventories->count())
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 bg-light rounded">
                    <div class="d-flex justify-content-between align-items-center">
                        <span class="fw-bold">Total</span>
                        <span class="fw-bold text-success">{{ number_format($total, 2) }}</span>
                    </div>
                </div>
            </div>
        @endif
    </div>
</div>
