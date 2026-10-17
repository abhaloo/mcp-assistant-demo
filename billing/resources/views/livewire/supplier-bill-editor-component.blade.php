<div>
    @include('livewire.partials.select2-assets')
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">
                <strong>@if($inventory_id) Edit @else Create @endif</strong>
                @if($is_goods_costing)
                    Goods Costing Bill
                @else
                    Supplier Bill
                @endif
            </h1>
        </div>
        <div class="col text-end">
            <a href="{{ route('supplier-bill.index') }}" class="btn btn-outline-secondary btn-sm">
                <i class="fa fa-chevron-left me-1"></i> Back
            </a>
        </div>
    </div>

    @if($is_goods_costing)
        {{-- GOODS COSTING MODE --}}
        @php
            $selectedCurrency = $currency_id ? $currencies->where('id', $currency_id)->first() : null;
            $currencySymbol = $selectedCurrency?->symbol ?? 'USD';
            $isForeignCurrency = $currency_id && $currency_id != 1;
        @endphp
        <div class="row">
            {{-- Left Column: Receive Entries Card --}}
            <div class="col-md-4">
                {{-- Linked Receive Entries Card --}}
                <div class="card border-0 shadow-sm mb-3">
                    <div class="card-header bg-light d-flex justify-content-between align-items-center">
                        <strong><i class="fa fa-truck me-2 text-success"></i>Receive Entries</strong>
                        @if(!empty($linked_receive_entries))
                            <x-badge color="success">{{ count($linked_receive_entries) }} linked</x-badge>
                        @endif
                    </div>
                    <div class="card-body">
                        @if(!empty($linked_receive_entries))
                            {{-- Show linked entries --}}
                            <div class="list-group list-group-flush mb-3">
                                @foreach($linked_receive_entries as $entry)
                                    <div class="list-group-item px-0 py-2 d-flex justify-content-between align-items-start">
                                        <div>
                                            <strong class="d-block">Entry #{{ $entry['id'] }}</strong>
                                            <small class="text-muted">
                                                {{ \Carbon\Carbon::parse($entry['inventory_date'])->format('d M Y') }}
                                                @if($entry['warehouse_name'] ?? false)
                                                    &bull; {{ $entry['warehouse_name'] }}
                                                @endif
                                                @if($entry['reference'] ?? false)
                                                    &bull; {{ $entry['reference'] }}
                                                @endif
                                            </small>
                                            <x-badge color="info" class="ms-1">{{ $entry['items_count'] ?? 0 }} items</x-badge>
                                        </div>
                                        <button type="button"
                                                wire:click="removeReceiveEntry({{ $entry['id'] }})"
                                                class="btn btn-link btn-sm text-danger p-0"
                                                title="Unlink">
                                            <i class="fa fa-times"></i>
                                        </button>
                                    </div>
                                @endforeach
                            </div>

                            <hr class="my-2">
                            <small class="text-muted d-block mb-2">Add more entries from same supplier:</small>
                        @else
                            <div class="text-center py-3">
                                <i class="fa fa-truck fa-2x mb-2 text-muted"></i>
                                <p class="mb-0 text-muted">No receive entries linked yet</p>
                                <small class="text-muted">Search and link approved receive entries to start costing.</small>
                            </div>
                        @endif

                        {{-- Search for more entries --}}
                        @livewire('receive-entry-search-component', [
                            'supplierId' => $supplier_id,
                            'excludeIds' => array_keys($linked_receive_entries),
                            'placeholder' => empty($linked_receive_entries) ? 'Search by entry ID or supplier...' : 'Search for more entries...',
                        ], key('receive-entry-search-' . ($supplier_id ?? 'none')))
                    </div>
                </div>

                {{-- Supplier & Costing Settings Card --}}
                <div class="card border-0 shadow-sm mb-3">
                    <div class="card-header bg-light">
                        <strong><i class="fa fa-cog me-2"></i>Costing Settings</strong>
                    </div>
                    <div class="card-body">
                        {{-- Supplier Info (shows when entries linked) --}}
                        @if($supplier_id)
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Supplier</label>
                            <div class="form-control form-control-sm bg-light">
                                <i class="fa fa-building me-2 text-muted"></i>{{ $supplier_name ?? 'Unknown Supplier' }}
                            </div>
                        </div>
                        @endif

                        <div class="mb-3">
                            <label class="form-label small fw-bold">Supplier Invoice No.</label>
                            <input type="text" class="form-control form-control-sm" wire:model.blur="supplier_invoice_no"
                                   placeholder="e.g. INV-12345">
                        </div>

                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label small fw-bold">Currency</label>
                                <select class="form-select form-select-sm" wire:model.live="currency_id">
                                    <option value="">Select Currency</option>
                                    @foreach($currencies as $curr)
                                        <option value="{{ $curr->id }}">{{ $curr->name }} ({{ $curr->symbol }})</option>
                                    @endforeach
                                </select>
                            </div>
                            @if($currency_id && $currency_id != 1)
                            <div class="col-md-6 mb-3">
                                <label class="form-label small fw-bold">Exchange Rate</label>
                                <div class="input-group input-group-sm">
                                    <span class="input-group-text">1 {{ $currencies->where('id', $currency_id)->first()?->symbol ?? 'FCY' }} =</span>
                                    <input type="number" class="form-control" wire:model.blur="exchange_rate"
                                           step="0.0001" min="0" placeholder="1.0000">
                                    <span class="input-group-text">USD</span>
                                </div>
                            </div>
                            @endif
                        </div>

                        <hr class="my-3">
                        <label class="form-label small fw-bold mb-2">Additional Charges @if($isForeignCurrency)<span class="text-muted fw-normal">({{ $currencySymbol }})</span>@endif</label>

                        <div class="row">
                            <div class="col-md-4 mb-2">
                                <label class="form-label small text-muted mb-1">Clearing</label>
                                <input type="number" class="form-control form-control-sm" wire:model.blur="clearing_charges"
                                       step="0.01" min="0" placeholder="0.00">
                            </div>
                            <div class="col-md-4 mb-2">
                                <label class="form-label small text-muted mb-1">Freight</label>
                                <input type="number" class="form-control form-control-sm" wire:model.blur="freight_charges"
                                       step="0.01" min="0" placeholder="0.00">
                            </div>
                            <div class="col-md-4 mb-2">
                                <label class="form-label small text-muted mb-1">Other</label>
                                <input type="number" class="form-control form-control-sm" wire:model.blur="other_charges"
                                       step="0.01" min="0" placeholder="0.00">
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {{-- Right Column: Items & Summary --}}
            <div class="col-md-8">
                {{-- Items Table Card --}}
                <div class="card border-0 shadow-sm mb-3">
                    <div class="card-header bg-light d-flex justify-content-between align-items-center">
                        <strong>Items</strong>
                        @if(!empty($inventory_items))
                            <x-badge color="secondary">{{ count($inventory_items) }} items</x-badge>
                        @endif
                    </div>
                    <div class="card-body p-0">
                        @if(!empty($inventory_items))
                            @php
                                // Check if any item has unit hierarchy for dynamic columns
                                $hasAnyHierarchy = collect($inventory_items)->contains(fn($item) =>
                                    ($item['category_config'] ?? false) &&
                                    !empty($item['category_config']['count_levels']) &&
                                    ($item['intermediate_unit_label'] ?? false)
                                );
                                $hierarchyItem = collect($inventory_items)->first(fn($item) =>
                                    ($item['category_config'] ?? false) &&
                                    !empty($item['category_config']['count_levels']) &&
                                    ($item['intermediate_unit_label'] ?? false)
                                );
                            @endphp
                            <div class="table-responsive">
                                <table class="table table-sm table-hover mb-0">
                                    <thead>
                                        <tr class="text-muted small">
                                            <th class="fw-medium">Item</th>
                                            <th class="fw-medium" style="min-width: 110px;">Invoice Value @if($isForeignCurrency)({{ $currencySymbol }})@endif</th>
                                            @if($hasAnyHierarchy && $hierarchyItem)
                                                <th class="fw-medium" style="min-width: 100px;">{{ $hierarchyItem['intermediate_unit_label'] }}s/{{ $hierarchyItem['costing_unit_label'] }}</th>
                                                <th class="fw-medium" style="min-width: 100px;">{{ $hierarchyItem['stock_unit_label'] }}s/{{ $hierarchyItem['intermediate_unit_label'] }}</th>
                                            @endif
                                            <th class="fw-medium text-center">Quantity</th>
                                            <th class="fw-medium text-end">Allocated</th>
                                            @if($hasAnyHierarchy && $hierarchyItem)
                                                <th class="fw-medium text-end">Cost/{{ $hierarchyItem['intermediate_unit_label'] }}</th>
                                            @endif
                                            <th class="fw-medium text-end">Unit Cost (USD)</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        @foreach($inventory_items as $key => $item)
                                            @php
                                                $itemHasHierarchy = ($item['category_config'] ?? false) &&
                                                    !empty($item['category_config']['count_levels']) &&
                                                    ($item['intermediate_unit_label'] ?? false);
                                            @endphp
                                            <tr>
                                                <td>
                                                    <span class="small">{{ $item['product_name'] }}</span>
                                                    @if($item['gsm'] || $item['size'])
                                                        <div class="small text-muted">
                                                            @if($item['gsm']){{ $item['gsm'] }}gsm @endif
                                                            @if($item['size']){{ $item['size'] }}@endif
                                                        </div>
                                                    @endif
                                                </td>
                                                <td>
                                                    <input type="number"
                                                           class="form-control form-control-sm"
                                                           wire:model.blur="inventory_items.{{ $key }}.invoice_value_foreign"
                                                           wire:change="calculateCosts"
                                                           step="0.01" min="0"
                                                           placeholder="0.00">
                                                </td>
                                                @if($hasAnyHierarchy && $hierarchyItem)
                                                    @if($itemHasHierarchy)
                                                        <td>
                                                            <input type="number"
                                                                   class="form-control form-control-sm text-center"
                                                                   step="0.01"
                                                                   wire:model.blur="inventory_items.{{ $key }}.costing_to_intermediate"
                                                                   wire:change="updateItemConversionFactor('{{ $key }}', 'costing_to_intermediate', $event.target.value)">
                                                        </td>
                                                        <td>
                                                            <input type="number"
                                                                   class="form-control form-control-sm text-center"
                                                                   step="0.01"
                                                                   wire:model.blur="inventory_items.{{ $key }}.intermediate_to_stock"
                                                                   wire:change="updateItemConversionFactor('{{ $key }}', 'intermediate_to_stock', $event.target.value)">
                                                        </td>
                                                    @else
                                                        <td class="text-center text-muted">-</td>
                                                        <td class="text-center text-muted">-</td>
                                                    @endif
                                                @endif
                                                <td class="text-center small">
                                                    {{ number_format($item['quantity'] ?? 0, 2) }}
                                                    <span class="text-muted d-block">{{ $item['stock_unit_label'] ?? 'Units' }}</span>
                                                </td>
                                                <td class="text-end small">
                                                    {{ number_format($item['allocated_charges'] ?? 0, 2) }}
                                                </td>
                                                @if($hasAnyHierarchy && $hierarchyItem)
                                                    <td class="text-end small">
                                                        @if($itemHasHierarchy && ($item['cost_per_intermediate'] ?? 0) > 0)
                                                            {{ number_format($item['cost_per_intermediate'] ?? 0, 2) }}
                                                        @else
                                                            <span class="text-muted">-</span>
                                                        @endif
                                                    </td>
                                                @endif
                                                <td class="text-end small fw-medium text-success">
                                                    {{ number_format($item['unit_cost_tzs'] ?? 0, 2) }}
                                                </td>
                                            </tr>
                                        @endforeach
                                    </tbody>
                                </table>
                            </div>
                        @else
                            <div class="text-center py-5">
                                <i class="fa fa-inbox fa-3x mb-3 text-secondary"></i>
                                <h5 class="fw-normal text-muted">No items to cost</h5>
                                <p class="mb-0 text-muted">Link receive entries from the left panel to see items here.</p>
                            </div>
                        @endif
                    </div>
                </div>

                {{-- Costing Summary Card --}}
                <div class="card border-0 shadow-sm mb-3">
                    <div class="card-header bg-light">
                        <strong><i class="fa fa-calculator me-2"></i>Costing Summary</strong>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6">
                                <table class="table table-sm table-borderless mb-0">
                                    <tr>
                                        <td class="text-muted">Invoice Total @if($isForeignCurrency)({{ $currencySymbol }})@endif:</td>
                                        <td class="text-end fw-medium">{{ number_format($total_invoice_foreign, 2) }}</td>
                                    </tr>
                                    <tr>
                                        <td class="text-muted">Total Charges @if($isForeignCurrency)({{ $currencySymbol }})@endif:</td>
                                        <td class="text-end fw-medium">{{ number_format($clearing_charges + $freight_charges + $other_charges, 2) }}</td>
                                    </tr>
                                    <tr>
                                        <td class="text-muted">Allocation Ratio:</td>
                                        <td class="text-end fw-medium">{{ number_format($allocation_ratio, 4) }}</td>
                                    </tr>
                                    @if($currency_id && $currency_id != 1)
                                    <tr>
                                        <td class="text-muted">Exchange Rate:</td>
                                        <td class="text-end fw-medium">{{ number_format($exchange_rate, 2) }}</td>
                                    </tr>
                                    @endif
                                </table>
                            </div>
                            <div class="col-md-6">
                                <div class="bg-success bg-opacity-10 rounded p-3 text-center">
                                    <small class="text-muted d-block mb-1">Total Landed Cost (USD)</small>
                                    <span class="fs-3 fw-bold text-success">{{ number_format($total_landed_cost_tzs, 2) }}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                {{-- Save Button --}}
                <div class="d-flex justify-content-end">
                    <button class="btn btn-success" wire:click="itemsSave({{ $inventory_id }})"
                            wire:loading.attr="disabled"
                            @if(empty($linked_receive_entries)) disabled @endif>
                        <span wire:loading.remove wire:target="itemsSave">
                            <i class="fa fa-save me-1"></i>
                            @if($inventory_id) Update @else Create @endif Costing Bill
                        </span>
                        <span wire:loading wire:target="itemsSave">
                            <i class="fa fa-spinner fa-spin me-1"></i> Saving...
                        </span>
                    </button>
                </div>
            </div>
        </div>

    @else
        {{-- EXPENSE BILL MODE (Original) --}}
        <div class="card card-body border-0 shadow">
            {{-- Supplier Selection --}}
            <div class="row mb-3">
                <div class="col-md-4">
                    @can('view supplier')
                        @livewire('supplier-search-component', [
                            'label' => 'Supplier',
                            'required' => true,
                            'initialSupplierId' => $supplier_id,
                        ], key('supplier-search'))
                    @endcan
                </div>
                <div class="col-md-5 pull-right mb-2">
                    @livewire('account-search-component', ['type' => 'Expenses'])
                </div>
            </div>

            <div class="row z-0">
                <div class="col-md-12">
                    @error('inventory_items')
                    <p class="text-danger font-weight-bold">
                        {{ $message }}
                    </p>
                    @enderror

                    <table class="table table-bordered table-hover table-sm bg-gray-100">
                        <tr>
                            <th>Item/ Account</th>
                            <th>Description</th>
                            <th>Qty</th>
                            <th>Cost</th>
                            <th>Tax</th>
                            <th colspan="2">Subtotal</th>
                        </tr>

                        <tbody>
                        @forelse($inventory_items as $item)
                            <tr>
                                <td>{{$item['name']}}</td>
                                <td>
                                    <input type="text" class="form-control form-control-sm boxed"
                                           wire:model.live="inventory_items.{{$item['id']}}.description">
                                </td>
                                <td>
                                    <input type="number" step="0.01" class="form-control form-control-sm boxed"
                                           wire:model.live="inventory_items.{{$item['id']}}.quantity">
                                </td>

                                <td>
                                    <input type="number" step="0.01" class="form-control form-control-sm"
                                           wire:model.live="inventory_items.{{$item['id']}}.cost">
                                </td>

                                <td style="vertical-align:bottom;">
                                    <select class="form-control form-control-sm"
                                            wire:model.live="inventory_items.{{$item['id']}}.tax_id" wire:change="changeTax('{{$item['id']}}')">
                                        <option value="0">Zero Tax</option>
                                        @forelse($taxes as $tax)
                                            <option value="{{$tax->id}}">{{$tax->rate}}%</option>
                                        @empty
                                        @endforelse
                                    </select>
                                </td>


                                <td class="text-right text-success font-weight-bold">
                                    {{number_format((intval($item['cost']) * intval($item['quantity'])), 2)}}
                                </td>

                                <td>
                                    <a href="#" class="btn btn-outline-danger text-dark btn-sm"
                                       wire:click="itemDelete({{$item['id']}})"
                                    ><i class="fa fa-trash"></i> </a>
                                </td>

                            </tr>
                        @empty
                            <tr>
                                <td colspan="7" class="text-center py-4 text-muted">
                                    No items added. Use the account search above to add expense items.
                                </td>
                            </tr>
                        @endforelse
                        </tbody>
                    </table>
                    <table class="table">
                        <tr class="text-right">
                            <th colspan="5">
                                <div class="row bg-gray-100">
                                    <div class="col-8"></div>
                                    <div class="col-4 text-end" style="font-size: 20px;font-weight: bold;line-height: 10px">
                                        <p>Totals: {{number_format($total_value, 2)}}</p>
                                        <p class="text-secondary">Tax: {{number_format($total_tax, 2)}}</p>
                                        <p class="text-success">Grand Total: {{number_format($total_value + $total_tax, 2)}}</p>
                                    </div>
                                </div>

                            </th>


                        </tr>
                    </table>

                    @if (session()->has('message'))
                        <div class="alert alert-success">
                            {{ session('message') }}
                        </div>
                    @endif

                </div>
            </div>

            {{-- Save Button --}}
            <div class="d-flex justify-content-end mt-3">
                <button class="btn btn-secondary" wire:click="itemsSave({{$inventory_id}})"
                        wire:loading.attr="disabled">
                    <span wire:loading.remove wire:target="itemsSave">
                        <i class="fa fa-save me-1"></i>
                        @if($inventory_id) Update @else Create @endif Expense Bill
                    </span>
                    <span wire:loading wire:target="itemsSave">
                        <i class="fa fa-spinner fa-spin me-1"></i> Saving...
                    </span>
                </button>
            </div>
        </div>
    @endif


    @push('scripts')
        <script>
            Livewire.on('closeModal', () => {
                $('#modal-inventory').modal('hide')
            })

            // Initialize Select2
            document.addEventListener('supplierSelected', function () {
                initSelect2()
            });
            document.addEventListener('livewire:init', function () {
                initSelect2()
            });

            function initSelect2(){
                $('#supplier').select2({
                    theme: 'bootstrap-5'
                }).on('select2:select', function (e) {
                    const data = $(this).val();
                    @this.set('supplier_id', data);
                });
            }
        </script>
    @endpush
</div>
