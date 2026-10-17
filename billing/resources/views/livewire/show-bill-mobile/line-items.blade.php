{{-- Line items as cards --}}
<div class="mb-3">
    <h6 class="fw-bold text-success mb-2">Bill Items</h6>

    @forelse($bill->billItems as $item)
        @php
            $effectiveQty = $item->quantity - $item->quantity_returned;
            $lineTotal = (($effectiveQty * $item->price) - $item->discount);
            $taxAmount = $lineTotal * ($item->tax / 100);
            $subtotal = $lineTotal + $taxAmount;
        @endphp
        <x-mcp.card class="mb-2">
            <div class="fw-semibold mb-1" style="font-size: 0.9rem;">
                {{ $item->product->name ?? 'None' }}
            </div>
            @if($item->description)
                <div class="text-muted small mb-2">{{ $item->description }}</div>
            @endif

            <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem;">
                <span>{{ number_format($item->price, 2) }} x {{ $effectiveQty }}</span>
                @if($item->discount > 0)
                    <span class="text-danger">-{{ number_format($item->discount, 2) }} disc</span>
                @endif
                @if($item->tax > 0)
                    <span>+{{ number_format($taxAmount, 2) }} tax</span>
                @endif
            </div>

            <div class="text-end fw-bold" style="font-size: 0.95rem;">
                {{ number_format($subtotal, 2) }}
            </div>
        </x-mcp.card>
    @empty
        <x-mcp.card>
            <div class="text-center text-muted py-3">No items</div>
        </x-mcp.card>
    @endforelse

    {{-- Totals --}}
    <x-mcp.card>
        <div class="d-flex justify-content-between mb-1" style="font-size: 0.85rem;">
            <span class="text-muted">Subtotal</span>
            <span>{{ number_format($bill_value - $bill->discount, 2) }}</span>
        </div>
        @if($bill->discount > 0)
            <div class="d-flex justify-content-between mb-1" style="font-size: 0.85rem;">
                <span class="text-muted">Discount</span>
                <span class="text-danger">-{{ number_format($bill->discount, 2) }}</span>
            </div>
        @endif
        @if($tax_value > 0)
            <div class="d-flex justify-content-between mb-1" style="font-size: 0.85rem;">
                <span class="text-muted">Tax</span>
                <span>{{ number_format($tax_value, 2) }}</span>
            </div>
        @endif
        <div class="d-flex justify-content-between pt-2 border-top fw-bold text-success">
            <span>Grand Total</span>
            <span>{{ number_format($bill_value + $tax_value, 2) }}</span>
        </div>
    </x-mcp.card>
</div>
