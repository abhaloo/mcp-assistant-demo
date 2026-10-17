{{-- Payment Modal (mobile-optimized) --}}
<div class="modal fade" wire:ignore.self id="modal-payment" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <h2 class="h6 modal-title">Add Payment</h2>
                <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
                <div class="mb-3">
                    <label for="m_method" class="form-label">Method <span class="text-danger">*</span></label>
                    <select wire:model.live="method" class="form-control @error('method') is-invalid @enderror" id="m_method">
                        <option value="">Choose..</option>
                        @forelse($providers as $provider)
                            <option value="{{ $provider->id }}">{{ $provider->name }}</option>
                        @empty
                        @endforelse
                    </select>
                    @error('method') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>

                <div class="mb-3">
                    <label for="m_paid_date" class="form-label">Paid At <span class="text-danger">*</span></label>
                    <input type="date" wire:model.live="paid_date"
                           class="form-control @error('paid_date') is-invalid @enderror" id="m_paid_date">
                    @error('paid_date') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>

                <div class="mb-3">
                    <label for="m_amount" class="form-label">Amount <span class="text-danger">*</span></label>
                    <input type="number" step="0.01" wire:model.live="amount"
                           class="form-control @error('amount') is-invalid @enderror" id="m_amount">
                    @error('amount') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>

                <div class="mb-3">
                    <label for="m_description" class="form-label">Description</label>
                    <input type="text" wire:model.live="description"
                           class="form-control @error('description') is-invalid @enderror" id="m_description">
                    @error('description') <div class="invalid-feedback">{{ $message }}</div> @enderror
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" wire:click.prevent="store" class="btn btn-primary flex-fill">Add Payment</button>
                <button type="button" class="btn btn-secondary" data-coreui-dismiss="modal">Close</button>
            </div>
        </div>
    </div>
</div>
