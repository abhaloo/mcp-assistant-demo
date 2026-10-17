{{-- Supplier bill details card --}}
<div class="card border-0 shadow-sm mb-3">
    <div class="card-body p-3">
        <h6 class="fw-bold text-muted mb-3" style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;">
            Bill Details
        </h6>

        <div class="row g-2" style="font-size: 0.85rem;">
            <div class="col-6">
                <span class="text-muted d-block small">Supplier</span>
                <span class="fw-semibold">{{ $inventory->supplier->name ?? 'N/A' }}</span>
            </div>
            <div class="col-6">
                <span class="text-muted d-block small">Reference</span>
                <span class="fw-semibold">{{ $inventory->reference }}</span>
            </div>
            <div class="col-6 mt-2">
                <span class="text-muted d-block small">Created</span>
                <span>{{ $inventory->created_at->format('d M Y') }}</span>
            </div>
            <div class="col-6 mt-2">
                <span class="text-muted d-block small">Type</span>
                <span>{{ $inventory->type }}</span>
            </div>
        </div>

        @if($inventory->description)
            <div class="mt-3 pt-2 border-top" style="font-size: 0.85rem;">
                <span class="text-muted small">Description</span>
                <p class="mb-0 mt-1">{{ $inventory->description }}</p>
            </div>
        @endif
    </div>
</div>
