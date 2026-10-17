@php
    $billBalance = $bill->balance();
    $needPayment = $bill->needPayment();
@endphp
<tr wire:key="bill-{{ $bill->id }}" @class([
    'mcp-row--priority' => $needPayment,
    'text-danger' => in_array($bill->type, ['Invoice', 'Payable Quotation']) && ! $bill->has_transactions,
])>
    <td class="mcp-td--mute">{{ $index }}</td>
    <td>
        <a href="{{ route('bill.show', $bill) }}" class="mcp-id-link">
            {{ $bill->invoice_number ?? $bill->id }}
        </a>
    </td>
    <td class="mcp-td--secondary">{{ $bill->type }}</td>
    <td class="mcp-cell-strong">
        <div class="mcp-truncate" title="{{ $bill->customer->name }}">{{ $bill->customer->name }}</div>
    </td>
    <td class="mcp-td--mono mcp-td--secondary">{{ $bill->created_at->format('d-m-Y') }}</td>
    <td class="mcp-td--mute">{{ $bill->currency->currency_code }}</td>
    <td class="mcp-td--right mcp-td--mono">{{ number_format($bill->amount(), 2) }}</td>
    <td class="mcp-td--right mcp-td--mono">{{ number_format($billBalance, 2) }}</td>
    <td>
        {!! $bill->paymentBadge() !!}
    </td>
    <td>
        @if($needPayment)
            <small class="text-danger fw-bold">FOLLOW UP</small>
        @endif
    </td>
    <td class="mcp-td--right">
        <div class="mcp-row-actions">
            <x-mcp.icon-btn variant="primary" icon="fa-eye" :href="route('bill.show', $bill)" title="Show" label="Show" />

            @can('add invoice')
                @if($bill->type != 'Invoice' && $bill->type != 'Payable Quotation')
                    <x-mcp.icon-btn variant="warning" icon="fa-share" title="To Tax Invoice" label="Convert"
                        data-coreui-toggle="modal" :data-coreui-target="'#modal-convert-tax-'.$bill->id" />
                    @if(auth()->user()->role == 'admin')
                        <x-mcp.icon-btn variant="warning" icon="fa-file-invoice-dollar" title="To Payable Quotation" label="Payable"
                            data-coreui-toggle="modal" :data-coreui-target="'#modal-convert-payable-'.$bill->id" />
                    @endif
                @endif
            @endcan

            @if($bill->type == 'Invoice')
                @can('edit invoice')
                    <x-mcp.icon-btn icon="fa-edit" :href="route('bill.create', $bill)" title="Edit" label="Edit" />
                @endcan
            @else
                @can('edit quotation')
                    <x-mcp.icon-btn icon="fa-edit" :href="route('bill.create', $bill)" title="Edit" label="Edit" />
                @endcan
            @endif
        </div>
    </td>
</tr>
