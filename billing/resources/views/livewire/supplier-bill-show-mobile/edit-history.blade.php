{{-- Edit history (collapsible) --}}
@if($editHistory && $editHistory->count() > 0)
    <div class="card border-0 shadow-sm mb-3">
        <div class="card-header bg-light d-flex justify-content-between align-items-center"
             data-coreui-toggle="collapse" data-coreui-target="#editHistoryCollapse"
             style="cursor: pointer;">
            <span>
                <i class="fa fa-history me-2"></i>
                <strong class="small">Edit History</strong>
                <x-badge color="secondary" class="ms-1">{{ $editHistory->count() }}</x-badge>
            </span>
            <i class="fa fa-chevron-down small"></i>
        </div>
        <div class="collapse" id="editHistoryCollapse">
            <div class="card-body p-0">
                @foreach($editHistory as $history)
                    @php $data = $history['data']; @endphp
                    <div class="p-3 {{ !$loop->last ? 'border-bottom' : '' }}">
                        <div class="d-flex justify-content-between mb-1" style="font-size: 0.8rem;">
                            <span class="text-muted">{{ \Carbon\Carbon::parse($history['date'])->format('d M Y H:i') }}</span>
                            <span class="fw-semibold">{{ $history['user'] }}</span>
                        </div>
                        <div class="d-flex flex-wrap gap-1">
                            @if(isset($data['reference']))
                                <x-badge color="light">Ref: {{ $data['reference'] ?? 'N/A' }}</x-badge>
                            @endif
                            @if(isset($data['exchange_rate']))
                                <x-badge color="light">Rate: {{ number_format($data['exchange_rate'] ?? 0, 2) }}</x-badge>
                            @endif
                            @if(isset($data['clearing_charges']))
                                <x-badge color="light">Clear: {{ number_format($data['clearing_charges'] ?? 0, 2) }}</x-badge>
                            @endif
                            @if(isset($data['freight_charges']))
                                <x-badge color="light">Freight: {{ number_format($data['freight_charges'] ?? 0, 2) }}</x-badge>
                            @endif
                            @if(isset($data['other_charges']))
                                <x-badge color="light">Other: {{ number_format($data['other_charges'] ?? 0, 2) }}</x-badge>
                            @endif
                            @if(isset($data['total_landed_cost_tzs']))
                                <x-badge color="light">Landed: {{ number_format($data['total_landed_cost_tzs'] ?? 0, 2) }}</x-badge>
                            @endif
                        </div>
                    </div>
                @endforeach
            </div>
        </div>
    </div>
@endif
