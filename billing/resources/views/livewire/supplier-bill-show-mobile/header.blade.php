{{-- Mobile header: back button, title, status --}}
<div class="d-flex align-items-center justify-content-between mb-3">
    <a href="{{ route('supplier-bill.index') }}"
       class="btn btn-sm btn-light rounded-circle d-flex align-items-center justify-content-center"
       style="width: 36px; height: 36px;">
        <i class="fa fa-chevron-left"></i>
    </a>

    <div class="text-center flex-fill px-2">
        <h1 class="h5 mb-0 fw-bold">
            {{ $inventory->is_goods_costing ? 'Goods Costing' : 'Bill Entry' }}
            #{{ $inventory->id }}
        </h1>
    </div>

    <div style="width: 36px;"></div>
</div>

{{-- Status badges --}}
@php $amount = $inventory->amount(); @endphp
<div class="d-flex justify-content-center gap-2 mb-3">
    @if($amount > 0)
        @if($balance == 0)
            <x-badge color="success">PAID</x-badge>
        @elseif($balance > 0 && $balance < $amount)
            <x-badge color="info">PARTIAL PAID</x-badge>
        @else
            <x-badge color="danger">UNPAID</x-badge>
        @endif
    @else
        <x-badge color="secondary">No Amount</x-badge>
    @endif
    <x-badge color="light">{{ $inventory->type }}</x-badge>
</div>

{{-- Balance highlight --}}
@if($amount > 0)
    <div class="card border-0 shadow-sm mb-3">
        <div class="card-body p-3">
            <div class="d-flex justify-content-between text-center" style="font-size: 0.85rem;">
                <div>
                    <span class="text-muted d-block small">Bill Amount</span>
                    <span class="fw-bold">{{ number_format($amount, 2) }}</span>
                </div>
                <div>
                    <span class="text-muted d-block small">Paid</span>
                    <span class="fw-bold text-success">{{ number_format($amount - $balance, 2) }}</span>
                </div>
                <div>
                    <span class="text-muted d-block small">Balance</span>
                    <span class="fw-bold {{ $balance > 0 ? 'text-danger' : 'text-success' }}">
                        {{ number_format($balance, 2) }}
                    </span>
                </div>
            </div>
        </div>
    </div>
@endif
