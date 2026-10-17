<tr wire:key="supplier-{{ $supplier->id }}">
    <td class="mcp-td--mute">{{ $index }}</td>
    <td>{{ $supplier->id }}</td>
    <td class="mcp-td--secondary">{{ $supplier->type }}</td>
    <td class="mcp-cell-strong">{{ $supplier->name }}</td>
    <td class="mcp-td--mute">{{ $supplier->currency->name }}</td>
    <td>{{ $supplier->phone }}</td>
    <td>{{ $supplier->email }}</td>
    <td class="mcp-td--right">
        @if($supplier->id != 1)
            <div class="mcp-row-actions">
                @can('view supplier')
                    <x-mcp.icon-btn variant="primary" icon="fa-eye"
                        :href="route('supplier.show', $supplier->id)" title="Show" label="Show" />
                @endcan
                @can('edit supplier')
                    <x-mcp.icon-btn icon="fa-edit" title="Edit" label="Edit"
                        wire:click="update({{ $supplier->id }})"
                        data-coreui-toggle="modal" data-coreui-target="#modal-supplier" />
                @endcan
                @can('delete supplier')
                    <x-mcp.icon-btn variant="danger" icon="fa-trash-alt" title="Delete" label="Delete"
                        wire:click="deleteConfirm({{ $supplier->id }})"
                        data-coreui-toggle="modal" data-coreui-target="#deleteModalsupplier" />
                @endcan
            </div>
        @endif
    </td>
</tr>
