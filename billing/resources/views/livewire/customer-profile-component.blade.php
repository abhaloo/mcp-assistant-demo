<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pb-4">
        <div class="d-flex justify-content-start">
            <div class="ml-4">
                <h2 class="text-success">{{ $customer->name }}</h2>
                <h5>Type : {{ $customer->type }}</h5>
                <p>Registered on : {{ $customer->created_at->diffForHumans() }} </p>
            </div>
        </div>

        @canany(['view invoice','view quotation'])
            <div style="float: right">
                <h4 class="text-success fw-bold">Balance: {{ number_format($customer->payable_bills->sum('balance') - $customer->customerDirectPayment->sum('debit'), 2) }}
                    {{ $customer->currency->symbol }}</h4>
            </div>
        @endcanany


        <div style="float: right">
            <x-back-link :fallback="route('customer.index')" class="btn btn-primary">
                <i class="fa fa-reply"></i> Back
            </x-back-link>
        </div>
    </div>

    <div class="card card-body border-0 shadow table-wrapper table-responsive">
        <div class="nav nav-tabs mb-4" id="nav-tab" role="tablist">
            <a class="nav-item nav-link active" id="nav-profile-tab" data-coreui-toggle="tab" href="#profile" role="tab"
               aria-controls="nav-profile" aria-selected="true" wire:ignore>
                <i class="fa fa-user"></i> Profile</a>
            @canany(['view invoice','view quotation'])
                <a class="nav-item nav-link" id="nav-bills-tab" data-coreui-toggle="tab" href="#bills" role="tab"
                   aria-controls="nav-bills" aria-selected="false" wire:ignore>
                    <i class="fa fa-coins"></i> Invoices</a>
            @endcanany

            @can('customer statement')
                <a class="nav-item nav-link" id="nav-payments-tab" data-coreui-toggle="tab" href="#statement" role="tab"
                   aria-controls="nav-payments" aria-selected="false" wire:ignore>
                    <i class="fa fa-money-bill"></i> Statement</a>
            @endcan

            @can('view job')
                <a class="nav-item nav-link" id="nav-jobs-tab" data-coreui-toggle="tab" href="#jobs" role="tab"
                   aria-controls="nav-payments" aria-selected="false" wire:ignore>
                    <i class="fa fa-clock"></i> Jobs</a>
            @endcan
        </div>

        <div class="tab-content pt-2" id="myTabContent">
            <div class="tab-pane fade show active" id="profile" role="tabpanel" aria-labelledby="profile-tab"
                 wire:ignore.self>
                <div class="d-none d-md-block">
                    <table class="table table-sm">
                        <tr>
                            <th>Reference</th>
                            <th>Email</th>
                            <th>Phone</th>
                            <th>Address</th>
                        </tr>

                        <tr>
                            <td> {{$customer->reference}}</td>
                            <td> {{$customer->email}}</td>
                            <td> {{$customer->phone}}</td>
                            <td> {{ $customer->address }}</td>
                        </tr>
                    </table>
                </div>
                <div class="d-md-none">
                    <div class="mb-2"><span class="text-muted small">Reference</span><div class="fw-bold">{{ $customer->reference }}</div></div>
                    <div class="mb-2"><span class="text-muted small">Email</span><div class="fw-bold">{{ $customer->email }}</div></div>
                    <div class="mb-2"><span class="text-muted small">Phone</span><div class="fw-bold">{{ $customer->phone }}</div></div>
                    <div class="mb-2"><span class="text-muted small">Address</span><div class="fw-bold">{{ $customer->address }}</div></div>
                </div>
            </div>
            <div class="tab-pane fade" id="bills" role="tabpanel" aria-labelledby="bill-tab" wire:ignore.self>
                <div class="d-none d-md-block">
                    <table class="table table-hover table-sm">
                        <thead>
                        <tr>
                            <th class="border-gray-200">#</th>
                            <th class="border-gray-200">Bill Id</th>
                            <th class="border-gray-200">Bill Type</th>
                            <th class="border-gray-200">Bill For</th>
                            <th class="border-gray-200">Issue Date</th>
                            <th class="border-gray-200">Reference</th>
                            <th class="border-gray-200 text-end">Total</th>
                            <th class="border-gray-200 text-end">Balance</th>
                            <th class="border-gray-200">Status</th>
                            <th class="border-gray-200 text-center">Action</th>
                        </tr>
                        </thead>
                        <tbody>
                        @forelse($bills as $bill)
                            <tr @if($bill->transactions->isEmpty()) class="bg-danger text-light" @endif>
                                <td>{{ $loop->index + 1 }}</td>
                                <td>{{ $bill->invoice_number ?? $bill->id }}</td>
                                <td>{{ $bill->type }}</td>
                                <td>{{ $bill->customer->name }}</td>
                                <td>{{ $bill->created_at->format('d-m-Y') }}</td>
                                <td>{{ $bill->controlNumber->control_number }}</td>
                                <td class="text-end">{{ number_format($bill->amount(), 2) }}</td>
                                <td class="text-end">{{ number_format($bill->balance(), 2) }}</td>
                                <td>{!! $bill->balanceStatus() !!}</td>
                                <td class="text-end">
                                    @can('add invoice')
                                        @if($bill->type != 'Invoice' && $bill->type != 'Payable Quotation' && Route::has('bill.convert'))
                                            <a href="#"
                                                data-coreui-toggle="modal" data-coreui-target="#modal-convert-bill-{{ $bill->id }}"
                                                class="btn btn-secondary btn-sm">
                                                <i class="fa fa-share"></i>
                                                Convert Tax Invoice</a>
                                            <x-modal.confirm id="modal-convert-bill-{{ $bill->id }}"
                                                             title="Convert to Tax Invoice"
                                                             action="{{ route('bill.convert', $bill) }}"
                                                             confirmText="Convert">
                                                Are you sure you want to convert bill <strong>{{ $bill->invoice_number ?? $bill->id }}</strong> to a Tax Invoice?
                                            </x-modal.confirm>
                                        @endif
                                    @endcan
                                    <a href="{{ route('bill.show', $bill) }}" class="btn btn-primary btn-sm">
                                        <i class="fa fa-eye"></i>
                                        Show</a>

                                    @if(Route::has('bill.create'))
                                        @if($bill->type == 'Invoice')
                                            @can('edit invoice')
                                                <a href="{{ route('bill.create', $bill) }}" class="btn btn-secondary btn-sm">
                                                    <i class="fa fa-edit"></i>
                                                    Edit</a>
                                            @endcan
                                        @else
                                            @can('edit quotation')
                                                <a href="{{ route('bill.create', $bill) }}" class="btn btn-secondary btn-sm">
                                                    <i class="fa fa-edit"></i>
                                                    Edit</a>
                                            @endcan
                                        @endif
                                    @endif

                                </td>
                            </tr>
                        @empty
                        @endforelse
                        </tbody>
                    </table>
                </div>

                <div class="d-md-none">
                    @forelse($bills as $bill)
                        <div class="card border-0 shadow-sm mb-2">
                            <div class="card-body p-3">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <span class="badge bg-info">{{ $bill->type }}</span>
                                    <span>{!! $bill->balanceStatus() !!}</span>
                                </div>
                                <div class="fw-bold">
                                    {{ $bill->customer->name }}
                                    @if($bill->transactions->isEmpty())
                                        <span class="badge bg-danger ms-1">No transactions</span>
                                    @endif
                                </div>
                                <div class="small text-muted mt-1">
                                    <i class="fa fa-hashtag"></i> {{ $bill->invoice_number ?? $bill->id }}
                                    <i class="fa fa-calendar ms-2"></i> {{ $bill->created_at->format('d-m-Y') }}
                                    <i class="fa fa-receipt ms-2"></i> {{ $bill->controlNumber->control_number }}
                                </div>
                                <div class="d-flex justify-content-between align-items-center border-top mt-2 pt-2">
                                    <span class="small text-muted">Total: {{ number_format($bill->amount(), 2) }}</span>
                                    <span class="fw-bold">Balance: {{ number_format($bill->balance(), 2) }}</span>
                                </div>
                                <div class="d-flex flex-wrap gap-1 border-top mt-2 pt-2">
                                    @can('add invoice')
                                        @if($bill->type != 'Invoice' && $bill->type != 'Payable Quotation')
                                            <a href="#"
                                               data-coreui-toggle="modal" data-coreui-target="#modal-convert-bill-{{ $bill->id }}"
                                               class="btn btn-secondary btn-sm flex-fill">
                                                <i class="fa fa-share"></i> Convert</a>
                                        @endif
                                    @endcan
                                    <a href="{{ route('bill.show', $bill) }}" class="btn btn-primary btn-sm flex-fill">
                                        <i class="fa fa-eye"></i> Show</a>
                                    @if(Route::has('bill.create'))
                                        @if($bill->type == 'Invoice')
                                            @can('edit invoice')
                                                <a href="{{ route('bill.create', $bill) }}" class="btn btn-secondary btn-sm flex-fill">
                                                    <i class="fa fa-edit"></i> Edit</a>
                                            @endcan
                                        @else
                                            @can('edit quotation')
                                                <a href="{{ route('bill.create', $bill) }}" class="btn btn-secondary btn-sm flex-fill">
                                                    <i class="fa fa-edit"></i> Edit</a>
                                            @endcan
                                        @endif
                                    @endif
                                </div>
                            </div>
                        </div>
                    @empty
                        <div class="card border-0 shadow-sm mb-2">
                            <div class="card-body p-3 text-center py-4 text-muted">
                                No invoices found.
                            </div>
                        </div>
                    @endforelse
                </div>

                <div class="">
                    {{ $bills->links() }}
                </div>
            </div>

            <div class="tab-pane fade" id="statement" role="tabpanel" aria-labelledby="bill-tab" wire:ignore.self>
                @livewire('customer-statement-component',['customer' => $customer->id])
            </div>

            <div class="tab-pane fade" id="jobs" role="tabpanel" aria-labelledby="bill-tab" wire:ignore.self
                data-mcp-page-context-owner="jobs"
                data-mcp-context-route="customer.show"
                data-mcp-context-kind="embedded_list"
                data-mcp-context-resource="work_order"
                data-mcp-context-id="jobs:customer:{{ $customer->id }}"
                data-mcp-context-scope-id="{{ $customer->id }}"
            >
                @livewire('work-order-component', ['customer' => $customer, 'contextVariant' => 'customer'])
            </div>
        </div>

    </div>
</div>
