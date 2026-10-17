{{-- Bill details card --}}
<x-mcp.card title="Bill Details" class="mb-3">
    <div class="row g-2" style="font-size: 0.85rem;">
        <div class="col-6">
            <span class="text-muted d-block small">Customer</span>
            <span class="fw-semibold">{{ $bill->customer->name }}</span>
        </div>
        <div class="col-6">
            <span class="text-muted d-block small">Reference</span>
            <span class="fw-semibold">{{ $bill->controlNumber->control_number }}</span>
        </div>
        <div class="col-6 mt-2">
            <span class="text-muted d-block small">Created</span>
            <span>{{ $bill->created_at->format('d M Y') }}</span>
        </div>
        <div class="col-6 mt-2">
            <span class="text-muted d-block small">Due Date</span>
            <span>{{ $bill->controlNumber->expire_date->format('d M Y') }}</span>
        </div>
    </div>

    @if($bill->description)
        <div class="mt-3 pt-2 border-top" style="font-size: 0.85rem;">
            <span class="text-muted small">Notes</span>
            <p class="mb-0 mt-1">{{ $bill->description }}</p>
        </div>
    @endif
</x-mcp.card>
