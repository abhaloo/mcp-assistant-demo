<div>
    <div class="d-none d-md-block mcp-table-wrap" wire:loading.class="mcp-skeleton-refreshing" wire:loading.attr="aria-busy">
        <table class="mcp-table">
            <thead>
            <tr>
                <th>#</th>
                <th>From</th>
                <th>Post Date</th>
                <th class="mcp-td--right">Total</th>
            </tr>
            </thead>
            <tbody>
            @forelse($inventories as $inventory)
                <tr class="mcp-row--link">
                    <td class="mcp-td--mute">
                        <a href="{{ route('inventory.show', $inventory->id) }}" class="mcp-row__stretch"
                            aria-label="View purchase from {{ $inventory->supplier->name ?? 'supplier' }}"><span class="visually-hidden">View</span></a>
                        {{ $loop->index + 1 }}
                    </td>
                    <td class="mcp-cell-strong">{{ $inventory->supplier->name ?? 'NIL' }}</td>
                    <td class="mcp-td--mono mcp-td--secondary">{{ $inventory->created_at->format('d-m-Y') }}</td>
                    <td class="mcp-td--right mcp-td--mono">{{ number_format($inventory->amount(), 2) }}</td>
                </tr>
            @empty
                <tr>
                    <td colspan="4">
                        <x-mcp.empty title="No purchases yet"
                            hint="Stock receipts and supplier bills will show up here.">
                            <x-slot:action>
                                <x-mcp.btn variant="primary" size="sm" icon="fa-boxes" :href="route('inventory.index')">View inventory</x-mcp.btn>
                            </x-slot:action>
                        </x-mcp.empty>
                    </td>
                </tr>
            @endforelse
            </tbody>
        </table>
    </div>

    <div class="d-md-none">
        @forelse($inventories as $inventory)
            <a href="{{ route('inventory.show', $inventory->id) }}" class="mcp-list-row">
                <span class="mcp-list-row__main">
                    <span class="mcp-list-row__title">{{ $inventory->supplier->name ?? 'NIL' }}</span>
                    <span class="mcp-list-row__sub d-block">{{ $inventory->created_at->format('d-m-Y') }}</span>
                </span>
                <span class="mcp-list-row__end">
                    <span class="mcp-list-row__title d-block">{{ number_format($inventory->amount(), 2) }}</span>
                </span>
            </a>
        @empty
            <x-mcp.empty title="No purchases yet"
                hint="Stock receipts and supplier bills will show up here." />
        @endforelse
    </div>

    {{ $inventories->links('vendor.pagination.mcp') }}
</div>
