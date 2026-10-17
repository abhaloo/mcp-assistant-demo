<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">
                @if($inventory->is_goods_costing)
                    Goods Costing Bill <strong>#{{$inventory->id}}</strong>
                @else
                    Bill Entry <strong>#{{$inventory->id}}</strong>
                @endif
                @php
                    $amount = $inventory->amount();
                @endphp
                @if($amount > 0)
                    @if($balance == 0)
                        <x-badge color="success">PAID</x-badge>
                    @elseif($balance > 0 && $balance < $amount)
                        <x-badge color="info">PARTIAL PAID</x-badge>
                    @else
                        <x-badge color="danger">UNPAID</x-badge>
                    @endif
                @else
                    <x-badge color="secondary">No Amount</x-badge>
                @endif
            </h1>
        </div>
        <div class="col">
            <div class="float-end">
                @can('add journal entry')
                    @if($inventory->status == 'APPROVED' && $balance > 0)
                        <a href="#"
                           class="btn btn-sm btn-secondary d-inline-flex align-items-center"
                           data-coreui-toggle="modal" data-coreui-target="#modal-payment">
                            <i class="fa fa-coins me-2"></i>
                            New Payment
                        </a>
                    @endif
                @endcan

                <a href="{{ route('supplier-bill.index') }}"
                   class="btn btn-sm btn-primary d-inline-flex align-items-center ms-2">
                    <i class="fa fa-chevron-left me-2"></i>
                    Back
                </a>
            </div>
        </div>
    </div>

    <div class="card card-body border-0 shadow table-wrapper table-responsive">
        <table class="table bg-gray-100">
            <tr>
                <th>Entry Id</th>
                <th>Type</th>
                <th>Supplier</th>
                <th>Reference</th>
                <th>Created At</th>
                <th>Description</th>
            </tr>

            <tbody>
            <tr>
                <td>{{ $inventory->id }}</td>
                <td>{{ $inventory->type }}</td>
                <td>{{ $inventory->supplier->name ?? 'N/A' }}</td>
                <td>{{ $inventory->reference }}</td>
                <td>{{ $inventory->created_at->format('d-m-Y H:i:s')}}</td>
                <td>{{ $inventory->description}}</td>
            </tr>
            </tbody>

        </table>

        {{-- Payment Summary Card --}}
        @if($amount > 0)
            <div class="row mt-3 mb-3">
                <div class="col-md-6">
                    <div class="card bg-light border-0">
                        <div class="card-body py-2">
                            <div class="d-flex justify-content-between align-items-center">
                                <div>
                                    <small class="text-muted d-block">Bill Amount</small>
                                    <span class="fw-bold">USD {{ number_format($amount, 2) }}</span>
                                </div>
                                <div class="text-center">
                                    <small class="text-muted d-block">Paid</small>
                                    <span class="fw-bold text-success">USD {{ number_format($amount - $balance, 2) }}</span>
                                </div>
                                <div class="text-end">
                                    <small class="text-muted d-block">Balance</small>
                                    <span class="fw-bold {{ $balance > 0 ? 'text-danger' : 'text-success' }}">
                                        USD {{ number_format($balance, 2) }}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        @endif

        @if($inventory->is_goods_costing)
            {{-- GOODS COSTING BILL --}}

            {{-- Items Table with Unit Hierarchy (FIRST) --}}
            <h5 class="mt-3">Items</h5>
            @php
                $hasAnyHierarchy = collect($goodsCostingItems)->contains(fn($item) => $item['has_hierarchy'] ?? false);
                $hierarchyItem = collect($goodsCostingItems)->first(fn($item) => $item['has_hierarchy'] ?? false);
            @endphp
            <table class="table table-sm">
                <thead class="bg-gray-100">
                    <tr>
                        <th>Product</th>
                        <th class="text-end">Invoice Value ({{ $currencySymbol }})</th>
                        @if($hasAnyHierarchy && $hierarchyItem)
                            <th class="text-center">{{ $hierarchyItem['intermediate_unit_label'] }}s/{{ $hierarchyItem['costing_unit_label'] }}</th>
                            <th class="text-center">{{ $hierarchyItem['stock_unit_label'] }}s/{{ $hierarchyItem['intermediate_unit_label'] }}</th>
                        @endif
                        <th class="text-center">Quantity</th>
                        <th class="text-end">Allocated ({{ $currencySymbol }})</th>
                        @if($hasAnyHierarchy && $hierarchyItem)
                            <th class="text-end">Cost/{{ $hierarchyItem['intermediate_unit_label'] }} (USD)</th>
                        @endif
                        <th class="text-end">Unit Cost (USD)</th>
                    </tr>
                </thead>
                <tbody>
                @forelse($goodsCostingItems as $item)
                    <tr>
                        <td>
                            <span>{{ $item['product_name'] }}</span>
                            @if($item['gsm'] || $item['size'])
                                <div class="small text-muted">
                                    @if($item['gsm']){{ $item['gsm'] }}gsm @endif
                                    @if($item['size']){{ $item['size'] }}@endif
                                </div>
                            @endif
                        </td>
                        <td class="text-end">{{ number_format($item['invoice_value_foreign'] ?? 0, 2) }}</td>
                        @if($hasAnyHierarchy && $hierarchyItem)
                            @if($item['has_hierarchy'])
                                <td class="text-center">{{ number_format($item['costing_to_intermediate'] ?? 1, 2) }}</td>
                                <td class="text-center">{{ number_format($item['intermediate_to_stock'] ?? 1, 2) }}</td>
                            @else
                                <td class="text-center text-muted">-</td>
                                <td class="text-center text-muted">-</td>
                            @endif
                        @endif
                        <td class="text-center">
                            {{ number_format($item['quantity'] ?? 0) }}
                            <span class="text-muted d-block small">{{ $item['stock_unit_label'] ?? 'Units' }}</span>
                        </td>
                        <td class="text-end">{{ number_format($item['allocated_charges'] ?? 0, 2) }}</td>
                        @if($hasAnyHierarchy && $hierarchyItem)
                            <td class="text-end">
                                @if($item['has_hierarchy'] && ($item['cost_per_intermediate'] ?? 0) > 0)
                                    {{ number_format($item['cost_per_intermediate'] ?? 0, 2) }}
                                @else
                                    <span class="text-muted">-</span>
                                @endif
                            </td>
                        @endif
                        <td class="text-end fw-medium text-success">{{ number_format($item['unit_cost_tzs'] ?? 0, 2) }}</td>
                    </tr>
                @empty
                    <tr>
                        <td colspan="{{ $hasAnyHierarchy ? 8 : 5 }}" class="text-center text-muted">No items found</td>
                    </tr>
                @endforelse
                </tbody>
            </table>

            {{-- Costing Details (BELOW ITEMS) --}}
            <h5 class="mt-4">Costing Details</h5>
            <div class="row mb-3">
                <div class="col-md-6">
                    <table class="table table-sm table-borderless mb-0">
                        <tr>
                            <td class="text-muted">Currency:</td>
                            <td class="fw-medium">{{ $inventory->currency->name ?? 'USD' }}</td>
                        </tr>
                        <tr>
                            <td class="text-muted">Supplier Invoice:</td>
                            <td class="fw-medium">{{ $inventory->supplier_invoice_no ?? 'N/A' }}</td>
                        </tr>
                        <tr>
                            <td class="text-muted">Exchange Rate:</td>
                            <td class="fw-medium">{{ number_format($inventory->exchange_rate ?? 1, 2) }}</td>
                        </tr>
                    </table>
                </div>
                <div class="col-md-6">
                    <table class="table table-sm table-borderless mb-0">
                        <tr>
                            <td class="text-muted">Invoice Total ({{ $currencySymbol }}):</td>
                            <td class="fw-medium text-end">{{ number_format($inventory->invoice_value_foreign ?? 0, 2) }}</td>
                        </tr>
                        <tr>
                            <td class="text-muted">Clearing Charges ({{ $currencySymbol }}):</td>
                            <td class="fw-medium text-end">{{ number_format($inventory->clearing_charges ?? 0, 2) }}</td>
                        </tr>
                        <tr>
                            <td class="text-muted">Freight Charges ({{ $currencySymbol }}):</td>
                            <td class="fw-medium text-end">{{ number_format($inventory->freight_charges ?? 0, 2) }}</td>
                        </tr>
                        <tr>
                            <td class="text-muted">Other Charges ({{ $currencySymbol }}):</td>
                            <td class="fw-medium text-end">{{ number_format($inventory->other_charges ?? 0, 2) }}</td>
                        </tr>
                        <tr class="border-top">
                            <td class="text-success fw-bold">Total Landed Cost (USD):</td>
                            <td class="fw-bold text-end text-success">{{ number_format($inventory->total_landed_cost_tzs ?? 0, 2) }}</td>
                        </tr>
                    </table>
                </div>
            </div>

            {{-- Linked Receive Entries --}}
            @if($inventory->linkedReceiveEntries->count() > 0)
                <div class="mb-3">
                    <small class="text-muted">Linked Receive Entries:</small>
                    @foreach($inventory->linkedReceiveEntries as $entry)
                        <x-badge color="light" class="me-1">
                            #{{ $entry->id }} - {{ $entry->reference ?? 'No Ref' }}
                        </x-badge>
                    @endforeach
                </div>
            @endif
        @else
            {{-- EXPENSE BILL ITEMS (Original) --}}
            <h5 class="mt-3">Entry Items</h5>
            <table class="table table-sm">
                <tr class="bg-gray-100">
                    <th>Name</th>
                    <th>Description</th>
                    <th>Cost</th>
                    <th>Quantity</th>
                    <th>Tax</th>
                    <th class="text-end">Subtotal</th>
                </tr>
                <tbody>
                @forelse($inventory->inventoryItems as $item)
                    <tr>
                        <td>{{ $item->account->name ?? 'None' }}</td>
                        <td>{{ $item->description }}</td>
                        <td>{{ number_format($item->cost, 2) }}</td>
                        <td>{{ $item->quantity }}</td>
                        <td>
                            {{ number_format(
                                ( (($item->quantity * $item->cost))*($item->tax/100)),2) }}
                        </td>
                        <td class="text-end">{{ number_format($item->quantity * $item->cost, 2) }}</td>
                    </tr>
                @empty
                @endforelse
                <tr class="bg-gray-100">
                    <th colspan="5"></th>
                    <th class="fw-bold text-end text-success">{{ number_format($inventory_value, 2) }}</th>
                </tr>
                </tbody>
            </table>
        @endif

        @can('add journal entry')
            {{-- Payments section for all supplier bills --}}
            <h5 class="mt-4 text-success fw-bold">Payments</h5>
                <table class="table table-sm table-hover">
                    <tr class="bg-light">
                        <th>Method</th>
                        <th>Receipt</th>
                        <th class="text-end">Amount</th>
                        <th>Description</th>
                        <th>Paid At</th>
                        <th>Posted At</th>
                        <th>Action</th>
                    </tr>
                    <tbody class="bg-white">
                    @forelse($inventory->payments as $payment)
                        <tr>
                            <td>{{ $payment->account->name }}</td>
                            <td>{{ $payment->document_no }}</td>
                            <td class="text-end">{{ number_format($payment->credit, 2) }}</td>
                            <td>{{ $payment->description }}</td>
                            <td>{{ $payment->post_date->format('d-m-Y') }}</td>
                            <td>{{ $payment->created_at->format('d-m-Y') }}</td>
                            <td>
                                <a href="#" target="_blank" class="btn btn-primary btn-sm">
                                    <i class="fa fa-receipt"></i>
                                    Receipt</a>
                            </td>
                        </tr>
                    @empty
                        <tr>
                            <td colspan="8" class="text-center">No Payment</td>
                        </tr>
                    @endforelse
                    <tr class="bg-gray-100">
                        <th colspan="4"></th>
                        <th class="fw-bold text-end text-success">
                            {{ number_format($inventory->payments->sum('credit'), 2) }}
                        </th>
                        <th colspan="4"></th>
                    </tr>
                    </tbody>
                </table>
        @endcan

        {{-- Edit History Section (Collapsible) --}}
        @if($editHistory && $editHistory->count() > 0)
            <div class="card mt-4">
                <div class="card-header bg-light d-flex justify-content-between align-items-center"
                     data-coreui-toggle="collapse" data-coreui-target="#editHistoryCollapse"
                     aria-expanded="false" aria-controls="editHistoryCollapse"
                     style="cursor: pointer;">
                    <span>
                        <i class="fa fa-history me-2"></i>
                        <strong>Edit History</strong>
                        <x-badge color="secondary" class="ms-2">{{ $editHistory->count() }}</x-badge>
                    </span>
                    <i class="fa fa-chevron-down"></i>
                </div>
                <div class="collapse" id="editHistoryCollapse">
                    <div class="card-body p-0">
                        <table class="table table-sm table-hover mb-0">
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
                                            @if(isset($data['reference']))
                                                <x-badge color="light">Reference: {{ $data['reference'] ?? 'N/A' }}</x-badge>
                                            @endif
                                            @if(isset($data['supplier_invoice_no']))
                                                <x-badge color="light">Invoice No: {{ $data['supplier_invoice_no'] ?? 'N/A' }}</x-badge>
                                            @endif
                                            @if(isset($data['exchange_rate']))
                                                <x-badge color="light">Exchange Rate: {{ number_format($data['exchange_rate'] ?? 0, 2) }}</x-badge>
                                            @endif
                                            @if(isset($data['clearing_charges']))
                                                <x-badge color="light">Clearing: {{ number_format($data['clearing_charges'] ?? 0, 2) }}</x-badge>
                                            @endif
                                            @if(isset($data['freight_charges']))
                                                <x-badge color="light">Freight: {{ number_format($data['freight_charges'] ?? 0, 2) }}</x-badge>
                                            @endif
                                            @if(isset($data['other_charges']))
                                                <x-badge color="light">Other: {{ number_format($data['other_charges'] ?? 0, 2) }}</x-badge>
                                            @endif
                                            @if(isset($data['tax']))
                                                <x-badge color="light">Tax: {{ number_format($data['tax'] ?? 0, 2) }}</x-badge>
                                            @endif
                                            @if(isset($data['total_landed_cost_tzs']))
                                                <x-badge color="light">Landed Cost: {{ number_format($data['total_landed_cost_tzs'] ?? 0, 2) }}</x-badge>
                                            @endif
                                        </div>
                                    </td>
                                </tr>
                            @endforeach
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        @endif
    </div>

    <!-- Modal Payment -->
    <div class="modal fade" wire:ignore.self id="modal-payment" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">Add Payment</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    {{-- Balance Display --}}
                    <div class="alert alert-light border mb-4">
                        <div class="d-flex justify-content-between align-items-center">
                            <span class="text-muted">Outstanding Balance:</span>
                            <span class="fw-bold text-danger cursor-pointer"
                                  wire:click="fillBalance"
                                  style="cursor: pointer;"
                                  title="Click to fill amount">
                                USD {{ number_format($balance, 2) }}
                                <i class="fa fa-arrow-right ms-1 small"></i>
                            </span>
                        </div>
                    </div>

                    <div class="row">
                        <div class="col-12 mb-4">
                            <label for="amount">Amount <span class="text-danger">*</span></label>
                            @php
                                $effectiveAmount = $payment_currency_id != $tzsCurrencyId
                                    ? (float)$amount * (float)$payment_exchange_rate
                                    : (float)$amount;
                            @endphp
                            <input type="number" step="0.01" wire:model.live="amount"
                                   class="form-control @error('amount') is-invalid @enderror @if($effectiveAmount > $balance) is-invalid @endif" id="amount"
                                   placeholder="0.00">
                            @error('amount')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                            @if($effectiveAmount > $balance)
                                <small class="text-danger">
                                    <i class="fa fa-exclamation-triangle me-1"></i>
                                    Amount exceeds outstanding balance (USD {{ number_format($balance, 2) }})
                                </small>
                            @elseif($payment_currency_id != $tzsCurrencyId && $amount > 0)
                                <small class="text-muted">
                                    = USD {{ number_format($effectiveAmount, 2) }}
                                </small>
                            @endif
                        </div>

                        <div class="col-12 mb-4">
                            <label for="method">Method <span class="text-danger">*</span></label>
                            <select wire:model.live="method" class="form-control @error('method') is-invalid @enderror"
                                    id="method">
                                <option value="">Choose..</option>
                                @forelse($providers as $provider)
                                    <option value="{{ $provider->id }}">{{ $provider->name }}</option>
                                @empty
                                @endforelse
                            </select>
                            @error('method')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="col-12 mb-4">
                            <label for="paid_date">Paid At <span class="text-danger">*</span></label>
                            <input type="date" wire:model.live="paid_date"
                                   class="form-control @error('paid_date') is-invalid @enderror" id="paid_date">
                            @error('paid_date')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="col-md-6 mb-4">
                            <label for="payment_currency_id">Currency <span class="text-danger">*</span></label>
                            <select wire:model.live="payment_currency_id"
                                    class="form-control @error('payment_currency_id') is-invalid @enderror"
                                    id="payment_currency_id">
                                @foreach($currencies as $currency)
                                    <option value="{{ $currency->id }}">{{ $currency->currency_code }} - {{ $currency->name }}</option>
                                @endforeach
                            </select>
                            @error('payment_currency_id')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        @if($payment_currency_id != $tzsCurrencyId)
                            <div class="col-md-6 mb-4">
                                <label for="payment_exchange_rate">Exchange Rate <span class="text-danger">*</span></label>
                                <input type="number" step="0.0001" wire:model.live="payment_exchange_rate"
                                       class="form-control @error('payment_exchange_rate') is-invalid @enderror"
                                       id="payment_exchange_rate" placeholder="1 FCY = ? USD">
                                @error('payment_exchange_rate')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            </div>
                        @else
                            <div class="col-md-6 mb-4"></div>
                        @endif

                        <div class="col-12 mb-4">
                            <label for="reference_no">Payment Reference No.</label>
                            <input type="text" wire:model.live="reference_no"
                                   class="form-control @error('reference_no') is-invalid @enderror" id="reference_no"
                                   placeholder="e.g., CHQ-12345, TT-98765">
                            @error('reference_no')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="col-12 mb-4">
                            <label for="description">Description</label>
                            <input type="text" wire:model.live="description"
                                   class="form-control @error('description') is-invalid @enderror" id="description">
                            @error('description')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="col-12">
                            <div class="d-flex justify-content-end gap-2">
                                <button type="button" class="btn btn-secondary"
                                        data-coreui-dismiss="modal">
                                    Close
                                </button>
                                <button type="button" wire:click.prevent="store" class="btn btn-primary">
                                    <i class="fa fa-plus me-1"></i> Add Payment
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!-- End of Modal Content -->

    @push('scripts')
    <script>
        Livewire.on('closeModal', () => {
            const modal = document.getElementById('modal-payment');
            const modalInstance = coreui.Modal.getInstance(modal);
            if (modalInstance) {
                modalInstance.hide();
            }
        });
    </script>
    @endpush
</div>
