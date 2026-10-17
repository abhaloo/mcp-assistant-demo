{{-- Costing details card --}}
<div class="card border-0 shadow-sm mb-3">
    <div class="card-body p-3">
        <h6 class="fw-bold text-muted mb-3" style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;">
            Costing Details
        </h6>

        <div class="row g-2" style="font-size: 0.85rem;">
            <div class="col-6">
                <span class="text-muted d-block small">Currency</span>
                <span>{{ $inventory->currency->name ?? 'USD' }}</span>
            </div>
            <div class="col-6">
                <span class="text-muted d-block small">Exchange Rate</span>
                <span>{{ number_format($inventory->exchange_rate ?? 1, 2) }}</span>
            </div>
            <div class="col-6 mt-2">
                <span class="text-muted d-block small">Supplier Invoice</span>
                <span>{{ $inventory->supplier_invoice_no ?? 'N/A' }}</span>
            </div>
            <div class="col-6 mt-2">
                <span class="text-muted d-block small">Invoice Total ({{ $currencySymbol }})</span>
                <span>{{ number_format($inventory->invoice_value_foreign ?? 0, 2) }}</span>
            </div>
        </div>

        <div class="mt-3 pt-2 border-top" style="font-size: 0.85rem;">
            <div class="d-flex justify-content-between mb-1">
                <span class="text-muted">Clearing Charges</span>
                <span>{{ number_format($inventory->clearing_charges ?? 0, 2) }}</span>
            </div>
            <div class="d-flex justify-content-between mb-1">
                <span class="text-muted">Freight Charges</span>
                <span>{{ number_format($inventory->freight_charges ?? 0, 2) }}</span>
            </div>
            <div class="d-flex justify-content-between mb-1">
                <span class="text-muted">Other Charges</span>
                <span>{{ number_format($inventory->other_charges ?? 0, 2) }}</span>
            </div>
            <div class="d-flex justify-content-between pt-2 border-top fw-bold text-success">
                <span>Total Landed Cost (USD)</span>
                <span>{{ number_format($inventory->total_landed_cost_tzs ?? 0, 2) }}</span>
            </div>
        </div>
    </div>
</div>

{{-- Linked receive entries --}}
@if($inventory->linkedReceiveEntries->count() > 0)
    <div class="d-flex flex-wrap gap-1 mb-3">
        <span class="text-muted small me-1">Linked Entries:</span>
        @foreach($inventory->linkedReceiveEntries as $entry)
            <x-badge color="light">#{{ $entry->id }} - {{ $entry->reference ?? 'No Ref' }}</x-badge>
        @endforeach
    </div>
@endif
