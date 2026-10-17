<div>
    {{-- Loading Overlay --}}
    <x-loading-overlay target="filter" message="Generating statement..." />

    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
        <h1 class="h2">Account Statement</h1>
        <div class="btn-toolbar mb-2 mb-md-0">
            <div class="form-inline">
                <div class="row">
                    <div class="col">
                        <label for="account">Account <span class="text-danger">*</span></label>
                        <select wire:model.live="activeAccount" class="form-control form-control-sm @error('activeAccount') is-invalid @enderror">
                            <option value="">--Select--</option>
                            @forelse($accounts as $account)
                                <option value="{{$account->id}}">{{$account->name}}</option>
                            @empty
                            @endforelse
                        </select>
                        @error('activeAccount')
                            <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>
                    <div class="col">
                        <label for="startDate">Start <span class="text-danger">*</span></label>
                        <input type="date" wire:model.live="from_date" class="form-control form-control-sm @error('from_date') is-invalid @enderror">
                        @error('from_date')
                            <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>
                    <div class="col">
                        <label for="startDate">End <span class="text-danger">*</span></label>
                        <input type="date" wire:model.live="to_date" class="form-control form-control-sm @error('to_date') is-invalid @enderror">
                        @error('to_date')
                            <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>

                    <div class="col mt-1 d-flex align-items-end gap-1">
                        {{-- Advanced Filters Button --}}
                        <button type="button" 
                                class="btn btn-outline-secondary btn-sm mt-3 position-relative"
                                data-coreui-toggle="modal" 
                                data-coreui-target="#advancedFiltersModal"
                                title="Advanced Filters">
                            <i class="fas fa-sliders-h"></i>
                            @if(count($selectedCustomers) > 0)
                                <x-badge color="primary" class="position-absolute top-0 start-100 translate-middle rounded-pill" style="font-size: 0.65rem;">
                                    {{ count($selectedCustomers) }}
                                </x-badge>
                            @endif
                        </button>
                        
                        <x-loading-button 
                            wire:click="filter" 
                            target="filter" 
                            label="Run" 
                            icon="fa-play"
                            loading-label="Running..."
                            class="btn btn-success btn-sm mt-3"
                        />
                        <input type="button" value="Print" onclick="PrintElem('#printer')"
                               class="btn btn-primary btn-sm mt-3">
                    </div>

                </div>

            </div>
        </div>
    </div>

    {{-- Advanced Filters Modal --}}
    <div wire:ignore.self class="modal fade" id="advancedFiltersModal" tabindex="-1" aria-labelledby="advancedFiltersModalLabel" aria-hidden="true">
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="advancedFiltersModalLabel">
                        <i class="fas fa-sliders-h text-primary me-2"></i>Advanced Filters
                    </h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    {{-- Customer Filter --}}
                    <div>
                        <label class="form-label fw-bold mb-2">
                            <i class="fas fa-users text-primary me-1"></i>Customers
                            @if(count($selectedCustomers) > 0)
                                <x-badge color="primary" class="ms-1">{{ count($selectedCustomers) }}</x-badge>
                            @endif
                        </label>
                        
                        {{-- Customer Search (always visible) --}}
                        @livewire('customer-search-component', [], key('statement-customer-search'))
                        
                        {{-- Selected Customers List --}}
                        @if(count($selectedCustomers) > 0)
                            <div class="mt-3">
                                <div class="d-flex flex-wrap gap-2">
                                    @foreach($selectedCustomers as $customer)
                                        <div class="d-inline-flex align-items-center bg-light border rounded px-2 py-1">
                                            <i class="fas fa-user text-primary me-2 small"></i>
                                            <span class="me-2">{{ $customer['name'] }}</span>
                                            <button type="button" 
                                                    wire:click="removeCustomer({{ $customer['id'] }})"
                                                    class="btn btn-link btn-sm text-danger p-0"
                                                    title="Remove">
                                                <i class="fas fa-times"></i>
                                            </button>
                                        </div>
                                    @endforeach
                                </div>
                                <button type="button" 
                                        wire:click="clearCustomerFilter" 
                                        class="btn btn-link btn-sm text-danger p-0 mt-2">
                                    <i class="fas fa-times-circle me-1"></i>Clear all
                                </button>
                            </div>
                        @endif
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-primary" data-coreui-dismiss="modal">
                        <i class="fas fa-check me-1"></i>Done
                    </button>
                </div>
            </div>
        </div>
    </div>

    @if($statement)
        <div class="card card-body" id="printer" style="font-size: 11pt">
            <h5 class="text-uppercase"><strong>Account</strong>:{{  \App\Models\Account::find($activeAccount)?->name }}</h5>
            <p>
                Showing transactions from <strong>{{ $from_date }}</strong> to <strong>{{ $to_date }}</strong>
                @if(count($selectedCustomers) > 0)
                    <x-badge color="primary" class="ms-2 no-print">
                        <i class="fas fa-users me-1"></i>{{ count($selectedCustomers) }} customer(s)
                    </x-badge>
                @endif
            </p>
            
            {{-- Active Filters Summary (for print) --}}
            @if(count($selectedCustomers) > 0)
                <p class="d-none d-print-block">
                    <strong>Customer Filter:</strong> 
                    {{ implode(', ', array_column($selectedCustomers, 'name')) }}
                </p>
            @endif
            
            <div class="d-none d-md-block">
            <table id="statement-table" class="table table-bordered table-sm table-hover">
                <tr>
                    <th colspan="8" ><div align="right">Opening Balance</div></th>
                    <th class="text-end">{{ number_format($statement['opening_balance'], 2) }}</th>
                </tr>
                <tr class="bg-light">
                    <th>Reference</th>
                    <th>Transaction Date</th>
                    <th>Narration</th>
                    <th>Ref./Bill</th>
                    <th>Receipt</th>
                    <th>Voucher</th>
                    <th>Debits</th>
                    <th>Credits</th>
                    <th>Balances</th>
                </tr>
                <tbody>
                @forelse($statement['transactions'] as $transaction)
                    <tr>
                        <td>{{ $transaction['document_no'] }}</td>
                        <td>{{ date_format(date_create($transaction['post_date']),'Y-m-d')}}</td>
                        <td>{!! $transaction['description'] !!} </td>
                        <td>{{ $transaction['bill_no'] }}</td>
                        <td>{{ $transaction['receipt_number'] }}</td>
                        <td>{{ $transaction['voucher_number'] }}</td>
                        <td align="right">{{ number_format($transaction['debit'], 2) }}</td>
                        <td align="right">{{ number_format($transaction['credit'], 2) }}</td>
                        <td align="right">{{ number_format($transaction['balance'], 2) }}</td>
                    </tr>
                @empty
                    <tr>
                        <td colspan="9" class="text-center text-muted py-3">
                            No transactions found for the selected criteria.
                        </td>
                    </tr>
                @endforelse
                </tbody>
                <tr>
                    <th colspan="8" ><div align="right">Closing Balance</div></th>
                    <th class="text-end">{{ number_format($statement['closing_balance'], 2) }}</th>
                </tr>
            </table>
            </div>

            {{-- Mobile Card View --}}
            <div class="d-md-none">
                <div class="d-flex justify-content-between p-2 bg-light rounded mb-2">
                    <span class="fw-bold">Opening Balance</span>
                    <span class="fw-bold">{{ number_format($statement['opening_balance'], 2) }}</span>
                </div>

                @forelse($statement['transactions'] as $transaction)
                    <div class="card border-0 shadow-sm mb-2">
                        <div class="card-body p-3">
                            <div class="d-flex justify-content-between mb-1">
                                <small class="text-muted">{{ $transaction['document_no'] }}</small>
                                <span class="fw-bold">{{ number_format($transaction['balance'], 2) }}</span>
                            </div>
                            <div class="mb-1">{{ \Illuminate\Support\Str::limit(strip_tags($transaction['description']), 60) }}</div>
                            <div class="d-flex flex-wrap gap-3 text-muted" style="font-size: 0.8rem">
                                <span><i class="fas fa-calendar me-1"></i>{{ date_format(date_create($transaction['post_date']),'Y-m-d') }}</span>
                                @if(!empty($transaction['bill_no']))
                                    <span><i class="fas fa-hashtag me-1"></i>{{ $transaction['bill_no'] }}</span>
                                @endif
                                @if(!empty($transaction['receipt_number']))
                                    <span><i class="fas fa-receipt me-1"></i>{{ $transaction['receipt_number'] }}</span>
                                @endif
                                @if(!empty($transaction['voucher_number']))
                                    <span><i class="fas fa-file-alt me-1"></i>{{ $transaction['voucher_number'] }}</span>
                                @endif
                            </div>
                            <div class="d-flex justify-content-between py-2 border-top mt-2" style="font-size: 0.85rem">
                                <span>Debit: @if($transaction['debit'] > 0)<span class="text-danger fw-bold">{{ number_format($transaction['debit'], 2) }}</span>@else 0.00 @endif</span>
                                <span>Credit: @if($transaction['credit'] > 0)<span class="text-success fw-bold">{{ number_format($transaction['credit'], 2) }}</span>@else 0.00 @endif</span>
                            </div>
                        </div>
                    </div>
                @empty
                    <div class="card border-0 shadow-sm mb-2">
                        <div class="card-body text-center py-4 text-muted">No transactions found for the selected criteria.</div>
                    </div>
                @endforelse

                <div class="d-flex justify-content-between p-2 bg-light rounded mb-2">
                    <span class="fw-bold">Closing Balance</span>
                    <span class="fw-bold">{{ number_format($statement['closing_balance'], 2) }}</span>
                </div>
            </div>
        </div>
    @endif
</div>
