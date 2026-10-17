{{-- Mobile header: back button, centered title, payment status --}}
<div class="d-flex align-items-center justify-content-between mb-3">
    <a href="{{ $fromOrder ? route('customer-orders.show', $fromOrder) : route('bill.index') }}"
       class="btn btn-sm btn-light rounded-circle d-flex align-items-center justify-content-center"
       style="width: 36px; height: 36px;">
        <i class="fa fa-chevron-left"></i>
    </a>

    <div class="text-center flex-fill px-2">
        <h1 class="h5 mb-0 fw-bold">{{ $bill->type }} #{{ $bill->invoice_number ?? $bill->id }}</h1>
    </div>

    {{-- Spacer to balance the back button --}}
    <div style="width: 36px;"></div>
</div>

{{-- Status badges --}}
<div class="d-flex justify-content-center gap-2 mb-3">
    @if($bill->balance() <= 0)
        <x-badge color="success">PAID</x-badge>
    @elseif($bill->balance() > 0 && $bill->balance() < $bill->amount())
        <x-badge color="sky">PARTIAL</x-badge>
    @else
        <x-badge color="danger">UNPAID</x-badge>
    @endif
    <x-badge color="info">{{ $bill->currency->currency_code }}</x-badge>
</div>

{{-- Balance highlight --}}
@if($bill->balance() > 0)
    <div class="text-center mb-3 py-2 rounded" style="background-color: #fff3cd;">
        <span class="text-muted small">Balance Due</span>
        <div class="fw-bold text-danger" style="font-size: 1.25rem;">
            {{ $bill->currency->currency_code }} {{ number_format($bill->balance(), 2) }}
        </div>
    </div>
@endif
