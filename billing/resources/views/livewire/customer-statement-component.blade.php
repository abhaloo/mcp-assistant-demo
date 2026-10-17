<div>
    {{-- Loading Overlay --}}\r
    <x-loading-overlay target="filter" message="Generating customer statement..." />
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pb-2 border-bottom">
        <h1 class="h4"></h1>
        <div class="btn-toolbar mb-md-0">
            <div class="form-inline">
                <div class="row">
                    <div class="col">
                        <label for="startDate">Start</label>
                        <input type="date" wire:model.live="from_date" class="form-control form-control-sm">
                    </div>
                    <div class="col">
                        <label for="startDate">End</label>
                        <input type="date" wire:model.live="to_date" class="form-control form-control-sm">
                    </div>

                    <div class="col mt-1">
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

    <div id="printer">
        <label><strong>Customer Statement:</strong> {{ $customer->name }} ({{ $customer->id }})</label>
        @if($statement)
            {{--        <div class="card card-body mt-2">--}}
            <p>Showing transactions from <strong>{{ $from_date }}</strong> to <strong>{{ $to_date }}</strong></p>
            <div class="d-none d-md-block">
            <table class="table table-bordered table-sm table-hover" style="font-size: 11pt">
                <tr>
                    <th colspan="8">
                        <div align="right">Opening Balance</div>
                    </th>
                    <th class="text-end">{{ number_format($statement['opening_balance'], 2) }}</th>
                </tr>
                <tr class="bg-light">
                    <th>Reference</th>
                    <th>Date</th>
                    <th>Narration</th>
                    <th>Ref./Bill</th>
                    <th>Receipt</th>
                    <th>Voucher</th>
                    <th>Debits</th>
                    <th>Credits</th>
                    <th>Balances</th>
                </tr>
                @php
                    $merged_transactions = [];
                @endphp

                @forelse($statement['transactions'] as $transaction)
                    <tr>
                        <td>{{ $transaction['document_no'] }}</td>
                        <td>{{ date_format(date_create($transaction['post_date']),'Y-m-d')}}</td>
                        <td>{!!  $transaction['description'] !!}</td>
                        <td>{{ $transaction['bill_no'] }}</td>
                        <td>{{ $transaction['receipt_number'] }}</td>
                        <td>{{ $transaction['voucher_number'] }}</td>
                        <td align="right">{{ number_format($transaction['debit'], 2) }}</td>
                        <td align="right">{{ number_format($transaction['credit'], 2) }}</td>
                        <td align="right">{{ number_format($transaction['balance'], 2) }}</td>
                    </tr>
                @empty
                @endforelse

                <tr>
                    <th colspan="8">
                        <div align="right">Closing Balance</div>
                    </th>
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
                        <div class="card-body text-center py-4 text-muted">No transactions found.</div>
                    </div>
                @endforelse

                <div class="d-flex justify-content-between p-2 bg-light rounded mb-2">
                    <span class="fw-bold">Closing Balance</span>
                    <span class="fw-bold">{{ number_format($statement['closing_balance'], 2) }}</span>
                </div>
            </div>
            {{--        </div>--}}
        @endif
    </div>
</div>

