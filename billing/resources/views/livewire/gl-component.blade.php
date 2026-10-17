<div>
    @include('livewire.partials.select2-assets')
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-md-0">
            <h2 class="h4">General <strong>Ledger</strong></h2>
        </div>

        @if($available_balance > 0)
            <div class="float-end text-success fw-bold">
                Available Balance: {{ number_format($available_balance, 2) }}
            </div>
        @endif

    </div>
    <div class="card card-body">
        <div class="row m-0">
            <div class="col border-bottom pb-2">
                @if ($errors->any())
                    <div class="alert alert-danger">
                        <ul>
                            @foreach ($errors->all() as $error)
                                <li>{{ $error }}</li>
                            @endforeach
                        </ul>
                    </div>
                @endif

                <div class="d-flex justify-content-between">
                    <div class="row m-0 p-0 g-2">
                        <div class="col-12 col-md">
                            <label class="text-success fw-bold">Transaction</label>
                            <select class="form-control" wire:model.live="transaction_type">
                                <option>General Entry</option>
                                <option>Cash Transfer</option>
                                <option>Customer Payment</option>
                                <option>Customer Deposit</option>
                                <option>Supplier Bill</option>
                                <option>Expenditure</option>
                                <option>Other Income</option>
                            </select>
                        </div>
                        <div class="col-12 col-md">
                            <label class="fw-bold">Date</label>
                            <input type="date" class="form-control" wire:model.live="post_date">
                        </div>
                        <div class="col-12 col-md">
                            @if($flip)
                                <label class="text-primary fw-bold">{{ $flip ? 'Credit' : 'Debit' }} Account</label>
                                <select class="form-control" wire:model.live="newAccount">
                                    <option>--Select--</option>
                                    @forelse($otherAccounts as $bank)
                                        <option value="{{ $bank->id }}"> {{ $bank->name }}</option>
                                    @empty
                                    @endforelse
                                </select>
                            @else
                                <label class="text-primary fw-bold">{{ $flip ? 'Credit' : 'Debit' }} Account</label>
                                <select class="form-control" wire:model.live="activeAccount">
                                    <option>--Select--</option>
                                    <optgroup label="PAID VIA">Other Deposit</optgroup>
                                    @forelse($bankAccounts as $bank)
                                        <option value="{{ $bank->id }}"> {{ $bank->name }}</option>
                                    @empty
                                    @endforelse
                                    @if($transaction_type == 'Customer Payment')
                                        <optgroup label="FROM DEPOSIT">Customer Deposit</optgroup>
                                        <option value="{{$customer_deposit}}">Customer Deposit</option>
                                    @endif
                                </select>
                            @endif
                        </div>

                        @if($transaction_type == 'Customer Payment' || $transaction_type == 'Customer Deposit')
                            <div class="col-12 col-md">
                                <label class="text-primary fw-bold">Customer</label>
                                <select class="form-control select2" wire:model.live="customer" id="customer">
                                    <option value="">--Select--</option>
                                    @forelse($customers as $customer)
                                        <option value="{{ $customer->id }}"> {{ $customer->name }}</option>
                                    @empty
                                    @endforelse
                                </select>
                            </div>

                            @if(!is_null($selected_customer) && $transaction_type == 'Customer Payment')
                                <div class="col-12 col-md">
                                    <label class="text-primary fw-bold">
                                        <a href="#" wire:click="$set('is_multi', true)"
                                           class="text-decoration-none hover:text-success"> Select Bills <i class="fa fa-list-alt"></i> </a></label>
                                    <select class="form-control" wire:model.live="invoice"
                                            @if($is_multi) multiple @endif>
                                        <option value="">--Select--</option>
                                        @forelse($selected_customer?->bills as $bill)
                                            @if($bill->balance() > 0 && ($bill->type == 'Invoice' || $bill->type == 'Payable Quotation'))
                                                <option class="fw-bold @if($bill->type == 'Invoice') text-success @else text-primary @endif"
                                                        value="{{ $bill->id }}"> {{ $bill->invoice_number ?? $bill->id }}
                                                    | {{ number_format($bill->balance(), 2) }}</option>
                                            @endif
                                        @empty
                                        @endforelse
                                    </select>
                                </div>
                            @endif
                            <div class="col-12 col-md">
                                <label class="text-primary fw-bold">Receipt No.</label>
                                <input class="form-control" wire:model.live="reference" placeholder="Receipt No.">
                            </div>
                        @endif

                        @if($transaction_type == 'Supplier Bill')
                            <div class="col-12 col-md">
                                <label class="text-primary fw-bold">Supplier</label>
                                <select class="form-control select2" wire:model.live="supplier" id="supplier">
                                    <option value="">--Select--</option>
                                    @forelse($suppliers as $supplier)
                                        <option value="{{ $supplier->id }}"> {{ $supplier->name }}</option>
                                    @empty
                                    @endforelse
                                </select>
                            </div>

                            @if(!is_null($selected_supplier))
                                <div class="col-12 col-md">
                                    <label class="text-primary fw-bold">Bill</label>
                                    <select class="form-control" wire:model.live="supplier_bill">
                                        <option value="">--Select--</option>
                                        @forelse($selected_supplier?->inventories as $bill)
                                            @if($bill->balance() > 0 && $bill->status == 'APPROVED' && $bill->type == 'Receive')
                                                <option class="fw-bold text-success"
                                                        value="{{ $bill->id }}"> {{ $bill->invoice_number ?? $bill->id }}
                                                    | {{ number_format($bill->balance(), 2) }}</option>
                                            @endif
                                        @empty
                                        @endforelse
                                    </select>
                                </div>

                                <div class="col-12 col-md">
                                    <label class="text-primary fw-bold">Supplier Invoice</label>
                                    <input class="form-control" wire:model.live="reference" placeholder="Supplier Invoice">
                                </div>
                            @endif
                        @endif

                        @if($transaction_type == 'Expenditure')
                            <div class="col-12 col-md">
                                <label class="text-primary fw-bold">Voucher No.</label>
                                <input class="form-control" wire:model.live="voucher_number" placeholder="Voucher No.">
                            </div>
                         @endif
                        <div class="col-12 col-md">
                            <label class="text-primary fw-bold">Voucher No</label>
                            <input class="form-control" wire:model.live="voucher_number" placeholder="Voucher">
                        </div>
                    </div>
                </div>

            </div>
        </div>

        <div class="row ">
            <div class="col">
                <div class="d-none d-md-block">
                    <table class="table table-sm table-hover table-bordered">
                        <tr style="background: #F5F5F5">
                            <th class="text-primary fw-bold">{{ $flip ? 'Debit' : 'Credit' }} Account</th>
                            <th>Narration</th>
                            <th>Amount</th>
                            <th></th>
                        </tr>
                        <tr>
                            <td>
                                @if(!$flip)
                                    <select class="form-control" wire:model.live="newAccount">
                                        <option value="">--Select Account--</option>
                                        @forelse($otherAccounts as $account)
                                            <option value="{{ $account->id }}"> {{ $account->name }}</option>
                                        @empty
                                        @endforelse
                                    </select>
                                @else
                                    <select class="form-control" wire:model.live="activeAccount">
                                        <option value="">--Select Account--</option>
                                        @forelse($bankAccounts as $account)
                                            <option value="{{ $account->id }}"> {{ $account->name }}</option>
                                        @empty
                                        @endforelse
                                    </select>
                                @endif
                            </td>

                            <td>
                                <input type="text" class="form-control" placeholder="Transaction narration.."
                                       wire:model.live="newNarration">
                            </td>

                            <td>
                                <input type="number" step="0.01" class="form-control" placeholder="Amount"
                                       wire:model.live="newAmount">
                            </td>


                            <td>
                                <button class="btn btn-primary btn-sm" wire:click="addItem">Add to Batch</button>
                            </td>
                        </tr>

                        @forelse($items as $item)
                            <tr class="bg-light">
                                <td>{{ \App\Models\Account::find($item['account'])->name }}</td>
                                <td>{{ number_format($item['amount'], 2) }}</td>
                                <td>{{ $item['narration'] }}</td>
                                <td></td>
                            </tr>
                        @empty
                        @endforelse
                    </table>
                </div>

                <div class="d-md-none mb-3">
                    <div class="mb-2">
                        <label class="small text-primary fw-bold">{{ $flip ? 'Debit' : 'Credit' }} Account</label>
                        @if(!$flip)
                            <select class="form-control" wire:model.live="newAccount">
                                <option value="">--Select Account--</option>
                                @forelse($otherAccounts as $account)
                                    <option value="{{ $account->id }}"> {{ $account->name }}</option>
                                @empty
                                @endforelse
                            </select>
                        @else
                            <select class="form-control" wire:model.live="activeAccount">
                                <option value="">--Select Account--</option>
                                @forelse($bankAccounts as $account)
                                    <option value="{{ $account->id }}"> {{ $account->name }}</option>
                                @empty
                                @endforelse
                            </select>
                        @endif
                    </div>
                    <div class="mb-2">
                        <label class="small fw-bold">Narration</label>
                        <input type="text" class="form-control" placeholder="Transaction narration.." wire:model.live="newNarration">
                    </div>
                    <div class="mb-2">
                        <label class="small fw-bold">Amount</label>
                        <input type="number" step="0.01" class="form-control" placeholder="Amount" wire:model.live="newAmount">
                    </div>
                    <button class="btn btn-primary btn-sm w-100" wire:click="addItem">Add to Batch</button>
                </div>

                <div class="d-md-none">
                    @forelse($items as $item)
                        <div class="card border-0 shadow-sm mb-2">
                            <div class="card-body p-3">
                                <div class="d-flex justify-content-between mb-1">
                                    <span class="fw-bold">{{ \App\Models\Account::find($item['account'])->name }}</span>
                                    <span class="text-success fw-bold">{{ number_format($item['amount'], 2) }}</span>
                                </div>
                                <div class="small text-muted">{{ $item['narration'] }}</div>
                            </div>
                        </div>
                    @empty
                    @endforelse
                </div>

                @if(!empty($items))
                    <button class="btn btn-success btn-sm" wire:click="processBatch">Process Batch</button>
                @endif
            </div>
        </div>
    </div>
</div>

@push('scripts')
    <script>
        // Initialize Select2
        document.addEventListener("initSelect2", function () {
            $('.select2').select2({
                theme: 'bootstrap-5'
            });

            // Livewire event listener to customer
            $('#customer').on('change', function (e) {
                const data = $(this).val();
                @this.set('customer', data);
            });

            // Livewire event listener to supplier
            $('#supplier').on('change', function (e) {
                const data = $(this).val();
                @this.set('supplier', data);
            });

        })

    </script>
@endpush
