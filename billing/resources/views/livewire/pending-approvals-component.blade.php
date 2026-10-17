<div>
    <div class="row align-items-center mb-2">
        <div class="col">
            <h3 class="mb-0"><strong>Approval</strong> Requests</h3>
        </div>
        <div class="col text-end">
            <a href="{{ route('home') }}" class="btn btn-secondary btn-sm">
                <i class="fa fa-arrow-left"></i> Back
            </a>
        </div>
    </div>

    <div class="row mb-2">
        <div class="col-md-4">
            <select wire:model.live="filterStatus" class="form-select form-select-sm">
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
                <option value="all">All</option>
            </select>
        </div>
    </div>

    <div class="row">
        <div class="col-12">
            <div class="card border-0 shadow">
                <div class="card-header">
                    <h2 class="fs-5 fw-bold mb-0">Requests List</h2>
                </div>
                <div class="card-body">
                    <div class="table-responsive d-none d-md-block">
                        <table class="table table-hover table-sm">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Action</th>
                                    <th>Details</th>
                                    <th>Requested By</th>
                                    <th>Approved By</th>
                                    <th>Status</th>
                                    <th class="text-end">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                @forelse($requests as $request)
                                    <tr>
                                        <td>{{ $request->id }}</td>
                                        <td>{{ $request->actionLabel() }}</td>
                                        <td>
                                            {{ $request->actionDescription() }}
                                            @if($request->approvable)
                                                <br>
                                                <small class="text-muted">
                                                    @if($request->action_type === \App\Models\ApprovalRequest::TYPE_DEPARTMENT_MOVE)
                                                        <a href="{{ route('work-order.show', $request->approvable_id) }}" target="_blank">
                                                            Job #{{ $request->approvable->work_number ?? $request->approvable_id }}
                                                        </a>
                                                    @endif
                                                </small>
                                            @endif
                                        </td>
                                        <td>
                                            {{ $request->requestedBy->name ?? 'N/A' }}
                                            <br>
                                            <small class="text-muted">{{ $request->created_at->format('d M Y H:i') }}</small>
                                        </td>
                                        <td>
                                            @if($request->reviewedBy)
                                                {{ $request->reviewedBy->name }}
                                                <br>
                                                <small class="text-muted">{{ $request->reviewed_at?->format('d M Y H:i') }}</small>
                                            @else
                                                <span class="text-muted">-</span>
                                            @endif
                                        </td>
                                        <td>{!! $request->statusBadge() !!}</td>
                                        <td class="text-end text-nowrap">
                                            @if($request->isPending())
                                                <button wire:click="$dispatch('quickApproveRequest', { id: {{ $request->id }} })"
                                                        class="btn btn-outline-success btn-sm"
                                                        title="Approve">
                                                    <i class="fa fa-check"></i>
                                                </button>
                                                <button onclick="confirmAction('reject', {{ $request->id }}, '{{ $request->actionLabel() }}')"
                                                        class="btn btn-outline-danger btn-sm"
                                                        title="Reject">
                                                    <i class="fa fa-times"></i>
                                                </button>
                                                <button wire:click="selectForReview({{ $request->id }})"
                                                        class="btn btn-outline-primary btn-sm"
                                                        data-coreui-toggle="modal"
                                                        data-coreui-target="#reviewModal"
                                                        title="Review details">
                                                    <i class="fa fa-eye"></i>
                                                </button>
                                            @else
                                                <button wire:click="selectForReview({{ $request->id }})"
                                                        class="btn btn-outline-primary btn-sm"
                                                        data-coreui-toggle="modal"
                                                        data-coreui-target="#reviewModal">
                                                    <i class="fa fa-eye"></i> View
                                                </button>
                                            @endif
                                        </td>
                                    </tr>
                                @empty
                                    <tr>
                                        <td colspan="7" class="text-center py-4">
                                            <i class="fa fa-check-circle text-success fa-2x mb-2"></i>
                                            <p class="mb-0">No {{ $filterStatus === 'all' ? '' : $filterStatus }} approval requests.</p>
                                        </td>
                                    </tr>
                                @endforelse
                            </tbody>
                        </table>
                    </div>

                    {{-- Mobile card layout --}}
                    <div class="d-md-none">
                        @forelse($requests as $request)
                            <div class="card border-0 shadow-sm mb-2">
                                <div class="card-body p-3">
                                    <div class="d-flex align-items-center justify-content-between mb-2">
                                        <span class="small fw-semibold">{{ $request->actionLabel() }}</span>
                                        {!! $request->statusBadge() !!}
                                    </div>
                                    <div class="fw-bold" style="font-size: 0.95rem">
                                        {{ $request->actionDescription() }}
                                    </div>
                                    @if($request->approvable && $request->action_type === \App\Models\ApprovalRequest::TYPE_DEPARTMENT_MOVE)
                                        <a href="{{ route('work-order.show', $request->approvable_id) }}" class="small text-primary">
                                            Job #{{ $request->approvable->work_number ?? $request->approvable_id }}
                                        </a>
                                    @endif
                                    <div class="d-flex flex-wrap gap-3 text-muted mt-2" style="font-size: 0.8rem">
                                        <div>
                                            <i class="fa fa-user me-1"></i>{{ $request->requestedBy->name ?? 'N/A' }}
                                            <small class="text-muted d-block">{{ $request->created_at->format('d M Y H:i') }}</small>
                                        </div>
                                        @if($request->reviewedBy)
                                            <div>
                                                <i class="fa fa-user-check me-1"></i>{{ $request->reviewedBy->name }}
                                                <small class="text-muted d-block">{{ $request->reviewed_at?->format('d M Y H:i') }}</small>
                                            </div>
                                        @endif
                                    </div>
                                    @if($request->isPending())
                                        <div class="d-flex flex-wrap gap-1 pt-2 border-top mt-2">
                                            <button wire:click="$dispatch('quickApproveRequest', { id: {{ $request->id }} })"
                                                    class="btn btn-outline-success btn-sm flex-fill"
                                                    title="Approve">
                                                <i class="fa fa-check"></i> Approve
                                            </button>
                                            <button onclick="confirmAction('reject', {{ $request->id }}, '{{ $request->actionLabel() }}')"
                                                    class="btn btn-outline-danger btn-sm flex-fill"
                                                    title="Reject">
                                                <i class="fa fa-times"></i> Reject
                                            </button>
                                            <button wire:click="selectForReview({{ $request->id }})"
                                                    class="btn btn-outline-primary btn-sm flex-fill"
                                                    data-coreui-toggle="modal"
                                                    data-coreui-target="#reviewModal"
                                                    title="Review details">
                                                <i class="fa fa-eye"></i> Review
                                            </button>
                                        </div>
                                    @else
                                        <div class="d-flex flex-wrap gap-1 pt-2 border-top mt-2">
                                            <button wire:click="selectForReview({{ $request->id }})"
                                                    class="btn btn-outline-primary btn-sm flex-fill"
                                                    data-coreui-toggle="modal"
                                                    data-coreui-target="#reviewModal">
                                                <i class="fa fa-eye"></i> View
                                            </button>
                                        </div>
                                    @endif
                                </div>
                            </div>
                        @empty
                            <div class="card border-0 shadow-sm mb-2">
                                <div class="card-body text-center py-4 text-muted">
                                    <i class="fa fa-check-circle text-success fa-2x mb-2 d-block"></i>
                                    No {{ $filterStatus === 'all' ? '' : $filterStatus }} approval requests.
                                </div>
                            </div>
                        @endforelse
                    </div>

                    <div class="d-flex justify-content-center mt-3">
                        {{ $requests->links() }}
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Review Modal -->
    <div class="modal fade" wire:ignore.self id="reviewModal" tabindex="-1" aria-labelledby="reviewModalLabel" aria-hidden="true">
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="reviewModalLabel">Review Approval Request</h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close" wire:click="closeModal"></button>
                </div>
                <div class="modal-body">
                    @if($selectedRequest)
                        <div class="mb-4">
                            <h6 class="text-muted">Request Details</h6>
                            <table class="table table-sm table-bordered">
                                <tr>
                                    <th style="width: 150px;">Action Type</th>
                                    <td>{{ $selectedRequest->actionLabel() }}</td>
                                </tr>
                                <tr>
                                    <th>Description</th>
                                    <td>{{ $selectedRequest->actionDescription() }}</td>
                                </tr>
                                <tr>
                                    <th>Requested By</th>
                                    <td>{{ $selectedRequest->requestedBy->name ?? 'N/A' }}</td>
                                </tr>
                                <tr>
                                    <th>Requested At</th>
                                    <td>{{ $selectedRequest->created_at->format('d M Y H:i:s') }}</td>
                                </tr>
                                <tr>
                                    <th>Reason</th>
                                    <td>{{ $selectedRequest->reason ?? 'No reason provided' }}</td>
                                </tr>
                                @if($selectedRequest->action_type === \App\Models\ApprovalRequest::TYPE_DEPARTMENT_MOVE && $selectedRequest->approvable)
                                    <tr>
                                        <th>Job</th>
                                        <td>
                                            <a href="{{ route('work-order.show', $selectedRequest->approvable_id) }}" target="_blank">
                                                {{ $selectedRequest->approvable->title ?? 'Job #' . $selectedRequest->approvable_id }}
                                                <i class="fa fa-external-link-alt fa-xs"></i>
                                            </a>
                                        </td>
                                    </tr>
                                    <tr>
                                        <th>Customer</th>
                                        <td>{{ $selectedRequest->approvable->customer->name ?? 'N/A' }}</td>
                                    </tr>
                                @endif
                            </table>
                        </div>

                        <div class="mb-3">
                            <label for="reviewNotes" class="form-label">
                                Review Notes <span class="text-muted">(optional)</span>
                            </label>
                            <textarea wire:model.live="reviewNotes"
                                      class="form-control @error('reviewNotes') is-invalid @enderror"
                                      id="reviewNotes"
                                      rows="3"
                                      placeholder="Enter your notes or reason for this decision..."></textarea>
                            @error('reviewNotes')
                                <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>


                    @endif
                </div>
                <div class="modal-footer">

                    @if($selectedRequest)
                        @if($selectedRequest->isPending())
                            <button type="button" wire:key="pa-btn-reject" class="btn btn-danger" wire:click="reject">
                                <i class="fa fa-times"></i> Reject
                            </button>
                            <button type="button" wire:key="pa-btn-approve" class="btn btn-success" wire:click="approve">
                                <i class="fa fa-check"></i> Approve
                            </button>
                        @else
                            <button type="button" wire:key="pa-btn-close" class="btn btn-secondary" data-coreui-dismiss="modal" wire:click="closeModal">
                                Close
                            </button>
                        @endif
                    @endif
                </div>
            </div>
        </div>
    </div>

    @push('scripts')
        <script>
            Livewire.on('closeReviewModal', () => {
                var modal = coreui.Modal.getInstance(document.getElementById('reviewModal'));
                if (modal) {
                    modal.hide();
                }
            });

            function confirmAction(action, id, label) {
                var isApprove = action === 'approve';
                Swal.fire({
                    title: isApprove ? 'Approve Request?' : 'Reject Request?',
                    text: label,
                    icon: 'question',
                    showCancelButton: true,
                    confirmButtonColor: isApprove ? '#2eb85c' : '#e55353',
                    confirmButtonText: isApprove ? 'Approve' : 'Reject',
                    cancelButtonText: 'Cancel',
                    reverseButtons: true
                }).then(function(result) {
                    if (result.isConfirmed) {
                        Livewire.dispatch(isApprove ? 'quickApproveRequest' : 'quickRejectRequest', { id: id });
                    }
                });
            }
        </script>
    @endpush
</div>

