<div>
    @include('livewire.partials.select2-assets')
    <div class="mcp">
        <x-mcp.page-header>
            <x-slot:titleSlot>
                @if($bill_id)
                    Edit <strong>{{ $type }}</strong>
                @else
                    Create <strong>{{ $type }}</strong>
                @endif
            </x-slot:titleSlot>
            <x-slot:actions>
                @if($from_order)
                    <x-mcp.btn variant="secondary" size="sm" icon="fa-chevron-left"
                        :href="route('customer-orders.show', $from_order)">
                        Back to Order
                    </x-mcp.btn>
                @else
                    <x-back-link :fallback="route('bill.index', ['type' => $type])" class="mcp-btn mcp-btn--secondary mcp-btn--sm">
                        <i class="fa fa-chevron-left"></i>
                        Back
                    </x-back-link>
                @endif
            </x-slot:actions>
        </x-mcp.page-header>

        <x-mcp.card title="Bill details" class="mb-3">
            <div class="row d-flex justify-content-between gy-3">
                <div class="col-md-4">
                    <label for="customer">Customer</label>
                    <select class="form-control" wire:model.live="customer_id" id="customer">
                        <option value="">Choose a customer..</option>
                        @forelse($customers as $customer)
                            <option value="{{$customer->id}}">{{$customer->name}}</option>
                        @empty
                        @endforelse
                    </select>
                    @error('customer_id')
                    <div class="text-danger fw-bold">
                        {{ $message }}
                    </div>
                    @enderror
                </div>
                <div class="col-md-3">
                    <label for="warehouse">Warehouse</label>
                    <select class="form-control" wire:model.live="warehouse_id" id="warehouse">
                        <option value="">Choose a warehouse..</option>
                        @forelse($warehouses as $warehouse)
                            @if($warehouse->department_id != null)
                                @if($warehouse->deparment_id == auth()->user()->department_id)
                                    <option value="{{$warehouse->id}}">{{$warehouse->name}}</option>
                                @endif
                            @else
                                <option value="{{$warehouse->id}}">{{$warehouse->name}}</option>
                            @endif
                        @empty
                        @endforelse
                    </select>
                    @error('warehouse_id')
                    <div class="text-danger fw-bold">
                        {{ $message }}
                    </div>
                    @enderror
                </div>
                <div class="col-md-5 mb-2">
                    @livewire('product-search-component')
                </div>
            </div>

            <div class="row mb-3 mt-3">
                <div class="col-md-6">
                    <div class="form-check form-check-inline">
                        <input class="form-check-input" type="checkbox" wire:model.live="vat_included" id="vat_included">
                        <label class="form-check-label" for="vat_included">
                            <i class="fa fa-check-circle text-success"></i> VAT Included
                        </label>
                    </div>
                    <div class="form-check form-check-inline">
                        <input class="form-check-input" type="checkbox" wire:model.live="vat_excepted" id="vat_excepted">
                        <label class="form-check-label" for="vat_excepted">
                            <i class="fa fa-times-circle text-danger"></i> VAT Exempted
                        </label>
                    </div>
                </div>
                <div class="col-md-6">
                    @if($vat_included)
                        <small class="text-info"><i class="fa fa-info-circle"></i> VAT is included in the item prices</small>
                    @elseif($vat_excepted)
                        <small class="text-warning"><i class="fa fa-exclamation-triangle"></i> This transaction is exempt from VAT</small>
                    @endif
                </div>
            </div>
        </x-mcp.card>

        @if(!empty($previouslyBilled))
            <x-mcp.card title="Previously billed on this order" class="mb-3">
                <div class="small col-md-4">
                    @foreach($previouslyBilled as $prevBill)
                        <div class="d-flex justify-content-between align-items-center py-1 {{ !$loop->last ? 'border-bottom' : '' }}">
                            <div>
                                <x-badge :color="$prevBill['type'] == 'Invoice' ? 'success' : 'info'" class="me-1">
                                    {{ $prevBill['type'] }}
                                </x-badge>
                                <span class="fw-bold me-2">#{{ $prevBill['number'] }}</span>
                                <span class="text-muted">&mdash;</span>
                                @foreach($prevBill['items'] as $item)
                                    <span class="ms-1">{{ $item['name'] }} &times;{{ $item['quantity'] }}{{ !$loop->last ? ',' : '' }}</span>
                                @endforeach
                            </div>
                            <span class="fw-bold text-nowrap ms-3">
                                {{ $prevBill['currency_code'] }} {{ number_format($prevBill['total'], 2) }}
                            </span>
                        </div>
                    @endforeach
                </div>
            </x-mcp.card>
        @endif

        <x-mcp.card title="Products" class="mb-3">
            <div class="row z-0">
                <div class="col-md-12">
                    @error('bill_items')
                    <p class="text-danger fw-bold">
                        {{ $message }}
                    </p>
                    @enderror

                    <div class="table-responsive d-none d-md-block">
                        <table class="mcp-table mcp-table--form">
                            <thead>
                            <tr>
                                <th>Item</th>
                                <th>Description</th>
                                <th>Unit</th>
                                <th>Qty</th>
                                <th>Price</th>
                                <th>Tax</th>
                                <th>Discount</th>
                                <th colspan="2">Subtotal</th>
                            </tr>
                            </thead>

                            <tbody>
                            @forelse($bill_items as $item)
                                <tr>
                                    <td class="align-bottom">{{$item['name']}}</td>
                                    <td>
                                        @include('livewire.partials.bill-item-description', ['item' => $item])
                                    </td>
                                    <td class="align-bottom">
                                        {{$bill_items[$item['id']]['unit']}}
                                    </td>
                                    <td class="align-bottom">
                                        <input type="number" step="0.01" class="form-control form-control-sm boxed"
                                               wire:model.live="bill_items.{{$item['id']}}.quantity">
                                    </td>

                                    <td class="align-bottom">
                                        <input type="number" step="0.01" class="form-control form-control-sm"
                                               wire:model.live="bill_items.{{$item['id']}}.price">
                                    </td>

                                    <td class="align-bottom">
                                        <select class="form-control form-control-sm {{ $vat_excepted ? 'bg-light text-muted' : '' }}"
                                                wire:model.live="bill_items.{{$item['id']}}.tax_id" wire:change="changeTax('{{$item['id']}}')"
                                                {{ $vat_excepted ? 'disabled' : '' }}>
                                            <option value="0">Zero Tax</option>
                                            @forelse($taxes as $tax)
                                                <option value="{{$tax->id}}">{{$tax->rate}}%</option>
                                            @empty
                                            @endforelse
                                        </select>
                                        @if($vat_excepted)
                                            <small class="text-muted">Exempt</small>
                                        @endif
                                    </td>

                                    <td class="align-bottom">
                                        <input type="number" step="0.01" class="form-control form-control-sm"
                                               wire:model.live="bill_items.{{$item['id']}}.discount">
                                    </td>

                                    <td class="text-end text-success fw-bold align-bottom">
                                        {{number_format(((float)$item['price'] * (float)$item['quantity']), 2)}}
                                    </td>

                                    <td class="align-bottom">
                                        <x-mcp.icon-btn icon="fa-trash" variant="danger" title="Remove item"
                                            wire:click="itemDelete('{{$item['id']}}')" />
                                    </td>

                                </tr>
                            @empty
                                <tr>
                                    <td colspan="9" class="text-center text-muted py-3">
                                        No items yet — search for a product above to add one.
                                    </td>
                                </tr>
                            @endforelse
                            </tbody>
                        </table>
                    </div>

                    {{-- ==================== MOBILE CARD STACK (visible only on mobile) ==================== --}}
                    <div class="d-md-none">
                        @forelse($bill_items as $item)
                            <x-mcp.mobile-card>
                                <div class="mcp-mobile-card__head">
                                    <span class="fw-semibold">{{$item['name']}}</span>
                                    <x-mcp.icon-btn icon="fa-trash" variant="danger" title="Remove item"
                                        wire:click="itemDelete('{{$item['id']}}')" />
                                </div>

                                <div class="small text-muted mb-2">{{$bill_items[$item['id']]['unit']}}</div>

                                <div class="row g-2">
                                    <div class="col-6">
                                        <label class="form-label small mb-1">Qty</label>
                                        <input type="number" step="0.01" class="form-control form-control-sm boxed"
                                               wire:model.live="bill_items.{{$item['id']}}.quantity">
                                    </div>
                                    <div class="col-6">
                                        <label class="form-label small mb-1">Price</label>
                                        <input type="number" step="0.01" class="form-control form-control-sm"
                                               wire:model.live="bill_items.{{$item['id']}}.price">
                                    </div>
                                    <div class="col-6">
                                        <label class="form-label small mb-1">Tax</label>
                                        <select class="form-control form-control-sm {{ $vat_excepted ? 'bg-light text-muted' : '' }}"
                                                wire:model.live="bill_items.{{$item['id']}}.tax_id" wire:change="changeTax('{{$item['id']}}')"
                                                {{ $vat_excepted ? 'disabled' : '' }}>
                                            <option value="0">Zero Tax</option>
                                            @forelse($taxes as $tax)
                                                <option value="{{$tax->id}}">{{$tax->rate}}%</option>
                                            @empty
                                            @endforelse
                                        </select>
                                        @if($vat_excepted)
                                            <small class="text-muted">Exempt</small>
                                        @endif
                                    </div>
                                    <div class="col-6">
                                        <label class="form-label small mb-1">Discount</label>
                                        <input type="number" step="0.01" class="form-control form-control-sm"
                                               wire:model.live="bill_items.{{$item['id']}}.discount">
                                    </div>
                                </div>

                                <div class="mt-2">
                                    <label class="form-label small mb-1">Description</label>
                                    @include('livewire.partials.bill-item-description', ['item' => $item])
                                </div>

                                <div class="mcp-mobile-card__amounts">
                                    <span class="text-muted">Subtotal</span>
                                    <span class="fw-bold text-success">{{number_format(((float)$item['price'] * (float)$item['quantity']), 2)}}</span>
                                </div>
                            </x-mcp.mobile-card>
                        @empty
                            <x-mcp.empty title="No items yet" hint="Search for a product above to add one." />
                        @endforelse
                    </div>

                    <div class="row mt-4 gy-3">
                        <div class="col-12 col-md-8">
                            <label for="descriptions">Notes & order info</label>
                            <textarea class="form-control" id="descriptions" wire:model.live="descriptions" placeholder="Notes & Order Info."></textarea>
                            <div class="row mt-3 gy-3">
                                <div class="col-12 col-md-6">
                                    <label>{{$type}} Date</label>
                                    <input type="date" class="form-control" wire:model.live="value_date">
                                </div>
                                <div class="col-12 col-md-3">
                                    <label>{{$type}} Number</label>
                                    <input type="text" class="form-control" wire:model.live="invoice_number">
                                </div>
                                <div class="col-12 col-md-3">
                                    <label>Preferred Method</label>
                                    <select class="form-control" wire:model.live="preferred_method">
                                        <option value="">--Select Account--</option>
                                        @forelse($accounts as $account)
                                            <option value="{{ $account->id }}">{{ $account->name }}</option>
                                        @empty
                                        @endforelse
                                    </select>

                                </div>
                            </div>

                        </div>

                        @if($currency && $currency->id != 1)
                            <div class="col-12 col-md-4 text-end fs-5 fw-bold">
                                <p>Totals({{ $currency->currency_code }}): {{number_format($total_value, 2)}} </p>
                                <p>Totals({{ $home_currency->currency_code }}): {{number_format($total_value * $currency->rate  , 2)}}</p>
                                <input type="number" wire:model.live="total_discount" step="0.01" class="form-control" placeholder="Invoice Discount">
                            </div>
                        @else
                            <div class="col-12 col-md-4 text-end fs-6 lh-sm">
                                <p>Totals: {{number_format($total_value, 2)}}</p>
                                <p class="text-danger">Discount: {{number_format($total_discount, 2)}}</p>
                                @if($vat_excepted)
                                    <p class="text-warning"><i class="fa fa-exclamation-triangle"></i> Tax (Exempt): {{number_format(0, 2)}}</p>
                                @else
                                    <p class="text-secondary">Tax: {{number_format($total_tax, 2)}}</p>
                                @endif
                                @if($vat_included)
                                    <p class="text-info"><i class="fa fa-info-circle"></i> VAT Included in Prices</p>
                                @endif
                                <p class="text-success fs-5 fw-bold border-top pt-2 mt-2">Grand Total: {{number_format($total_tax + $total_value, 2)}}</p>
                            </div>
                        @endif

                    </div>

                    @if (session()->has('message'))
                        <div class="alert alert-success">
                            {{ session('message') }}
                        </div>
                    @endif

                </div>
            </div>
        </x-mcp.card>

        <div class="mcp-save-row mb-3">
            <x-loading-button
                target="itemsSave"
                :label="($bill_id ? 'Update ' : 'Create ') . $type"
                icon="fa-receipt"
                :loading-label="$bill_id ? 'Updating...' : 'Creating...'"
                class="mcp-btn mcp-btn--primary"
                wire:click="itemsSave({{$bill_id}})"
            />
        </div>
    </div>

    <!-- Delete Modal -->
    <div wire:ignore.self class="modal fade" id="deleteModalbill" data-backdrop="false" tabindex="-1" role="dialog"
         aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Delete Confirm </h5>
                </div>
                <div class="modal-body">
                    <p>Are you sure you want to delete this item?
                    </p>
                </div>
                <div class="modal-footer">
                    <x-mcp.btn type="button" variant="secondary" class="close-btn" data-coreui-dismiss="modal">Cancel</x-mcp.btn>
                    <x-mcp.btn type="button" variant="danger" class="close-modal"
                        wire:click.prevent="destroy()" data-coreui-dismiss="modal">
                        Yes, Delete
                    </x-mcp.btn>
                </div>
            </div>
        </div>
    </div>

    @push('scripts')
        <script>
            Livewire.on('closeModal', () => {
                $('#modal-bill').modal('hide')
            })

            // Initialize Select2
            document.addEventListener('customerSelected', function () {
                initSelect2()
            });
            document.addEventListener('livewire:init', function () {
                initSelect2()
            });

            function initSelect2(){
                $('#customer').select2({
                    theme: 'bootstrap-5',
                    width: '100%'
                }).on('select2:select', function (e) {
                    const data = $(this).val();
                    @this.set('customer_id', data);
                });
            }

            // Auto-grow line-item description textareas to fit their content
            function initDescriptionAutogrow(){
                document.querySelectorAll('textarea.js-autogrow').forEach(function (el) {
                    if (!el.dataset.agBound) {
                        el.addEventListener('input', function () {
                            el.style.height = 'auto';
                            el.style.height = el.scrollHeight + 'px';
                        });
                        el.dataset.agBound = '1';
                    }
                    el.style.height = 'auto';
                    el.style.height = el.scrollHeight + 'px';
                });
            }
            document.addEventListener('livewire:init', initDescriptionAutogrow);
            if (window.Livewire) {
                window.Livewire.hook('morph.updated', initDescriptionAutogrow);
            }
        </script>
    @endpush
</div>
