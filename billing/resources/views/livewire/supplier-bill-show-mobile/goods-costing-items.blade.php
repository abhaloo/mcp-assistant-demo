{{-- Goods costing items as cards --}}
<div class="mb-3">
    <h6 class="fw-bold mb-2">Items</h6>

    @forelse($goodsCostingItems as $item)
        <div class="card border-0 shadow-sm mb-2">
            <div class="card-body p-3">
                <div class="fw-semibold mb-1" style="font-size: 0.9rem;">
                    {{ $item['product_name'] }}
                </div>
                @if($item['gsm'] || $item['size'])
                    <div class="text-muted small mb-2">
                        @if($item['gsm']){{ $item['gsm'] }}gsm @endif
                        @if($item['size']){{ $item['size'] }}@endif
                    </div>
                @endif

                <div class="row g-2 mb-2" style="font-size: 0.8rem;">
                    <div class="col-6">
                        <span class="text-muted d-block">Invoice Value ({{ $currencySymbol }})</span>
                        <span>{{ number_format($item['invoice_value_foreign'] ?? 0, 2) }}</span>
                    </div>
                    <div class="col-6">
                        <span class="text-muted d-block">Quantity</span>
                        <span>{{ number_format($item['quantity'] ?? 0) }} {{ $item['stock_unit_label'] ?? 'Units' }}</span>
                    </div>
                    @if($item['has_hierarchy'])
                        <div class="col-6">
                            <span class="text-muted d-block">{{ $item['intermediate_unit_label'] }}s/{{ $item['costing_unit_label'] }}</span>
                            <span>{{ number_format($item['costing_to_intermediate'] ?? 1, 2) }}</span>
                        </div>
                        <div class="col-6">
                            <span class="text-muted d-block">{{ $item['stock_unit_label'] }}s/{{ $item['intermediate_unit_label'] }}</span>
                            <span>{{ number_format($item['intermediate_to_stock'] ?? 1, 2) }}</span>
                        </div>
                    @endif
                    <div class="col-6">
                        <span class="text-muted d-block">Allocated ({{ $currencySymbol }})</span>
                        <span>{{ number_format($item['allocated_charges'] ?? 0, 2) }}</span>
                    </div>
                </div>

                <div class="d-flex justify-content-end pt-2 border-top">
                    <div>
                        <span class="text-muted small">Unit Cost (USD)</span>
                        <span class="fw-bold text-success ms-2">{{ number_format($item['unit_cost_tzs'] ?? 0, 2) }}</span>
                    </div>
                </div>
            </div>
        </div>
    @empty
        <div class="card border-0 shadow-sm">
            <div class="card-body text-center text-muted py-3 small">No items found</div>
        </div>
    @endforelse
</div>
