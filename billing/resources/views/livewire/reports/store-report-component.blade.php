<div>
    <div
        class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
        <h1 class="h2">Store Report in
            <strong>{{\App\Models\WareHouse::find($warehouse)->name ?? 'All Warehouses'}}</strong></h1>
        <div class="btn-toolbar mb-2 mb-md-0">
            <div class="form-inline">
                <div class="row">
                    <div class="col" wire:loading.remove>
                        <select class="form-control" wire:model.live="warehouse" id="warehouse">
                            <option>Choose a warehouse..</option>
                            @forelse($warehouses as $warehouse)
                               <option value="{{$warehouse->id}}">{{$warehouse->name}}</option>
                            @empty
                            @endforelse
                        </select>
                    </div>
                    <p wire:loading>Loading..</p>
                </div>

            </div>
        </div>
    </div>


    <div class="card card-body d-none d-md-block">
        <table class="table table-hover table-sm">
            <thead>
            <tr>
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">SKU</th>
                <th class="border-gray-200">Type</th>
                <th class="border-gray-200">Name</th>
                <th class="border-gray-200">Category</th>
                <th class="border-gray-200">Unit</th>
                <th class="border-gray-200 text-center">Reorder</th>
                <th class="border-gray-200">In Hands</th>
            </tr>
            </thead>
            <tbody>
            @forelse($products as $product)
                @php
                    $balance = $product->stockBalance();
                @endphp

                @if($balance > 0)
                    <tr>
                        <td>{{ $loop->index + 1 }}</td>
                        <td>{{ $product->sku }}</td>
                        <td>{{ $product->type }}</td>
                        <td>{{ $product->name }}</td>
                        <td>{{ $product->category->name }}</td>
                        <td>{{ $product->unit->name ?? 'None' }}</td>
                        <td class="text-center">{{ $product->reorder }}</td>
                        @if(!$product->inventory)
                            <td>-</td>
                        @else
                            <td><strong>{{ number_format($balance) }}</strong></td>
                        @endif
                    </tr>
                @endif
            @empty
            @endforelse
            </tbody>
        </table>
    </div>

    {{-- Mobile View --}}
    <div class="d-md-none">
        @php $hasRecords = false; @endphp
        @forelse($products as $product)
            @php
                $balance = $product->stockBalance();
            @endphp

            @if($balance > 0)
                @php $hasRecords = true; @endphp
                <div class="card border-0 shadow-sm mb-2">
                    <div class="card-body p-3">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <span class="badge bg-secondary">{{ $product->type }}</span>
                            <span class="fw-bold">
                                @if(!$product->inventory)
                                    -
                                @else
                                    {{ number_format($balance) }}
                                @endif
                            </span>
                        </div>
                        <div class="fw-bold mb-1">{{ $product->name }}</div>
                        <div class="small text-muted">
                            <i class="fa fa-barcode me-1"></i>{{ $product->sku }}
                            <span class="ms-2"><i class="fa fa-tag me-1"></i>{{ $product->category->name }}</span>
                            <span class="ms-2"><i class="fa fa-ruler me-1"></i>{{ $product->unit->name ?? 'None' }}</span>
                        </div>
                        @if($balance <= $product->reorder)
                            <div class="border-top mt-2 pt-2 text-warning small">
                                <i class="fa fa-exclamation-triangle me-1"></i>Low stock - Reorder: {{ $product->reorder }}
                            </div>
                        @else
                            <div class="border-top mt-2 pt-2 small text-muted">
                                Reorder level: {{ $product->reorder }}
                            </div>
                        @endif
                    </div>
                </div>
            @endif
        @empty
        @endforelse
        @if(!$hasRecords)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 text-center py-4 text-muted">
                    No products in stock.
                </div>
            </div>
        @endif
    </div>

</div>
