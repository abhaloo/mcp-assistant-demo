{{-- Payment Modal (mobile-optimized) --}}
<div class="modal fade" wire:ignore.self id="modal-payment" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h2 class="h6 modal-title">Add Payment</h2>
                <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
                {{-- Balance display --}}
                <div class="alert alert-light border mb-3 py-2">
                    <div class="d-flex justify-content-between align-items-center">
                        <span class="text-muted small">Outstanding:</span>
                        <span class="fw-bold text-danger" wire:click="fillBalance" style="cursor: pointer;">
                            USD {{ number_format($balance, 2) }}
                            <i class="fa fa-arrow-right ms-1 small"></i>
                        </span>
                    </div>
                </div>

                <div class="mb-3">
                    <label for="m_sb_amount" class="form-label">Amount <span class="text-danger">*</span></label>
                    @php
                        $effectiveAmount = $payment_currency_id != $tzsCurrencyId
                            ? (float)$amount * (float)$payment_exchange_rate
                            : (float)$amount;
                    @endphp
                    <input type="number" step="0.01" wire:model.live="amount"
                           class="form-control @error('amount') is-invalid @enderror @if($effectiveAmount > $balance) is-invalid @endif"
                           id="m_sb_amount" placeholder="0.00">
                    @error('amount') <div class="invalid-feedback">{{ $message }}</div> @enderror
                    @if($effectiveAmount > $balance)
                        <small class="text-danger">
                            <i class="fa fa-exclamation-triangle me-1"></i>Exceeds balance
                        </small>
                    @elseif($payment_currency_id != $tzsCurrencyId && $amount > 0)
                        <small class="text-muted">= USD {{ number_format($effectiveAmount, 2) }}</small>
                    @endif
                </div>

                <div class="mb-3">
                    <label for="m_sb_method" class="form-label">Method <span class="text-danger">*</span></label>
                    <select wire:model.live="method" class="form-control @error('method') is-invalid @enderror" id="m_sb_method">
                        <option value="">Choose..</option>
                        @foreach($providers as $provider)
                            <option value="{{ $provider->id }}">{{ $provider->name }}</option>
                        @endforeach
                    </select>
                    @error('method') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>

                <div class="mb-3">
                    <label for="m_sb_paid_date" class="form-label">Paid At <span class="text-danger">*</span></label>
                    <input type="date" wire:model.live="paid_date"
                           class="form-control @error('paid_date') is-invalid @enderror" id="m_sb_paid_date">
                    @error('paid_date') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>

                <div class="mb-3">
                    <label for="m_sb_currency" class="form-label">Currency <span class="text-danger">*</span></label>
                    <select wire:model.live="payment_currency_id"
                            class="form-control @error('payment_currency_id') is-invalid @enderror" id="m_sb_currency">
                        @foreach($currencies as $currency)
                            <option value="{{ $currency->id }}">{{ $currency->currency_code }} - {{ $currency->name }}</option>
                        @endforeach
                    </select>
                    @error('payment_currency_id') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>

                @if($payment_currency_id != $tzsCurrencyId)
                    <div class="mb-3">
                        <label for="m_sb_rate" class="form-label">Exchange Rate <span class="text-danger">*</span></label>
                        <input type="number" step="0.0001" wire:model.live="payment_exchange_rate"
                               class="form-control @error('payment_exchange_rate') is-invalid @enderror"
                               id="m_sb_rate" placeholder="1 FCY = ? USD">
                        @error('payment_exchange_rate') <div class="invalid-feedback">{{ $message }}</div> @enderror
                    </div>
                @endif

                <div class="mb-3">
                    <label for="m_sb_ref" class="form-label">Payment Reference</label>
                    <input type="text" wire:model.live="reference_no"
                           class="form-control @error('reference_no') is-invalid @enderror"
                           id="m_sb_ref" placeholder="e.g., CHQ-12345">
                    @error('reference_no') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>

                <div class="mb-3">
                    <label for="m_sb_desc" class="form-label">Description</label>
                    <input type="text" wire:model.live="description"
                           class="form-control @error('description') is-invalid @enderror" id="m_sb_desc">
                    @error('description') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" wire:click.prevent="store" class="btn btn-primary flex-fill">
                    <i class="fa fa-plus me-1"></i> Add Payment
                </button>
                <button type="button" class="btn btn-secondary" data-coreui-dismiss="modal">Close</button>
            </div>
        </div>
    </div>
</div>
