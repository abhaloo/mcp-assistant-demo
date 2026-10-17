{{-- Expense bill items as cards --}}
<div class="mb-3">
    <h6 class="fw-bold mb-2">Entry Items</h6>

    @forelse($inventory->inventoryItems as $item)
        @php
            $lineTotal = $item->quantity * $item->cost;
            $taxAmount = $lineTotal * ($item->tax / 100);
        @endphp
        <div class="card border-0 shadow-sm mb-2">
            <div class="card-body p-3">
                <div class="fw-semibold mb-1" style="font-size: 0.9rem;">
                    {{ $item->account->name ?? 'None' }}
                </div>
                @if($item->description)
                    <div class="text-muted small mb-2">{{ $item->description }}</div>
                @endif

                <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem;">
                    <span>{{ number_format($item->cost, 2) }} x {{ $item->quantity }}</span>
                    @if($item->tax > 0)
                        <span>+{{ number_format($taxAmount, 2) }} tax</span>
                    @endif
                </div>

                <div class="text-end fw-bold" style="font-size: 0.95rem;">
                    {{ number_format($lineTotal, 2) }}
                </div>
            </div>
        </div>
    @empty
        <div class="card border-0 shadow-sm">
            <div class="card-body text-center text-muted py-3 small">No items</div>
        </div>
    @endforelse

    {{-- Total --}}
    <div class="card border-0 shadow-sm">
        <div class="card-body p-3 d-flex justify-content-between fw-bold text-success">
            <span>Total</span>
            <span>{{ number_format($inventory_value, 2) }}</span>
        </div>
    </div>
</div>
