{{-- Payments section --}}
@can('add journal entry')
    <div class="mb-3">
        <h6 class="fw-bold text-success mb-2">Payments</h6>

        @forelse($inventory->payments as $payment)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex justify-content-between align-items-start mb-1">
                        <span class="fw-semibold" style="font-size: 0.9rem;">
                            {{ $payment->account->name }}
                        </span>
                        <span class="fw-bold text-success" style="font-size: 0.95rem;">
                            {{ number_format($payment->credit, 2) }}
                        </span>
                    </div>

                    @if($payment->description)
                        <div class="text-muted small mb-1">{{ $payment->description }}</div>
                    @endif

                    <div class="d-flex flex-wrap gap-3 text-muted" style="font-size: 0.75rem;">
                        @if($payment->document_no)
                            <span><i class="fa fa-receipt me-1"></i>{{ $payment->document_no }}</span>
                        @endif
                        <span><i class="fa fa-calendar me-1"></i>{{ $payment->post_date->format('d M Y') }}</span>
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center text-muted py-3 small">No payments recorded</div>
            </div>
        @endforelse

        @if($inventory->payments->count() > 0)
            <div class="card border-0 shadow-sm">
                <div class="card-body p-3 d-flex justify-content-between fw-bold text-success">
                    <span>Total Paid</span>
                    <span>{{ number_format($inventory->payments->sum('credit'), 2) }}</span>
                </div>
            </div>
        @endif
    </div>
@endcan
