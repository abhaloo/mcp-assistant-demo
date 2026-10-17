<div
    data-mcp-page-context-owner="jobs"
    data-mcp-context-route="report.work-order"
    data-mcp-context-kind="report"
    data-mcp-context-resource="work_order"
    data-mcp-context-id="jobs:report"
>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Jobs <strong>Report</strong></h1>
        </div>
    </div>
    <div class="table-settings mb-4">
        <div class="row align-items-center justify-content-between mb-2">
            <form class="col col-md-10">
                <div class="row g-2">
                    <div class="col-6 col-md-auto input-group me-2 fmxw-400">
                        <span class="input-group-text">
                            Status
                        </span>
                        <select wire:model.live="status" name="status" id="status" class="form-select">
                            <option value="">Choose ...</option>
                            <option value="CREATED">CREATED</option>
                            <option value="IN PROGRESS">IN PROGRESS</option>
                            <option value="PENDING MOVE APPROVAL">PENDING MOVE APPROVAL</option>
                            <option value="FINISHED">FINISHED</option>
                            <option value="NOT INVOICED">NOT INVOICED</option>
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
                <th class="border-gray-200">Job Id</th>
                <th class="border-gray-200">Bill Id</th>
                <th class="border-gray-200">Customer</th>
                <th class="border-gray-200">Deadline</th>
                <th class="border-gray-200">Delivery</th>
                <th class="border-gray-200">Status</th>
            </tr>
            </thead>
            <tbody>
            @forelse($work_orders as $work_order)
                <tr data-mcp-context-job-id="{{ $work_order->id }}">
                    <td>{{ $work_order->work_number ?? $work_order->id}}</td>
                    <td>{{ $work_order->bill->id ?? 'None'}}</td>
                    <td>{{ $work_order->customer->name?? '-' }}</td>
                    <td>{{ $work_order->delivery ?? 'None' }}</td>
                    <td>{{ $work_order->actual_delivery ?? 'None' }}</td>
                    <td>{!! $work_order->getStatus() !!}</td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>
    </div>

    <div class="d-md-none">
        @forelse($work_orders as $work_order)
            <div class="card border-0 shadow-sm mb-2" data-mcp-context-job-id="{{ $work_order->id }}">
                <div class="card-body p-3">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span class="small text-muted">Job #{{ $work_order->work_number ?? $work_order->id }}</span>
                        <span>{!! $work_order->getStatus() !!}</span>
                    </div>
                    <div class="fw-bold mb-1">{{ $work_order->customer->name ?? '-' }}</div>
                    <div class="small text-muted">
                        <i class="fa fa-hashtag me-1"></i>Bill: {{ $work_order->bill->id ?? 'None' }}
                        <span class="ms-2"><i class="fa fa-calendar me-1"></i>{{ $work_order->delivery ?? 'None' }}</span>
                        <span class="ms-2"><i class="fa fa-truck me-1"></i>{{ $work_order->actual_delivery ?? 'None' }}</span>
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
    </div>

    <div class="mt-3">
        {{ $work_orders->links() }}
    </div>
</div>
