@extends('layouts.app')

@section('content')
    <div class="row"
        data-mcp-page-context-owner="jobs"
        data-mcp-context-route="work-order.show"
        data-mcp-context-kind="detail"
        data-mcp-context-resource="work_order"
        data-mcp-context-id="jobs:show:{{ $workOrder->id }}"
        data-mcp-context-job-id="{{ $workOrder->id }}"
    >
        <div class="col">
            <h1 class="h3 mb-3">
                Job #{{$workOrder->work_number ? : $workOrder->id}} {!! $workOrder->getStatus() !!}
                @if($workOrder->priority == 'high')
                    <x-badge color="danger" class="ms-2" data-coreui-toggle="tooltip" title="High Priority">
                        <i class="fa fa-exclamation"></i>
                    </x-badge>
                @endif
                @if($workOrder->status == 'PENDING MOVE APPROVAL')
                    @php
                        $pendingApproval = $workOrder->pendingApproval(\App\Models\ApprovalRequest::TYPE_DEPARTMENT_MOVE);
                    @endphp
                    @if($pendingApproval)
                        <i class="fa fa-info-circle text-primary ms-2" 
                           style="cursor: help; font-size: 0.9rem;" 
                           data-coreui-toggle="tooltip" 
                           data-coreui-placement="right" 
                           title="{{ $pendingApproval->actionDescription() }}"></i>
                    @endif
                @endif
                <x-badge color="warning" class="ms-2">{{ $workOrder->section }}</x-badge>
            </h1>
        </div>

        <div class="col">
            <div class="float-end">
                @if(($fromOrder ?? false) && Route::has('customer-orders.show'))
                    <a href="{{ route('customer-orders.show', $fromOrder) }}"
                       class="btn btn-sm btn-secondary d-inline-flex align-items-center ms-2">
                        <i class="fa fa-chevron-left me-2"></i>
                        Back to Order
                    </a>
                @else
                    <x-back-link :fallback="route('work-order.index')"
                       class="btn btn-sm btn-secondary d-inline-flex align-items-center ms-2">
                        <i class="fa fa-chevron-left me-2"></i>
                        Back
                    </x-back-link>
                @endif
            </div>
        </div>
    </div>
    <div class="card card-body border-0 shadow table-wrapper table-responsive">

        <div class="tab tab-vertical">
            <ul class="nav nav-tabs" role="tablist">
                <li class="nav-item" role="presentation">
                    <a class="nav-link active" href="#home" data-coreui-toggle="tab" role="tab" aria-selected="true">
                        <i class="fa fa-file-text fs-5"></i>
                        INFO.
                    </a>
                </li>
                <li class="nav-item" role="presentation">
                    <a class="nav-link" href="#messages" data-coreui-toggle="tab" role="tab" aria-selected="false"
                       tabindex="-1">
                        <i class="fa fa-repeat fs-5"></i>
                        MOVEMENTS
                    </a>
                </li>

                @can('view inventory')
                    <li class="nav-item" role="presentation">
                        <a class="nav-link" href="#materials" data-coreui-toggle="tab" role="tab" aria-selected="false"
                           tabindex="-1">
                            <i class="fa fa-cubes fs-5"></i>
                            MATERIALS
                        </a>
                    </li>
                @endcan
                <li class="nav-item" role="presentation">
                    <a class="nav-link" href="#attachments" data-coreui-toggle="tab" role="tab" aria-selected="false"
                       tabindex="-1">
                        <i class="fa fa-image fs-5"></i>
                        ARTWORKS
                    </a>
                </li>
            </ul>

            <div class="tab-content mt-4">
                <div class="tab-pane active show mt-2" id="home" role="tabpanel">
                    @livewire('work-data-component', [$workOrder])
                </div>

                <div class="tab-pane mt-2" id="messages" role="tabpanel">
                    <table class="table table-striped">
                        <tr>
                            <th>Date</th>
                            <th>Movement</th>
                            <th>Status</th>
                            <th>Message</th>
                            <th>Assigned By</th>
                        </tr>
                        @forelse($workOrder->messages->sortByDesc('id') as $message)
                            <tr>
                                <td>{{ $message->created_at }}</td>
                                <td>{!! $message->section !!}</td>
                                <td>{{ $message->status }}</td>
                                <td>{{ $message->message }}</td>
                                <td>{{ $message->assigned_by }}</td>
                            </tr>
                        @empty
                        @endforelse
                    </table>
                </div>

                <div class="tab-pane mt-2" id="materials" role="tabpanel">
                    @if(Route::has('inventory.create'))
                        <a href="{{ route('inventory.create', ['type' => 'Issue', 'job_number' => $workOrder->id]) }}"
                           class="btn btn-primary float-end mb-2">Add More Materials</a>
                    @endif

                    <table class="table table-bordered">
                        <tr class="bg-light">
                            <th>Issue Id</th>
                            <th>Material Id</th>
                            <th>Material Name</th>
                            <th>Material Qty</th>
                        </tr>

                        @foreach($workOrder->materials as $material)
                            @foreach($material->inventoryItems as $item)
                                <tr>
                                    <td>
                                        @if(Route::has('inventory.show'))
                                            <a href="{{ route('inventory.show', $item->inventory_id) }}" target="_blank">
                                                {{ $item->inventory_id }}
                                            </a>
                                        @else
                                            {{ $item->inventory_id }}
                                        @endif
                                    </td>
                                    <td>{{ $item->product->id }}</td>
                                    <td>{{ $item->product->name }}</td>
                                    <td>{{ $item->quantity }}</td>
                                </tr>
                            @endforeach

                        @endforeach
                    </table>
                </div>
                <div class="tab-pane mt-2" id="attachments" role="tabpanel">
                    @livewire('work-order-attachment-component',['work_order_id' => $workOrder->id])
                </div>
            </div>
        </div>

    </div>
@endsection

@push('scripts')
    <script>
        // Initialize CoreUI tooltips
        document.addEventListener('DOMContentLoaded', function() {
            const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-coreui-toggle="tooltip"]'));
            tooltipTriggerList.forEach(function(tooltipTriggerEl) {
                new coreui.Tooltip(tooltipTriggerEl);
            });
        });
    </script>
@endpush
