<div>
    <div class="d-none d-md-block mcp-table-wrap" wire:loading.class="mcp-skeleton-refreshing" wire:loading.attr="aria-busy">
        <table class="mcp-table">
            <thead>
            <tr>
                <th>#</th>
                <th>Bill For</th>
                <th>Issue Date</th>
                <th>Type</th>
                <th class="mcp-td--right">Total</th>
                <th>Status</th>
            </tr>
            </thead>
            <tbody>
            @forelse($bills as $bill)
                <tr class="mcp-row--link">
                    <td class="mcp-td--mute">
                        <a href="{{ route('bill.show', $bill->id) }}" class="mcp-row__stretch"
                            aria-label="View invoice for {{ $bill->customer->name }}"><span class="visually-hidden">View</span></a>
                        {{ $loop->index + 1 }}
                    </td>
                    <td class="mcp-cell-strong">{{ $bill->customer->name }}</td>
                    <td class="mcp-td--mono mcp-td--secondary">{{ $bill->created_at->format('d-m-Y') }}</td>
                    <td class="mcp-td--secondary">{{ $bill->type }}</td>
                    <td class="mcp-td--right mcp-td--mono">{{ number_format($bill->amount(), 2) }}</td>
                    <td>
                        @if($bill->type == 'Invoice')
                            {!! $bill->paymentBadge() !!}
                        @else
                            <span class="mcp-em-dash">—</span>
                        @endif
                    </td>
                </tr>
            @empty
                <tr>
                    <td colspan="6">
                        <x-mcp.empty title="No invoices yet"
                            hint="Invoices appear here once customer orders are billed.">
                            <x-slot:action>
                                <x-mcp.btn variant="primary" size="sm" icon="fa-receipt" :href="route('bill.index', ['type' => 'Invoice'])">View invoices</x-mcp.btn>
                            </x-slot:action>
                        </x-mcp.empty>
                    </td>
                </tr>
            @endforelse
            </tbody>
        </table>
    </div>

    <div class="d-md-none">
        @forelse($bills as $bill)
            <a href="{{ route('bill.show', $bill->id) }}" class="mcp-list-row">
                <span class="mcp-list-row__main">
                    <span class="mcp-list-row__title">{{ $bill->customer->name }}</span>
                    <span class="mcp-list-row__sub d-block">{{ $bill->type }} · {{ $bill->created_at->format('d-m-Y') }}</span>
                </span>
                <span class="mcp-list-row__end">
                    <span class="mcp-list-row__title d-block">{{ number_format($bill->amount(), 2) }}</span>
                    @if($bill->type == 'Invoice')
                        {!! $bill->paymentBadge() !!}
                    @endif
                </span>
            </a>
        @empty
            <x-mcp.empty title="No invoices yet"
                hint="Invoices appear here once customer orders are billed." />
        @endforelse
    </div>

    {{ $bills->links('vendor.pagination.mcp') }}
</div>
