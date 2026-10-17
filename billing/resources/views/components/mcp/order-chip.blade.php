@props([
    'order',
    'href' => null,
])

@php
    $resolvedHref = $href ?? (Route::has('customer-orders.show') ? route('customer-orders.show', $order) : null);
@endphp

{{-- 9px icon size: extracting a class for one numeric property is over-engineering. --}}
@if($resolvedHref)
<a href="{{ $resolvedHref }}" {{ $attributes->merge(['class' => 'mcp-order-chip']) }}>
    <i class="fa fa-folder" aria-hidden="true"></i>
    #{{ $order->order_number }}
</a>
@else
<span {{ $attributes->merge(['class' => 'mcp-order-chip']) }}>
    <i class="fa fa-folder" aria-hidden="true"></i>
    #{{ $order->order_number }}
</span>
@endif
