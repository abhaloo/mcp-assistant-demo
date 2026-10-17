<div>
    <div class="row">
        <div class="col">
            <h2 class="h3 mb-3">Payment <strong>Report</strong></h2>
        </div>
    </div>
    <div class="table-settings mb-4">
        <div class="row align-items-center justify-content-between mb-2">
            <form class="col col-md-10">
                <div class="row g-2">
                    <div class="col-6 col-md-auto input-group me-2 fmxw-400">
                        <span class="input-group-text">
                            Method
                        </span>
                        <select wire:model.live="method" name="method" id="method" class="form-select">
                            <option value="">Choose ...</option>
                            @forelse($accounts as $account)
                                <option value="{{ $account->id }}"> {{ $account->name }}</option>
                            @empty
                            @endforelse
                        </select>
                    </div>
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
                <th class="border-gray-200">Payment By</th>
                <th class="border-gray-200">Method</th>
                <th class="border-gray-200">Paid Date</th>
                <th class="border-gray-200">Reference</th>
                <th class="border-gray-200 text-end">Amount</th>
            </tr>
            </thead>
            <tbody>
            @forelse($payments as $payment)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $payment->id }}</td>
                    <td>{{ $payment->bill->customer->name }}</td>
                    <td>{{ $payment->account->name }}</td>
                    <td>{{ $payment->created_at->format('d-m-Y') }}</td>
                    <td>{{ $payment->bill->controlNumber->control_number }}</td>
                    <td class="text-end">{{ number_format($payment->debit, 2) }}</td>
                </tr>
            @empty
            @endforelse
            </tbody>
            <tfoot>
            <tr>
                <td colspan="10" style="text-align: right">
                    <b>Total</b> : {{ number_format($payments->sum('debit'), 2) }}
                </td>
            </tr>
            </tfoot>
        </table>
    </div>

    {{-- Mobile View --}}
    <div class="d-md-none">
        @forelse($payments as $payment)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span class="badge bg-info">{{ $payment->account->name }}</span>
                        <span class="fw-bold text-success">{{ number_format($payment->debit, 2) }}</span>
                    </div>
                    <div class="fw-bold mb-1">{{ $payment->bill->customer->name }}</div>
                    <div class="small text-muted">
                        <i class="fa fa-hashtag me-1"></i>{{ $payment->id }}
                        <i class="fa fa-calendar ms-2 me-1"></i>{{ $payment->created_at->format('d-m-Y') }}
                        <i class="fa fa-receipt ms-2 me-1"></i>{{ $payment->bill->controlNumber->control_number }}
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 text-center py-4 text-muted">
                    No payments found.
                </div>
            </div>
        @endforelse

        @if($payments->count())
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 bg-light rounded">
                    <div class="d-flex justify-content-between align-items-center">
                        <span class="fw-bold">Total</span>
                        <span class="fw-bold text-success">{{ number_format($payments->sum('debit'), 2) }}</span>
                    </div>
                </div>
            </div>
        @endif
    </div>
</div>
