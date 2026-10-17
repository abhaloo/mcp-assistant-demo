<div>
    <div class="card border-0 shadow-sm">
        <div class="card-body">
            
            {{-- Product Search --}}
            <div class="mb-3">
                <label>Add Product</label>
                @livewire('product-search-component')
            </div>

            {{-- Items Table --}}
            <div class="table-responsive mb-3">
                <table class="table table-bordered table-sm bg-gray-100 mb-0">
                    <thead class="table-light">
                    <tr>
                        <th style="width: 25%">Item</th>
                        <th style="width: 10%">Unit</th>
                        <th style="width: 10%">Qty</th>
                        <th style="width: 15%">Price</th>
                        <th style="width: 15%">Tax</th>
                        <th style="width: 15%">Discount</th>
                        <th style="width: 15%">Subtotal</th>
                        <th style="width: 5%"></th>
                    </tr>
                    </thead>
                    <tbody>
                    @forelse($bill_items as $uuid => $item)
                        <tr wire:key="item-{{ $uuid }}">
                            <td class="align-middle">
                                <strong>{{ $item['name'] }}</strong>
                                <small class="d-block text-muted">{{ $item['description'][0] ?? '' }}</small>
                            </td>
                            <td class="align-middle">{{ $item['unit'] }}</td>
                            <td class="align-middle">
                                <input type="number" step="0.01" class="form-control form-control-sm"
                                       wire:model.blur="bill_items.{{ $uuid }}.quantity"
                                       wire:change="updateItem('{{ $uuid }}')">
                            </td>
                            <td class="align-middle">
                                <input type="number" step="0.01" class="form-control form-control-sm"
                                       wire:model.blur="bill_items.{{ $uuid }}.price"
                                       wire:change="updateItem('{{ $uuid }}')">
                            </td>
                            <td class="align-middle">
                                <select class="form-control form-control-sm {{ $vat_excepted ? 'bg-light text-muted' : '' }}"
                                        wire:model.live="bill_items.{{ $uuid }}.tax_id" 
                                        wire:change="changeTax('{{ $uuid }}')"
                                        {{ $vat_excepted ? 'disabled' : '' }}>
                                    <option value="0">Zero Tax</option>
                                    @foreach($taxes as $tax)
                                        <option value="{{ $tax->id }}">{{ $tax->rate }}%</option>
                                    @endforeach
                                </select>
                            </td>
                            <td class="align-middle">
                                <input type="number" step="0.01" class="form-control form-control-sm"
                                       wire:model.blur="bill_items.{{ $uuid }}.discount"
                                       wire:change="updateItem('{{ $uuid }}')">
                            </td>
                            <td class="align-middle text-end fw-bold">
                                {{ number_format(((float)$bill_items[$uuid]['price'] * (float)$bill_items[$uuid]['quantity']) - (float)$bill_items[$uuid]['discount'], 2) }}
                            </td>
                            <td class="align-middle text-center">
                                <button class="btn btn-sm btn-outline-danger border-0" 
                                        wire:click="removeItem('{{ $uuid }}')">
                                    <i class="fa fa-trash"></i>
                                </button>
                            </td>
                        </tr>
                    @empty
                        <tr>
                            <td colspan="8" class="text-center py-3 text-muted">
                                <i class="fa fa-shopping-basket mb-2"></i><br>
                                No items added yet. Search for a product above.
                            </td>
                        </tr>
                    @endforelse
                    </tbody>
                </table>
            </div>
            
            @error('bill_items') <span class="text-danger small d-block mb-3">{{ $message }}</span> @enderror

            {{-- Summary & Options --}}
            <div class="row">
                <div class="col-md-7">
                     <div class="row g-2">
                        <div class="col-md-6">
                            <label class="form-label small">Invoice Date</label>
                            <input type="date" class="form-control form-control-sm" wire:model.live="value_date">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label small">Invoice Number</label>
                            <input type="text" class="form-control form-control-sm" wire:model.live="invoice_number">
                        </div>
                        <div class="col-12 mt-2">
                            <textarea class="form-control form-control-sm" 
                                      wire:model.live="descriptions" 
                                      placeholder="Notes & Order Info..." 
                                      rows="3"></textarea>
                        </div>
                        <div class="col-12 mt-2">
                            <div class="form-check form-check-inline">
                                <input class="form-check-input" type="checkbox" wire:model.live="vat_included" id="orphan_vat_included">
                                <label class="form-check-label small" for="orphan_vat_included">
                                    VAT Included
                                </label>
                            </div>
                            <div class="form-check form-check-inline">
                                <input class="form-check-input" type="checkbox" wire:model.live="vat_excepted" id="orphan_vat_excepted">
                                <label class="form-check-label small" for="orphan_vat_excepted">
                                    VAT Exempt
                                </label>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-5">
                    <div class="bg-gray-100 p-3 rounded">
                        <div class="d-flex justify-content-between mb-1">
                            <span>Subtotal:</span>
                            <span class="fw-bold">{{ number_format($total_value, 2) }}</span>
                        </div>
                        <div class="d-flex justify-content-between mb-1 {{ $total_discount > 0 ? 'text-danger' : 'text-muted' }}">
                            <span>Discount:</span>
                            <span>-{{ number_format($total_discount, 2) }}</span>
                        </div>
                        <div class="d-flex justify-content-between mb-1 {{ $vat_excepted ? 'text-muted text-decoration-line-through' : '' }}">
                            <span>Tax:</span>
                            <span>{{ number_format($total_tax, 2) }}</span>
                        </div>
                        <hr class="my-2">
                        <div class="d-flex justify-content-between fs-5 text-success">
                            <strong>Total:</strong>
                            <strong>{{ number_format($total_value + $total_tax, 2) }}</strong>
                        </div>
                    </div>
                    
                    <button class="btn btn-success w-100 mt-3" 
                            wire:click="saveInvoice" 
                            wire:loading.attr="disabled">
                        <span wire:loading.remove><i class="fa fa-check-circle"></i> Create & Link Invoice</span>
                        <span wire:loading><i class="fa fa-spinner fa-spin"></i> Creating...</span>
                    </button>
                    
                    @if($processing)
                        <div class="alert alert-info py-1 px-2 mt-2 small text-center mb-0">
                            Processing... please wait.
                        </div>
                    @endif
                </div>
            </div>

        </div>
    </div>
</div>
