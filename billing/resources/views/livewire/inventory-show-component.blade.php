<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Inventory Entry <strong>#{{$inventory->id}}</strong>
            </h1>
        </div>
        <div class="col">
            <div class="float-end">
                <x-back-link :fallback="route('inventory.index')"
                   class="btn btn-sm btn-secondary d-inline-flex align-items-center">
                    <i class="fa fa-chevron-left me-2"></i>
                    Back
                </x-back-link>
            </div>
        </div>
    </div>

    <div class="card card-body border-0 shadow table-wrapper table-responsive">
        <table class="table bg-gray-100">
            <tr>
                <th>Entry Id</th>
                <th>Warehouse</th>
                <th>Type</th>
                <th>Supplier</th>
                @if($inventory->type == 'Adjustment')
                    <th>Reason</th>
                @else
                    <th>Reference</th>
                @endif
                <th>Created At</th>
                <th>Description</th>
            </tr>

            <tbody>
            <tr>
                <td>{{ $inventory->id }}</td>
                <td>{{ $inventory->warehouse->name ?? '-' }}</td>
                <td>{{ $inventory->type }}</td>
                <td>{{ $inventory->supplier->name ?? 'N/A' }}</td>
                @if($inventory->type == 'Adjustment')
                    <td>{{ $inventory->adj_reason }}</td>
                @else
                    <td>{{ $inventory->reference }}</td>
                @endif
                <td>{{ $inventory->created_at->format('d-m-Y H:i:s')}}</td>
                <td>{{ $inventory->description}}</td>
            </tr>
            </tbody>

        </table>
        <h5 class="mt-3">Entry Items</h5>
        <table class="table table-sm">
            <tr class="bg-gray-100">
                <th>Name</th>
                <th>Description</th>
                @can('view inventory costing')
                    <th>Cost</th>
                @endcan
                <th>Quantity</th>
                @can('view inventory costing')
                    <th class="text-end">Subtotal</th>
                @endcan
            </tr>
            <tbody>
            @forelse($inventory->inventoryItems as $item)
                <tr>
                    <td>{{ $item->product->name ?? 'None' }}</td>
                    <td>{{ $item->description }}</td>
                    @can('view inventory costing')
                        <td>{{ number_format($item->cost, 2) }}</td>
                    @endcan
                    <td>{{ number_format($item->quantity, 2) }}</td>
                    @can('view inventory costing')
                        <td class="text-end">{{ number_format($item->quantity * $item->cost, 2) }}</td>
                    @endcan
                </tr>
            @empty
            @endforelse
            @can('view inventory costing')
                <tr class="bg-gray-100">
                    <th class="fw-bold text-end text-success" colspan="5">
                        Total: {{ number_format($inventory_value, 2) }}<br/>
                        Tax: {{ number_format($inventory->tax, 2) }}<br/>
                        G.Total: {{ number_format($inventory_value + $inventory->tax, 2) }}
                    </th>
                </tr>
            @endcan
            </tbody>
        </table>

        {{-- Edit History Section --}}
        @if($editHistory->count() > 0)
            <h5 class="mt-4">Edit History</h5>
            <table class="table table-sm table-bordered">
                <thead class="bg-gray-100">
                    <tr>
                        <th style="width: 150px;">Date & Time</th>
                        <th style="width: 120px;">Edited By</th>
                        <th>Previous Values</th>
                    </tr>
                </thead>
                <tbody>
                @foreach($editHistory as $history)
                    <tr>
                        <td class="small">{{ \Carbon\Carbon::parse($history['date'])->format('d-m-Y H:i:s') }}</td>
                        <td class="small">{{ $history['user'] }}</td>
                        <td class="small">
                            @php $data = $history['data']; @endphp
                            <div class="d-flex flex-wrap gap-2">
                                @if(isset($data['supplier_id']))
                                    <x-badge color="light">Supplier: {{ $data['supplier_id'] ?? 'N/A' }}</x-badge>
                                @endif
                                @if(isset($data['ware_house_id']))
                                    <x-badge color="light">Warehouse: {{ $data['ware_house_id'] ?? 'N/A' }}</x-badge>
                                @endif
                                @if(isset($data['reference']))
                                    <x-badge color="light">Reference: {{ $data['reference'] ?? 'N/A' }}</x-badge>
                                @endif
                                @if(isset($data['adj_reason']))
                                    <x-badge color="light">Reason: {{ $data['adj_reason'] ?? 'N/A' }}</x-badge>
                                @endif
                                @if(isset($data['tax']))
                                    <x-badge color="light">Tax: {{ number_format($data['tax'] ?? 0, 2) }}</x-badge>
                                @endif
                            </div>
                        </td>
                    </tr>
                @endforeach
                </tbody>
            </table>
        @endif
    </div>
</div>
