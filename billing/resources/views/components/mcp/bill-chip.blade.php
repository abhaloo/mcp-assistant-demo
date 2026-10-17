@props([
    'bill',
    'href' => null,
])

@php
    $type = $bill->type;
    $typeClass = strtolower(str_replace(' ', '-', $type));
    $supportedTypes = ['invoice', 'quotation', 'payable', 'payable-quotation'];
    if (!in_array($typeClass, $supportedTypes, true)) {
        $typeClass = 'quotation';
    }
    $number = $bill->invoice_number ?? $bill->id;
@endphp

@if ($href)
    <a href="{{ $href }}" class="mcp-bill mcp-bill--{{ $typeClass }}">
        <span class="mcp-bill__type">{{ $type }}</span>
        <span class="mcp-bill__num">#{{ $number }}</span>
    </a>
@else
    <span class="mcp-bill mcp-bill--{{ $typeClass }}">
        <span class="mcp-bill__type">{{ $type }}</span>
        <span class="mcp-bill__num">#{{ $number }}</span>
    </span>
@endif
