@extends('layouts.app')

@section('content')
    <div
        data-mcp-page-context-owner="jobs"
        data-mcp-context-route="work-order.show"
        data-mcp-context-kind="detail"
        data-mcp-context-resource="work_order"
        data-mcp-context-id="jobs:show:{{ $workOrder->id }}"
        data-mcp-context-job-id="{{ $workOrder->id }}"
    >
    <div class="d-flex align-items-center mb-2">
        @if(($fromOrder ?? false) && Route::has('customer-orders.show'))
            <a href="{{ route('customer-orders.show', $fromOrder) }}" class="btn btn-sm btn-outline-secondary rounded-circle flex-shrink-0" style="width: 36px; height: 36px; padding: 0; display: flex; align-items: center; justify-content: center;">
                <i class="fa fa-chevron-left"></i>
            </a>
        @elseif($call_back && Route::has('invoice-works'))
            <a href="{{ route('invoice-works', $workOrder->bill) }}" class="btn btn-sm btn-outline-secondary rounded-circle flex-shrink-0" style="width: 36px; height: 36px; padding: 0; display: flex; align-items: center; justify-content: center;">
                <i class="fa fa-chevron-left"></i>
            </a>
        @else
            <a href="{{ route('work-order.index') }}" class="btn btn-sm btn-outline-secondary rounded-circle flex-shrink-0" style="width: 36px; height: 36px; padding: 0; display: flex; align-items: center; justify-content: center;">
                <i class="fa fa-chevron-left"></i>
            </a>
        @endif

        <div class="flex-grow-1 text-center">
            <h1 class="h5 mb-0 fw-bold">Job #{{ $workOrder->work_number ?: $workOrder->id }}</h1>
        </div>
        {{-- Spacer to balance back button --}}
        <div style="width: 36px;" class="flex-shrink-0"></div>
    </div>

    {{-- Status badges --}}
    <div class="d-flex align-items-center justify-content-center flex-wrap gap-2 mb-3">
        {!! $workOrder->getStatus() !!}
        @if($workOrder->priority == 'high')
            <x-badge color="danger"><i class="fa fa-exclamation me-1"></i>High</x-badge>
        @endif
        <x-badge color="warning">{{ $workOrder->section }}</x-badge>
        @if($workOrder->status == 'PENDING MOVE APPROVAL')
            @php
                $pendingApproval = $workOrder->pendingApproval(\App\Models\ApprovalRequest::TYPE_DEPARTMENT_MOVE);
            @endphp
            @if($pendingApproval)
                <i class="fa fa-info-circle text-primary" style="cursor: help;" title="{{ $pendingApproval->actionDescription() }}"></i>
            @endif
        @endif
    </div>

    {{-- Horizontal tab navigation --}}
    <style>
        .mobile-tab-nav { background: #f0f1f3; border-radius: 8px; padding: 3px; }
        .mobile-tab-nav .nav-link {
            font-size: 0.82rem;
            font-weight: 600;
            color: #555;
            border-radius: 6px;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
        }
        .mobile-tab-nav .nav-link i {
            font-size: 0.78rem;
            line-height: 1;
        }
        .mobile-tab-nav .nav-link:hover { color: #333; }
        .mobile-tab-nav .nav-link.active {
            background: #fff;
            color: #1a1a1a;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
    </style>
    <ul class="nav nav-pills nav-fill mb-3 mobile-tab-nav" role="tablist">
        <li class="nav-item" role="presentation">
            <a class="nav-link active py-2 px-2" href="#home" data-coreui-toggle="tab" role="tab">
                <i class="fa fa-file-text"></i><span>Info</span>
            </a>
        </li>
        <li class="nav-item" role="presentation">
            <a class="nav-link py-2 px-2" href="#messages" data-coreui-toggle="tab" role="tab">
                <i class="fa fa-repeat"></i><span>Moves</span>
            </a>
        </li>
        @can('view inventory')
            <li class="nav-item" role="presentation">
                <a class="nav-link py-2 px-2" href="#materials" data-coreui-toggle="tab" role="tab">
                    <i class="fa fa-cubes"></i><span>Mat.</span>
                </a>
            </li>
        @endcan
        <li class="nav-item" role="presentation">
            <a class="nav-link py-2 px-2" href="#attachments" data-coreui-toggle="tab" role="tab">
                <i class="fa fa-image"></i><span>Art</span>
            </a>
        </li>
    </ul>

    {{-- Tab content --}}
    <div class="tab-content">
        {{-- INFO TAB --}}
        <div class="tab-pane active show" id="home" role="tabpanel">
            @livewire('work-data-component', ['workOrder' => $workOrder, 'mobile' => true])
        </div>

        {{-- MOVEMENTS TAB --}}
        <div class="tab-pane" id="messages" role="tabpanel">
            @forelse($workOrder->messages->sortByDesc('id') as $message)
                <div class="card border-0 shadow-sm mb-2">
                    <div class="card-body p-3">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <span class="small text-muted">
                                <i class="fa fa-calendar me-1"></i>{{ $message->created_at->format('d M Y H:i') }}
                            </span>
                            <x-badge color="secondary">{{ $message->status }}</x-badge>
                        </div>
                        <div class="fw-bold mb-1">{!! $message->section !!}</div>
                        @if($message->message)
                            <div class="text-muted small mb-1">{{ $message->message }}</div>
                        @endif
                        <div class="text-muted small">
                            <i class="fa fa-user me-1"></i>{{ $message->assigned_by }}
                        </div>
                    </div>
                </div>
            @empty
                <div class="text-center py-4 text-muted">
                    <div class="mb-2"><i class="fa fa-inbox fa-2x"></i></div>
                    No movements recorded.
                </div>
            @endforelse
        </div>

        {{-- MATERIALS TAB --}}
        <div class="tab-pane" id="materials" role="tabpanel">
            @if(Route::has('inventory.create'))
                <div class="mb-3">
                    <a href="{{ route('inventory.create', ['type' => 'Issue', 'job_number' => $workOrder->id]) }}"
                       class="btn btn-primary btn-sm w-100">
                        <i class="fa fa-plus me-1"></i>Add More Materials
                    </a>
                </div>
            @endif

            @php $hasMaterials = false; @endphp
            @foreach($workOrder->materials as $material)
                @foreach($material->inventoryItems as $item)
                    @php $hasMaterials = true; @endphp
                    <div class="card border-0 shadow-sm mb-2">
                        <div class="card-body p-3">
                            <div class="fw-bold mb-1">{{ $item->product->name }}</div>
                            <div class="d-flex flex-wrap gap-3 text-muted small">
                                <span><i class="fa fa-hashtag me-1"></i>Issue 
                                    @if(Route::has('inventory.show'))
                                        <a href="{{ route('inventory.show', $item->inventory_id) }}">{{ $item->inventory_id }}</a>
                                    @else
                                        {{ $item->inventory_id }}
                                    @endif
                                </span>
                                <span><i class="fa fa-barcode me-1"></i>ID: {{ $item->product->id }}</span>
                                <span><i class="fa fa-cubes me-1"></i>Qty: <strong class="text-dark">{{ $item->quantity }}</strong></span>
                            </div>
                        </div>
                    </div>
                @endforeach
            @endforeach
            @if(!$hasMaterials)
                <div class="text-center py-4 text-muted">
                    <div class="mb-2"><i class="fa fa-cubes fa-2x"></i></div>
                    No materials issued.
                </div>
            @endif
        </div>

        {{-- ARTWORKS TAB --}}
        <div class="tab-pane" id="attachments" role="tabpanel">
            @livewire('work-order-attachment-component', ['work_order_id' => $workOrder->id])
        </div>
    </div>
@endsection

@push('scripts')
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-coreui-toggle="tooltip"]'));
            tooltipTriggerList.forEach(function(tooltipTriggerEl) {
                new coreui.Tooltip(tooltipTriggerEl);
            });
        });
    </script>
@endpush
