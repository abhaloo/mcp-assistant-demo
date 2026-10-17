<div>
    {{-- Loading Overlay --}}
    <x-loading-overlay target="generateReport, exportExcel" message="Generating report..." />

    <div class="row mb-3">
        <div class="col">
            <h1 class="h3"><strong>Stock Movement Report</strong></h1>
        </div>
        <div class="col text-end">
            @if($reportGenerated)
                <x-loading-button
                    wire:click="exportExcel"
                    target="exportExcel"
                    label="Export to Excel"
                    icon="fa-file-excel"
                    loading-label="Exporting..."
                    class="btn btn-success"
                />
            @endif
        </div>
    </div>

    <div class="card card-body border-0 shadow mb-4">
        {{-- Filters --}}
        <div class="row g-3">
            <div class="col-6 col-md-2">
                <label class="form-label">Start Date</label>
                <input type="date" class="form-control" wire:model.live="start_date">
            </div>
            <div class="col-6 col-md-2">
                <label class="form-label">End Date</label>
                <input type="date" class="form-control" wire:model.live="end_date">
            </div>
            <div class="col-6 col-md-3">
                <label class="form-label">Category</label>
                <select class="form-select" wire:model.live="category_id">
                    <option value="">All Categories</option>
                    @foreach($categories as $category)
                        <option value="{{ $category->id }}">{{ $category->name }}</option>
                    @endforeach
                </select>
            </div>
            <div class="col-6 col-md-3">
                <label class="form-label">Warehouse</label>
                <select class="form-select" wire:model.live="warehouse_id">
                    <option value="">All Warehouses</option>
                    @foreach($warehouses as $warehouse)
                        <option value="{{ $warehouse->id }}">{{ $warehouse->name }}</option>
                    @endforeach
                </select>
            </div>
            <div class="col-md-2 d-flex align-items-end">
                <x-loading-button
                    wire:click="generateReport"
                    target="generateReport"
                    label="Generate"
                    icon="fa-play"
                    loading-label="Generating..."
                    class="btn btn-primary w-100"
                />
            </div>
        </div>
    </div>

    {{-- Report Table --}}
    @if($reportGenerated)
    <div class="card border-0 shadow">
        <div class="card-header bg-dark text-white">
            <strong>STOCK AS AT {{ strtoupper(date('d F Y', strtotime($end_date))) }}</strong>
        </div>
        <div class="card-body p-0">
            <div class="table-responsive d-none d-md-block">
                <table class="table table-bordered table-sm mb-0">
                    <thead class="table-dark">
                        <tr>
                            <th style="width: 40px;">#</th>
                            <th>Description</th>
                            <th style="width: 60px;">GSM</th>
                            <th style="width: 80px;">Size</th>
                            <th>Supplier</th>
                            <th style="width: 60px;">Unit</th>
                            <th class="text-end" style="width: 100px;">Opening</th>
                            <th class="text-end" style="width: 80px;">IN</th>
                            <th class="text-end" style="width: 80px;">OUT</th>
                            <th class="text-end" style="width: 100px;">Balance</th>
                            <th class="text-end" style="width: 110px;">Unit Price</th>
                            <th class="text-end" style="width: 130px;">Total Value</th>
                        </tr>
                    </thead>
                    <tbody>
                        @foreach($reportData as $row)
                            @if($row['type'] === 'category_header')
                                <tr class="table-secondary">
                                    <td colspan="12" class="fw-bold">
                                        {{ strtoupper($row['category']) }}
                                    </td>
                                </tr>
                            @elseif($row['type'] === 'category_total')
                                <tr class="table-light">
                                    <td colspan="11" class="text-end fw-bold">
                                        {{ $row['category'] }} Subtotal:
                                    </td>
                                    <td class="text-end fw-bold">
                                        {{ number_format($row['total'], 2) }}
                                    </td>
                                </tr>
                            @else
                                <tr>
                                    <td class="text-center">{{ $row['index'] ?? '' }}</td>
                                    <td>{{ $row['name'] }}</td>
                                    <td>{{ $row['gsm'] }}</td>
                                    <td>{{ $row['size'] }}</td>
                                    <td>{{ $row['supplier'] }}</td>
                                    <td>{{ $row['unit'] }}</td>
                                    <td class="text-end">{{ number_format($row['opening_stock']) }}</td>
                                    <td class="text-end text-success">
                                        {{ $row['in'] > 0 ? number_format($row['in']) : '' }}
                                    </td>
                                    <td class="text-end text-danger">
                                        {{ $row['out'] > 0 ? number_format($row['out']) : '' }}
                                    </td>
                                    <td class="text-end fw-bold">{{ number_format($row['balance']) }}</td>
                                    <td class="text-end">{{ number_format($row['unit_price'], 2) }}</td>
                                    <td class="text-end">{{ number_format($row['total_value'], 2) }}</td>
                                </tr>
                            @endif
                        @endforeach
                    </tbody>
                    <tfoot class="table-dark">
                        <tr>
                            <td colspan="11" class="text-end fw-bold">GRAND TOTAL:</td>
                            <td class="text-end fw-bold">{{ number_format($grandTotal, 2) }}</td>
                        </tr>
                    </tfoot>
                </table>
            </div>

            {{-- Mobile View --}}
            <div class="d-md-none p-3">
                @foreach($reportData as $row)
                    @if($row['type'] === 'category_header')
                        <div class="bg-secondary text-white p-2 fw-bold mb-1">{{ strtoupper($row['category']) }}</div>
                    @elseif($row['type'] === 'category_total')
                        <div class="d-flex justify-content-between p-2 bg-light rounded mb-2">
                            <span class="fw-bold">{{ $row['category'] }} Subtotal</span>
                            <span class="fw-bold">{{ number_format($row['total'], 2) }}</span>
                        </div>
                    @else
                        <div class="card border-0 shadow-sm mb-2">
                            <div class="card-body p-3">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <span class="fw-bold">{{ $row['name'] }}</span>
                                    <span class="fw-bold">{{ number_format($row['balance']) }}</span>
                                </div>
                                <div class="small text-muted mb-2">
                                    @if($row['gsm'])<span class="me-2">GSM: {{ $row['gsm'] }}</span>@endif
                                    @if($row['size'])<span class="me-2">Size: {{ $row['size'] }}</span>@endif
                                    @if($row['supplier'])<span class="me-2">{{ $row['supplier'] }}</span>@endif
                                    @if($row['unit'])<span>{{ $row['unit'] }}</span>@endif
                                </div>
                                <div class="border-top pt-2 d-flex justify-content-between small">
                                    <span>Opening: {{ number_format($row['opening_stock']) }}</span>
                                    <span class="text-success">IN: {{ $row['in'] > 0 ? number_format($row['in']) : '0' }}</span>
                                    <span class="text-danger">OUT: {{ $row['out'] > 0 ? number_format($row['out']) : '0' }}</span>
                                    <span class="fw-bold">Bal: {{ number_format($row['balance']) }}</span>
                                </div>
                                <div class="border-top mt-2 pt-2 d-flex justify-content-between small">
                                    <span>Unit Price: {{ number_format($row['unit_price'], 2) }}</span>
                                    <span class="fw-bold">Total: {{ number_format($row['total_value'], 2) }}</span>
                                </div>
                            </div>
                        </div>
                    @endif
                @endforeach
                <div class="d-flex justify-content-between p-2 bg-dark text-white rounded">
                    <span class="fw-bold">GRAND TOTAL</span>
                    <span class="fw-bold">{{ number_format($grandTotal, 2) }}</span>
                </div>
            </div>
        </div>
    </div>
    @else
        <div class="alert alert-info">
            <i class="fa fa-info-circle me-2"></i>
            Select date range and click "Generate" to view stock movements.
        </div>
    @endif
</div>
