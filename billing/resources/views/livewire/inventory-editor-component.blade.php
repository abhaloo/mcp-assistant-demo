<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3"><strong>@if($inventory_id)
                        Edit
                    @else
                        Create
                    @endif</strong> {{ ucfirst($type) }} Entry
            </h1>
        </div>
        <div class="col">
            @if($from_job)
                <div class="float-end">
                    <a href="{{ route('work-order.show', $from_job) }}"
                       class="btn btn-sm btn-secondary d-inline-flex align-items-center">
                        <i class="fa fa-chevron-left me-2"></i>
                        Back to Job
                    </a>
                </div>
            @endif
        </div>
    </div>

    <div class="card card-body border-0 shadow">
        <div class="row d-flex justify-content-between">
            @can('view job')
                @if($type == 'Issue')
                    <div class="col-md-3">
                        @livewire('job-search-component', [
                            'label' => 'Issue for a Job?',
                            'placeholder' => 'Search job by number or title...',
                            'statusFilter' => null,
                            'initialJobId' => $job_number ?? null,
                        ], key('issue-job-search-' . ($job_number ?? 'new')))
                        @error('job_number')
                        <div class="text-danger small mt-1">
                            {{ $message }}
                        </div>
                        @enderror
                    </div>
                @endif
            @endcan

            @if($type == 'Adjustment')
                <div class="col-md-2">
                    <label for="adj_reason">Adjustment Reason</label>
                    <select class="form-control" wire:model.live="adj_reason" id="adj_reason">
                        <option>Choose a reason..</option>
                        <option>Wrong Input</option>
                        <option>Lost</option>
                        <option>Expired</option>
                    </select>
                    @error('adj_reason')
                    <div class="invalid-feedback">
                        {{ $message }}
                    </div>
                    @enderror
                </div>
            @endif

            @can('view supplier')
                @if($type == 'Receive')
                    <div class="col-md-3">
                        @livewire('supplier-search-component', [
                            'initialSupplierId' => $supplier_id ?? null,
                        ], key('receive-supplier-search-' . ($supplier_id ?? 'new')))
                        @error('supplier_id')
                        <div class="text-danger small mt-1">
                            {{ $message }}
                        </div>
                        @enderror
                    </div>
                @endif
            @endcan
            @if($type == 'Transfer')
                <div class="col-md-2">
                    <label for="warehouse">From</label>
                    <select class="form-control" wire:model.live="warehouse_id" id="warehouse">
                        <option>Choose a warehouse..</option>
                        @forelse($warehouses as $warehouse)
                            <option value="{{$warehouse->id}}">{{$warehouse->name}}</option>
                        @empty
                        @endforelse
                    </select>
                    @error('warehouse_id')
                    <div class="invalid-feedback">
                        {{ $message }}
                    </div>
                    @enderror
                </div>

                <div class="col-md-2">
                    <label for="warehouse_to">To</label>
                    <select class="form-control" wire:model.live="warehouse_id_to" id="warehouse_to">
                        <option>Choose a warehouse..</option>
                        @forelse($warehouses as $warehouse)
                            <option value="{{$warehouse->id}}">{{$warehouse->name}}</option>
                        @empty
                        @endforelse
                    </select>
                    @error('warehouse_id_to')
                    <div class="invalid-feedback">
                        {{ $message }}
                    </div>
                    @enderror
                </div>
            @else
                <div class="col-md-2">
                    <label for="warehouse">Warehouse</label>
                    <select class="form-control" wire:model.live="warehouse_id" id="warehouse">
                        <option>Choose a warehouse..</option>
                        @forelse($warehouses as $warehouse)
                            <option value="{{$warehouse->id}}">{{$warehouse->name}}</option>
                        @empty
                        @endforelse
                    </select>
                    @error('warehouse_id')
                    <div class="invalid-feedback">
                        {{ $message }}
                    </div>
                    @enderror
                </div>
            @endif
            <div class="col-md-5 pull-right mb-2">
                @livewire('product-search-component', ['type' => 'Product & Material'])
            </div>
        </div>

        <div class="row z-0 mt-3">
            <div class="col-md-12">
                @error('inventory_items')
                <p class="text-danger font-weight-bold">
                    {{ $message }}
                </p>
                @enderror

                @php
                    // Check if any item has unit hierarchy for dynamic columns
                    $hasAnyHierarchy = collect($inventory_items)->contains(fn($item) => $item['has_hierarchy'] ?? false);
                    // Get first item with hierarchy to determine column labels
                    $hierarchyItem = collect($inventory_items)->first(fn($item) => $item['has_hierarchy'] ?? false);
                @endphp

                <table class="table table-bordered table-hover table-sm bg-gray-100">
                    <tr>
                        <th>Item</th>
                        <th>Description</th>
                        @if($type == 'Receive' && $hasAnyHierarchy && $hierarchyItem)
                            <th class="text-center bg-light" style="min-width: 100px;">{{ $hierarchyItem['costing_unit_label'] }}s</th>
                            @if($hierarchyItem['intermediate_unit_label'] ?? false)
                                <th class="text-center bg-light" style="min-width: 120px;">{{ $hierarchyItem['intermediate_unit_label'] }}s per {{ $hierarchyItem['costing_unit_label'] }}</th>
                                <th class="text-center bg-light" style="min-width: 120px;">{{ $hierarchyItem['stock_unit_label'] }}s per {{ $hierarchyItem['intermediate_unit_label'] }}</th>
                            @else
                                <th class="text-center bg-light" style="min-width: 120px;">{{ $hierarchyItem['stock_unit_label'] }}s per {{ $hierarchyItem['costing_unit_label'] }}</th>
                            @endif
                            <th class="text-center bg-light" style="min-width: 100px;">Total {{ $hierarchyItem['stock_unit_label'] }}s</th>
                        @else
                            <th>Qty</th>
                            <th>Unit</th>
                        @endif
                        @if($type == 'Issue')
                            @can('view inventory costing')
                                <th>Cost</th>
                                <th>Subtotal</th>
                            @endcan
                            <th></th>
                        @elseif($type != 'Receive')
                            @can('view supplier')
                                <th>Cost</th>
                            @endcan
                            <th colspan="2">Subtotal</th>
                        @else
                            <th></th>
                        @endif
                    </tr>

                    <tbody>
                    @forelse($inventory_items as $item)
                        <tr>
                            <td>{{$item['name']}}</td>
                            <td>
                                <input type="text" class="form-control form-control-sm boxed"
                                       wire:model.live="inventory_items.{{$item['id']}}.description">
                            </td>

                            @if($type == 'Receive' && $hasAnyHierarchy)
                                @if($item['has_hierarchy'] ?? false)
                                    {{-- Costing Quantity (e.g., Pallets) --}}
                                    <td>
                                        <input type="number" step="0.01" min="0"
                                               class="form-control form-control-sm text-center"
                                               wire:model.blur="inventory_items.{{$item['id']}}.costing_quantity"
                                               placeholder="0">
                                    </td>
                                    {{-- Conversion Factor 1 (e.g., Reams per Pallet, or Kgs per Tin for 2-level) --}}
                                    <td>
                                        <input type="number" step="0.01" min="0"
                                               class="form-control form-control-sm text-center"
                                               wire:model.blur="inventory_items.{{$item['id']}}.costing_to_intermediate"
                                               placeholder="1">
                                    </td>
                                    {{-- Conversion Factor 2 (e.g., Sheets per Ream) --}}
                                    @if($hierarchyItem['intermediate_unit_label'] ?? false)
                                    <td>
                                        @if($item['intermediate_unit_label'] ?? false)
                                            <input type="number" step="0.01" min="0"
                                                   class="form-control form-control-sm text-center"
                                                   wire:model.blur="inventory_items.{{$item['id']}}.intermediate_to_stock"
                                                   placeholder="1">
                                        @else
                                            <span class="text-muted text-center d-block">-</span>
                                        @endif
                                    </td>
                                    @endif
                                    {{-- Calculated Stock Quantity --}}
                                    <td>
                                        <input type="text"
                                               class="form-control form-control-sm text-center fw-bold text-success bg-light"
                                               value="{{ number_format($item['quantity'] ?? 0, 2) }}"
                                               readonly>
                                    </td>
                                @else
                                    {{-- Product without hierarchy - show simple quantity input spanning the hierarchy columns --}}
                                    @php $colSpan = ($hierarchyItem['intermediate_unit_label'] ?? false) ? 3 : 2; @endphp
                                    <td colspan="{{ $colSpan }}">
                                        <input type="number" step="0.01" class="form-control form-control-sm"
                                               wire:model.blur="inventory_items.{{$item['id']}}.quantity"
                                               wire:change="checkBalance({{$item['id']}})">
                                    </td>
                                    <td>
                                        <span class="text-muted">{{ $item['unit'] }}</span>
                                    </td>
                                @endif
                            @else
                                {{-- Standard quantity input for non-Receive or when no hierarchy exists --}}
                                <td>
                                    <input type="number" step="0.01" class="form-control form-control-sm boxed"
                                           wire:model.blur="inventory_items.{{$item['id']}}.quantity"
                                           wire:change="checkBalance({{$item['id']}})">
                                </td>
                                <td>{{$item['unit']}}</td>
                            @endif

                            @if($type == 'Issue')
                                @can('view inventory costing')
                                    <td>
                                        <input type="number" step="0.01" disabled class="form-control form-control-sm"
                                               wire:model.live="inventory_items.{{$item['id']}}.cost">
                                    </td>
                                    <td class="text-right text-success font-weight-bold">
                                        {{number_format((floatval($item['cost'] ?? 0) * floatval($item['quantity'] ?? 0)), 2)}}
                                    </td>
                                @endcan
                            @elseif($type != 'Receive')
                                @can('view supplier')
                                    <td>
                                        <input type="number" step="0.01" disabled class="form-control form-control-sm"
                                               wire:model.live="inventory_items.{{$item['id']}}.cost">
                                    </td>
                                @endcan

                                <td class="text-right text-success font-weight-bold">
                                    {{number_format((floatval($item['cost'] ?? 0) * floatval($item['quantity'] ?? 0)), 2)}}
                                </td>
                            @endif

                            <td>
                                <a href="#" class="btn btn-outline-danger text-dark btn-sm"
                                   wire:click="itemDelete({{$item['id']}})"
                                ><i class="fa fa-trash"></i> </a>
                            </td>

                        </tr>
                    @empty
                        <tr>
                            <td colspan="10" class="text-center py-4">
                                <div class="text-muted">
                                    <h5 class="fw-normal mb-2">No products added yet</h5>
                                    <p class="mb-0">Use the <strong>Product Search</strong> above to add products to this {{ strtolower($type) }} entry.</p>
                                </div>
                            </td>
                        </tr>
                    @endforelse
                    </tbody>
                </table>

                @if($type == 'Issue')
                    @can('view inventory costing')
                        <div class="row">
                            <div class="col-8"></div>
                            <div class="col-4 text-end" style="font-size: 20px;font-weight: bold;line-height: 10px">
                                <p class="text-success">Total: {{number_format($total_value, 2)}}</p>
                            </div>
                        </div>
                    @endcan
                @elseif($type != 'Receive')
                <table class="table">
                    <tr class="text-right">
                        <th colspan="5">Totals: {{number_format($total_value, 2)}}</th>
                    </tr>
                </table>
                <div class="row">
                    <div class="col-8"></div>
                    @if($currency && $currency->id != 1)
                        <div class="col-4 text-end" style="font-size: 20px;font-weight: bold">
                            <p>Totals({{ $currency->currency_code }}): {{number_format($total_value, 2)}} </p>
                            <p>Totals({{ $home_currency->currency_code ?? 'USD' }}): {{number_format($total_value * $currency->rate  , 2)}}</p>
                        </div>
                    @else
                        <div class="col-4 text-end" style="font-size: 20px;font-weight: bold;line-height: 10px">
                            <p>Totals: {{number_format($total_value, 2)}}</p>
                            <p class="text-secondary">Tax: {{number_format($total_tax, 2)}}</p>
                            <p class="text-success">Grand Total: {{number_format($total_tax + $total_value, 2)}}</p>
                        </div>
                    @endif
                </div>
                @endif


                @if (session()->has('message'))
                    <div class="alert alert-success">
                        {{ session('message') }}
                    </div>
                @endif

            </div>
        </div>
        <div class="d-flex justify-content-end mt-3">
            <button class="btn btn-secondary"
                  onclick="confirmWarehouse('{{strtoupper(\App\Models\WareHouse::find($warehouse_id)?->name) }}', '{{ $type }}') || event.stopImmediatePropagation()"
            >
                <i data-feather="user-save" style="margin-bottom: 3px"></i> @if($inventory_id)
                    Edit
                @else
                    Create
                @endif {{ ucfirst($type) }}</button>
        </div>
    </div>


    @push('scripts')
        <script>
            Livewire.on('closeModal', () => {
                $('#modal-inventory').modal('hide')
            })

            function confirmWarehouse(message, type){
                let text = "You are about to issue items from warehouse: " + message + ". Do you want to proceed?";

                if (type === 'Receive') {
                    text = "You are about to add items to the warehouse: " + message + ". Do you want to proceed?";
                } else if (type === 'Issue') {
                    text = "You are about to issue items to the warehouse: " + message + ". Do you want to proceed?";
                }

                Swal.fire({
                    title: "Confirm Warehouse Selection",
                    text: text,
                    icon: "warning",
                    showDenyButton: true,
                    confirmButtonText: 'Yes, proceed',
                    denyButtonText: 'Cancel',
                    dangerMode: true,
                }).then((result) => {
                    if (result.isConfirmed) {
                        @this.itemsSave({{ $inventory_id }})
                    } else if (result.isDenied) {
                        return false
                    }
                })
            }
        </script>
    @endpush
</div>
