<div>
    <div class="mcp">
        <x-mcp.page-header :title="$bill->type.' #'.$bill->id">
            <x-slot:breadcrumb>
                <x-mcp.breadcrumb :items="[
                    ['label' => 'Bills', 'href' => route('bill.index')],
                    ['label' => '#'.$bill->id],
                ]" />
            </x-slot:breadcrumb>
            <x-slot:titleAdornment>
                {!! $bill->paymentBadge() !!}
                @if($bill->balance() > 0)
                    <span class="ms-2 text-muted">Balance {{ number_format($bill->balance(), 2) }}</span>
                @endif
            </x-slot:titleAdornment>
            <x-slot:actions>
                {{-- Task P7 — "Ask about this bill": identifiers only, in escaped data
                     attributes. Delegated click handling lives in public/js/mcp-ask-ai.js
                     (document-level listener); no inline JavaScript here. Rendered only
                     when both RAG feature flags are on AND the registry recognizes this
                     Bill's subtype (BusinessRecordRegistry::resourceTypeForModel()). --}}
                @if($ragEnabled && $recordContextEnabled && $recordResourceType)
                    @php
                        $askBillLabel = ($bill->invoice_number ?: '#'.$bill->id).' ('.$bill->type.')';
                    @endphp
                    <x-mcp.btn variant="secondary" size="sm" icon="fa-wand-magic-sparkles"
                        data-mcp-ask-ai-record-trigger
                        data-record-resource-type="{{ $recordResourceType }}"
                        data-record-id="{{ $bill->id }}"
                        data-record-label="{{ $askBillLabel }}"
                        title="Ask about this bill's reference, customer, or notes.">
                        Ask about this bill
                    </x-mcp.btn>
                @endif

                @if($bill->balance() > 0 && $bill->type == 'Invoice')
                    @can('add journal entry')
                        <x-mcp.btn variant="secondary" size="sm" icon="fa-coins"
                            data-coreui-toggle="modal" data-coreui-target="#modal-payment">
                            New Payment
                        </x-mcp.btn>
                    @endcan
                @endif

                @if(Route::has('bill.create'))
                    @if($bill->type == 'Invoice')
                        @can('edit invoice')
                            <x-mcp.btn variant="secondary" size="sm" icon="fa-edit"
                                :href="route('bill.create', ['bill' => $bill, 'type' => 'Invoice'])">
                                Edit
                            </x-mcp.btn>
                        @endcan
                    @else
                        @can('edit quotation')
                            <x-mcp.btn variant="secondary" size="sm" icon="fa-edit"
                                :href="route('bill.create', ['bill' => $bill, 'type' => $bill->type])">
                                Edit
                            </x-mcp.btn>
                        @endcan
                    @endif
                @endif

                @if(Route::has('bill.print'))
                    <x-mcp.btn variant="success" size="sm" icon="fa-print"
                        :href="route('bill.print', $bill)" target="_blank">
                        Print Document
                    </x-mcp.btn>
                @endif

                @if($fromOrder && Route::has('customer-orders.show'))
                    <x-mcp.btn variant="secondary" size="sm" icon="fa-chevron-left"
                        :href="route('customer-orders.show', $fromOrder)">
                        Back to Order
                    </x-mcp.btn>
                @else
                    <x-back-link :fallback="route('bill.index', ['type' => $bill->type])" class="mcp-btn mcp-btn--secondary mcp-btn--sm">
                        <i class="fa fa-chevron-left"></i>
                        Back
                    </x-back-link>
                @endif
            </x-slot:actions>
        </x-mcp.page-header>

        <x-mcp.card title="Bill Details" class="mb-3">
            <x-mcp.deflist>
                <x-mcp.deflist.row label="Bill Id">
                    {{ $bill->id }}
                </x-mcp.deflist.row>
                <x-mcp.deflist.row label="Customer">
                    {{ $bill->customer->name }}
                </x-mcp.deflist.row>
                <x-mcp.deflist.row label="Reference">
                    {{ $bill->controlNumber->control_number }}
                </x-mcp.deflist.row>
                <x-mcp.deflist.row label="Created At">
                    {{ $bill->created_at->format('d-m-Y H:i:s') }}
                </x-mcp.deflist.row>
                <x-mcp.deflist.row label="Due At">
                    {{ $bill->controlNumber->expire_date->format('d-m-Y H:i:s') }}
                </x-mcp.deflist.row>
                <x-mcp.deflist.row label="Currency">
                    {{ $bill->currency->currency_code }}
                </x-mcp.deflist.row>
                <x-mcp.deflist.row label="Status">
                    {!! $bill->paymentBadge() !!}
                </x-mcp.deflist.row>
            </x-mcp.deflist>

            @if($bill->description)
                <p class="mb-0 mt-3">
                    <strong>Notes: </strong>{{ $bill->description }}
                </p>
            @endif
        </x-mcp.card>

        <x-mcp.card title="Bill Items" flush class="mb-3">
            <div class="mcp-table-wrap" wire:loading.class="mcp-skeleton-refreshing" wire:loading.attr="aria-busy">
                <table class="mcp-table">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Description</th>
                            <th>Price</th>
                            <th>Quantity</th>
                            <th>Disc.</th>
                            <th>Tax</th>
                            <th class="mcp-td--right">Subtotal</th>
                        </tr>
                    </thead>
                    <tbody>
                        @forelse($bill->billItems as $item)
                            @php $effectiveQty = $item->quantity - $item->quantity_returned; @endphp
                            <tr>
                                <td class="mcp-cell-strong">{{ $item->product->name ?? 'None' }}</td>
                                <td class="mcp-td--mute">{{ $item->description }}</td>
                                <td class="mcp-td--right mcp-td--mono">{{ number_format($item->price, 2) }}</td>
                                <td class="mcp-td--right mcp-td--mono">{{ $effectiveQty }}</td>
                                <td class="mcp-td--right mcp-td--mono">{{ number_format($item->discount, 2) }}</td>
                                <td class="mcp-td--right mcp-td--mono">{{ number_format((($effectiveQty * $item->price) - $item->discount) * ($item->tax / 100), 2) }}</td>
                                <td class="mcp-td--right mcp-td--mono">{{ number_format((($effectiveQty * $item->price) - $item->discount) + ((($effectiveQty * $item->price) - $item->discount) * ($item->tax / 100)), 2) }}</td>
                            </tr>
                        @empty
                            <tr>
                                <td colspan="7" class="text-center py-4">
                                    <x-mcp.empty title="No items" />
                                </td>
                            </tr>
                        @endforelse
                        @if($bill->billItems->isNotEmpty())
                            <tr class="mcp-table__totals">
                                <th colspan="6"></th>
                                <th class="fw-bold text-end">
                                    Total.: {{ number_format($bill_value - $bill->discount, 2) }}<br/>
                                    Disc.: {{ number_format($bill->discount, 2) }}<br/>
                                    Tax: {{ number_format($tax_value, 2) }}<br/>
                                    G.Total: {{ number_format($bill_value + $tax_value, 2) }}
                                </th>
                            </tr>
                        @endif
                    </tbody>
                </table>
            </div>
        </x-mcp.card>

        <h5 class="mb-3 fw-bold">Jobs Associated with this order</h5>
        <div
            data-mcp-page-context-owner="jobs"
            data-mcp-context-route="bill.show"
            data-mcp-context-kind="embedded_list"
            data-mcp-context-resource="work_order"
            data-mcp-context-id="jobs:bill:{{ $bill->id }}"
            data-mcp-context-scope-id="{{ $bill->id }}"
        >
        @livewire('work-order-component', ['bill' => $bill, 'contextVariant' => 'bill'])
        </div>

        @if($bill->type != 'Quotation')
            <x-mcp.card title="Payments" flush class="mt-4">
                <div class="mcp-table-wrap" wire:loading.class="mcp-skeleton-refreshing" wire:loading.attr="aria-busy">
                    <table class="mcp-table">
                        <thead>
                            <tr>
                                <th>Method</th>
                                <th>Receipt</th>
                                <th class="mcp-td--right">Amount</th>
                                <th>Description</th>
                                <th>Paid At</th>
                                <th>Posted At</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            @forelse($bill->payments as $payment)
                                <tr>
                                    <td class="mcp-cell-strong">{{ $payment->account->name }}</td>
                                    <td class="mcp-td--mono">{{ $payment->document_no }}</td>
                                    <td class="mcp-td--right mcp-td--mono">{{ number_format($payment->debit, 2) }}</td>
                                    <td class="mcp-td--mute">{{ $payment->description }}</td>
                                    <td class="mcp-td--mute mcp-td--mono mcp-td--secondary">{{ $payment->post_date->format('d-m-Y') }}</td>
                                    <td class="mcp-td--mute mcp-td--mono mcp-td--secondary">{{ $payment->created_at->format('d-m-Y') }}</td>
                                    <td>
                                        <a href="#" target="_blank" class="btn btn-primary btn-sm">
                                            <i class="fa fa-receipt"></i>
                                            Receipt
                                        </a>
                                    </td>
                                </tr>
                            @empty
                                <tr>
                                    <td colspan="7" class="text-center py-4">
                                        <x-mcp.empty title="No Payment" />
                                    </td>
                                </tr>
                            @endforelse
                            @if($bill->payments->isNotEmpty())
                                <tr class="mcp-table__totals">
                                    <th colspan="2"></th>
                                    <th class="fw-bold text-end">
                                        {{ number_format($bill->payments->sum('debit'), 2) }}
                                    </th>
                                    <th colspan="4"></th>
                                </tr>
                            @endif
                        </tbody>
                    </table>
                </div>
            </x-mcp.card>
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
                    <div class="row">
                        <div class="mb-4">
                            <label for="type">Method <span class="text-danger">*</span></label>
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

                        <div class="mb-4">
                            <label for="paid_date">Paid At <span class="text-danger">*</span></label>
                            <input type="date" wire:model.live="paid_date"
                                   class="form-control @error('paid_date') is-invalid @enderror" id="paid_date">
                            @error('paid_date')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="amount">Amount <span class="text-danger">*</span></label>
                            <input type="number" step="0.01" wire:model.live="amount"
                                   class="form-control @error('amount') is-invalid @enderror" id="amount">
                            @error('amount')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="description">Description</label>
                            <input type="text" wire:model.live="description"
                                   class="form-control @error('phone') is-invalid @enderror" id="description">
                            @error('description')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="modal-footer">

                            <button type="button" wire:click.prevent="store" class="btn btn-primary">Add</button>
                            <button type="button" class="btn btn-secondary text-gray-600 ms-auto"
                                    data-coreui-dismiss="modal">
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!-- End of Modal Content -->

    @script
        <script>
            $wire.on('closeModal', () => {
                $('#modal-payment').modal('hide')
            })
        </script>
    @endscript
</div>
