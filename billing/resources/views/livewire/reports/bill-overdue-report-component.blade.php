<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Bills Overdue <strong>Report</strong></h1>
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
                <th class="border-gray-200">Bill Id</th>
                <th class="border-gray-200">Bill For</th>
                <th class="border-gray-200">Issue Date</th>
                <th class="border-gray-200">Reference</th>
                <th class="border-gray-200 text-end">Total</th>
                <th class="border-gray-200 text-end">Balance</th>
                <th class="border-gray-200">Status</th>
            </tr>
            </thead>
            <tbody>
            @forelse($bills as $bill)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $bill->id }}</td>
                    <td>{{ $bill->customer->name }}</td>
                    <td>{{ $bill->created_at->format('d-m-Y') }}</td>
                    <td>{{ $bill->controlNumber->control_number }}</td>
                    <td class="text-end">{{ number_format($bill->amount(), 2) }}</td>
                    <td class="text-end">{{ number_format($bill->balance(), 2) }}</td>
                    <td>{!! $bill->balanceStatus() !!}</td>
                </tr>
            @empty
            @endforelse
            </tbody>
            <tfoot>
            <tr>
                <td colspan="7" style="text-align: right">
                    Total: {{ number_format($total, 2) }}
                </td>
            </tr>
            </tfoot>
        </table>
    </div>

    <div class="d-md-none">
        @forelse($bills as $bill)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span class="small text-muted">Bill #{{ $bill->id }}</span>
                        <span>{!! $bill->balanceStatus() !!}</span>
                    </div>
                    <div class="fw-bold mb-1">{{ $bill->customer->name }}</div>
                    <div class="small text-muted mb-2">
                        <i class="fa fa-calendar me-1"></i>{{ $bill->created_at->format('d-m-Y') }}
                        <span class="ms-2"><i class="fa fa-hashtag me-1"></i>{{ $bill->controlNumber->control_number }}</span>
                    </div>
                    <div class="border-top pt-2 d-flex justify-content-between">
                        <div>
                            <span class="small text-muted">Total</span><br>
                            <span>{{ number_format($bill->amount(), 2) }}</span>
                        </div>
                        <div class="text-end">
                            <span class="small text-muted">Balance</span><br>
                            <span class="{{ $bill->balance() > 0 ? 'text-danger' : 'text-success' }} fw-bold">{{ number_format($bill->balance(), 2) }}</span>
                        </div>
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 text-center py-4 text-muted">
                    No records found.
                </div>
            </div>
        @endforelse

        @if($bills->count() > 0)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 d-flex justify-content-between align-items-center">
                    <span class="fw-bold">Total</span>
                    <span class="fw-bold">{{ number_format($total, 2) }}</span>
                </div>
            </div>
        @endif
    </div>
</div>
