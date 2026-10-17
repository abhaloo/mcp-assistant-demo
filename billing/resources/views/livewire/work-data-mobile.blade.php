<div style="position: relative;">
    <style>
        /* Mobile view mode - disabled fields stay legible */
        .mobile-view-mode input:disabled,
        .mobile-view-mode textarea:disabled,
        .mobile-view-mode select:disabled {
            color: #333 !important;
            opacity: 1 !important;
            -webkit-text-fill-color: #333 !important;
        }

        .mobile-view-mode .form-check-input:disabled {
            opacity: 1 !important;
        }

        .mobile-view-mode .form-check-input:checked:disabled {
            background-color: #dc3545 !important;
            border-color: #dc3545 !important;
        }

        /* Selected/filled field highlights */
        .mobile-view-mode .option-selected {
            background-color: rgba(220, 53, 69, 0.08);
            border-radius: 6px;
            padding: 0.2rem 0.4rem;
        }

        .mobile-view-mode .label-selected {
            font-weight: 700 !important;
            color: #1a1a1a !important;
        }

        .mobile-view-mode .form-check-label:not(.label-selected) {
            color: #999 !important;
            font-weight: 400 !important;
        }

        /* Sticky bottom action bar */
        .mobile-sticky-bar {
            position: sticky;
            bottom: 0;
            z-index: 10;
            background: #fff;
            border-top: 1px solid #dee2e6;
            padding: 0.75rem;
            margin: 0 -0.75rem -0.75rem;
            box-shadow: 0 -2px 8px rgba(0,0,0,0.08);
        }

        /* Group header touch target */
        .mobile-group-header {
            min-height: 48px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.65rem 1rem;
            background: #f0f1f3;
            border: 1px solid #e0e2e6;
            border-radius: 8px;
            cursor: pointer;
            -webkit-tap-highlight-color: rgba(0,0,0,0.05);
        }

        .mobile-group-header.expanded {
            border-bottom-left-radius: 0;
            border-bottom-right-radius: 0;
            border-bottom-color: transparent;
            margin-bottom: 0;
        }

        /* Card sections */
        .mobile-section {
            background: #fff;
            border: 1px solid #e9ecef;
            border-radius: 8px;
            padding: 1.25rem;
            margin-bottom: 0.75rem;
        }

        /* Remove border from last field in each section */
        .mobile-section > div:last-child {
            border-bottom: none !important;
            padding-bottom: 0 !important;
            margin-bottom: 0 !important;
        }
    </style>

    {{-- ======================== --}}
    {{-- MOBILE ACTION BUTTONS    --}}
    {{-- ======================== --}}
    <div class="d-flex flex-wrap gap-2 mb-3 pb-3" style="border-bottom: 1px solid #e9ecef;">
        @can(['edit job','add job'])
            @if($workOrder->editing == 'CLOSED' && $workOrder->status != 'FINISHED')
                <a href="#" wire:key="wd-btn-edit" class="btn btn-sm btn-warning" wire:click="edit">
                    <i class="fa fa-edit"></i> Edit</a>
            @endif
        @endcan

        <a href="#" wire:key="wd-btn-print" class="btn btn-sm btn-info" data-coreui-toggle="modal" data-coreui-target="#modal-pdf-preview">
            <i class="fa fa-file-pdf"></i> PDF</a>

        {{-- @can('issue item') --}}
            @if($workOrder->status == 'CREATED')
                <a href="#" wire:key="wd-btn-materials" class="btn btn-sm btn-secondary" data-coreui-toggle="modal"
                   data-coreui-target="#modal-material">
                    <i class="fa fa-cubes"></i> Materials</a>
            @endif
        {{-- @endcan --}}

        @can('move job')
            @if($workOrder->status == 'PENDING MOVE APPROVAL')
                <a href="#" wire:key="wd-btn-cancel-move" class="btn btn-sm btn-danger"
                   data-coreui-toggle="modal" data-coreui-target="#modal-cancel-move">
                    <i class="fa fa-times"></i> Cancel Move</a>
                <x-modal.confirm id="modal-cancel-move"
                                 title="Cancel Move Request"
                                 method="cancelMoveRequest"
                                 confirmClass="btn-danger"
                                 confirmIcon="fa-times"
                                 confirmText="Cancel Request">
                    Are you sure you want to cancel the move request for <strong>{{ $workOrder->title }}</strong>?
                </x-modal.confirm>
            @else
                <a href="#" wire:key="wd-btn-update-job" class="btn btn-sm btn-secondary"
                   data-coreui-toggle="modal" data-coreui-target="#modal-work_order">
                    <i class="fa fa-angle-double-right"></i> Update</a>
            @endif
        @endcan

        @can('approve job requests')
            @if($workOrder->status == 'PENDING MOVE APPROVAL')
                <a href="#" wire:key="wd-btn-approve" class="btn btn-sm btn-success"
                   data-coreui-toggle="modal" data-coreui-target="#modal-approve-move">
                    <i class="fa fa-check"></i> Approve</a>
                <x-modal.confirm id="modal-approve-move"
                                 title="Approve Move Request"
                                 method="approveRequest"
                                 confirmClass="btn-success"
                                 confirmIcon="fa-check"
                                 confirmText="Approve">
                    Are you sure you want to approve the move request for <strong>{{ $workOrder->title }}</strong>?
                </x-modal.confirm>
            @endif
        @endcan
    </div>

    {{-- ======================== --}}
    {{-- UPDATE JOB MODAL         --}}
    {{-- ======================== --}}
    <div class="modal fade" wire:ignore.self id="modal-work_order" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">Update Job</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        @can('edit job')
                            <div class="mb-4">
                                <div class="form-check form-switch">
                                    <input class="form-check-input" wire:model.live="delivered" type="checkbox" role="switch"
                                           id="m_delivery">
                                    <label class="form-check-label" for="m_delivery">DELIVERED TO CUSTOMER ?</label>
                                </div>
                            </div>
                        @endcan

                        <div class="mb-4">
                            <label>Current Department</label>
                            <input type="text" class="form-control" value="{{ $workOrder->department->name ?? 'None' }}" readonly disabled>
                        </div>

                        <div class="mb-4">
                            <label for="m_department">Move To <span class="text-danger">*</span></label>
                            <select wire:model.live="department"
                                    class="form-control @error('department') is-invalid @enderror" id="m_department">
                                <option value="">--Select--</option>
                                @forelse($departments as $department)
                                    @if($department->id != $workOrder->department_id)
                                        <option value="{{ $department->id }}">{{ $department->name }}</option>
                                    @endif
                                @empty
                                @endforelse
                            </select>
                            @error('department')
                            <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="m_priority">Priority</label>
                            <select wire:model.live="priority" class="form-control" id="m_priority">
                                <option value="normal">Normal</option>
                                <option value="high">High</option>
                            </select>
                        </div>

                        <div class="mb-4">
                            <label for="m_remark">Description <span class="text-danger">*</span></label>
                            <textarea wire:model.live="remark"
                                      class="form-control @error('remark') is-invalid @enderror"
                                      id="m_remark"></textarea>
                            @error('remark')
                            <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>
                        <div class="modal-footer">
                            <button type="button" wire:click.prevent="move" class="btn btn-secondary">Update</button>
                            <button type="button" class="btn btn-danger text-gray-600 ms-auto"
                                    data-coreui-dismiss="modal">Close</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    {{-- ================================= --}}
    {{-- MOBILE FORM (Edit/View)            --}}
    {{-- ================================= --}}
    <div class="{{ $workOrder->editing == 'CLOSED' ? 'mobile-view-mode' : '' }}">
        @php
            $sortedItems = $workOrder->specification->items->sortBy('sort_order');
            $grouped = $sortedItems->groupBy(fn($item) => $item->group_name ?? '');
            $ungrouped = $grouped->get('', collect());
            $grouped = $grouped->forget('');
        @endphp

        {{-- Job metadata card --}}
        <div class="mobile-section">
            <div class="d-flex align-items-center gap-2 mb-2 pb-2" style="border-bottom: 1px solid #eee;">
                <img src="{{ asset('img/logo/logo.gif') }}" width="40">
                <div style="font-size: 0.8rem; line-height: 1.5;">
                    <div><strong>Job #:</strong> {{ $workOrder->work_number ?: $workOrder->id }}</div>
                    <div><strong>Bill #:</strong> {{ !$workOrder->bill ? 'None' : ($workOrder->bill->invoice_number ?? $workOrder->bill->id) }}</div>
                    <div><strong>Created:</strong> {{ $workOrder->created_at->format('d M Y') }} by {{ $workOrder->user->name ?? 'N/A' }}</div>
                </div>
            </div>

            {{-- Customer --}}
            <div class="mb-3">
                <label class="form-label fw-semibold text-dark small mb-1">Customer</label>
                <div class="d-flex align-items-center">
                    <span class="form-control bg-light" style="font-size: 0.9rem;">{{ $workOrder->customer->name ?? 'N/A' }}</span>
                    @if($workOrder->editing == 'OPEN')
                        <a href="#" class="btn btn-sm btn-outline-primary ms-2 flex-shrink-0" data-coreui-toggle="modal" data-coreui-target="#modal-customer-edit">
                            <i class="fa fa-edit"></i>
                        </a>
                    @endif
                </div>
            </div>

            {{-- Job Description --}}
            <div class="mb-3">
                <label class="form-label fw-semibold text-dark small mb-1">Job Description</label>
                <input type="text" class="form-control" wire:model.live="title"
                       placeholder="Job description"
                       style="font-size: 0.9rem;"
                       @if($title && $workOrder->editing == 'CLOSED') disabled @endif>
            </div>

            {{-- Ordered Qty + Pages --}}
            <div class="row">
                <div class="col-6 mb-3">
                    <label class="form-label fw-semibold text-dark small mb-1">Ordered Qty</label>
                    <input type="text" class="form-control" wire:model.live="ordered_qty"
                           placeholder="Qty"
                           style="font-size: 0.9rem;"
                           @if($ordered_qty > 1 && $workOrder->editing == 'CLOSED') disabled @endif>
                </div>
                <div class="col-6 mb-3">
                    <label class="form-label fw-semibold text-dark small mb-1">No. of Pages</label>
                    <input type="text" class="form-control" wire:model.live="no_pages"
                           placeholder="Pages"
                           style="font-size: 0.9rem;"
                           @if($no_pages && $workOrder->editing == 'CLOSED') disabled @endif>
                </div>
            </div>
        </div>

        {{-- Ungrouped specification fields --}}
        @if($ungrouped->count() > 0)
            <div class="mobile-section">
                @foreach($ungrouped as $item)
                    @include('livewire.partials.modern-field-mobile', ['item' => $item, 'payload' => $payload, 'workOrder' => $workOrder])
                @endforeach
            </div>
        @endif

        {{-- Grouped specification fields --}}
        @foreach($grouped as $groupName => $items)
            @php
                $isExpanded = $this->isGroupExpanded($groupName);
                $filledCount = $this->getFilledFieldCount($groupName);
                $totalCount = $items->count();
            @endphp

            <div class="mobile-group-header mt-2 {{ $isExpanded ? 'expanded' : '' }}"
                 wire:click="toggleGroup('{{ $groupName }}')">
                <div class="d-flex align-items-center">
                    <i class="fa fa-{{ $isExpanded ? 'chevron-down' : 'chevron-right' }} me-2" style="font-size: 0.75rem; width: 12px;"></i>
                    <strong style="font-size: 0.85rem; text-transform: uppercase;">{{ $groupName }}</strong>
                </div>
                <x-badge :color="$filledCount > 0 ? 'success' : 'secondary'" style="font-size: 0.7rem;">
                    {{ $filledCount }}/{{ $totalCount }}
                </x-badge>
            </div>

            @if($isExpanded)
                <div class="mobile-section" style="margin-top: 0; border-top-left-radius: 0; border-top-right-radius: 0; border-top: none;">
                    @foreach($items as $item)
                        @include('livewire.partials.modern-field-mobile', ['item' => $item, 'payload' => $payload, 'workOrder' => $workOrder])
                    @endforeach
                </div>
            @endif
        @endforeach

        {{-- Bottom spacer to prevent sticky bar from covering content --}}
        @if($workOrder->editing != 'CLOSED')
            <div style="height: 70px;"></div>
        @endif

        {{-- Sticky bottom action bar --}}
        @if($workOrder->editing != 'CLOSED')
            <div class="mobile-sticky-bar">
                <div class="d-flex gap-2">
                    @can('edit job')
                        <button wire:key="wd-btn-save" wire:click="store" class="btn btn-success flex-fill" wire:loading.attr="disabled">
                            <span wire:loading.remove wire:target="store"><i class="fa fa-save me-1"></i>Save Changes</span>
                            <span wire:loading wire:target="store"><i class="fa fa-spinner fa-spin me-1"></i>Saving...</span>
                        </button>
                    @endcan
                    @can('add job')
                        @if($workOrder->status == 'CREATED' || $workOrder->editing == 'OPEN')
                            <button wire:key="wd-btn-submit" wire:click="submit" class="btn btn-primary flex-fill" wire:loading.attr="disabled">
                                <span wire:loading.remove wire:target="submit"><i class="fa fa-upload me-1"></i>Submit</span>
                                <span wire:loading wire:target="submit"><i class="fa fa-spinner fa-spin me-1"></i>Submitting...</span>
                            </button>
                        @endif
                    @endcan
                </div>
            </div>
        @endif
    </div>

    {{-- ============================================== --}}
    {{-- PRINT VIEW (Hidden, for PDF Export)             --}}
    {{-- ============================================== --}}
    @include('livewire.partials.work-data-print')

    {{-- ======================== --}}
    {{-- MODALS (shared)           --}}
    {{-- ======================== --}}

    <!-- Modal Add Materials -->
    <div class="modal fade" wire:ignore.self id="modal-material" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-scrollable" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">Job Materials</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="mb-3">
                        <label for="m_warehouse">Warehouse</label>
                        <select class="form-control" wire:model.live="warehouse_id" id="m_warehouse">
                            <option>Choose a warehouse..</option>
                            @forelse($warehouses as $warehouse)
                                <option value="{{$warehouse->id}}">{{$warehouse->name}}</option>
                            @empty
                            @endforelse
                        </select>
                        @error('warehouse_id')
                        <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>
                    <div class="mb-3">
                        @livewire('product-search-component', ['Material'])
                    </div>
                    <div class="table-responsive">
                        <table class="table table-bordered table-sm bg-light">
                            <tr>
                                <th>Item</th>
                                <th>Qty</th>
                                <th>Unit</th>
                                <th></th>
                            </tr>
                            <tbody>
                            @forelse($work_items as $item)
                                <tr>
                                    <td>{{$item['name']}}</td>
                                    <td>
                                        <input type="number" step="0.01" class="form-control form-control-sm"
                                               wire:model.live="work_items.{{$item['id']}}.quantity"
                                               wire:keyup="checkBalance({{$item['id']}})">
                                    </td>
                                    <td>
                                        <input type="text" disabled class="form-control form-control-sm"
                                               wire:model.live="work_items.{{$item['id']}}.unit">
                                    </td>
                                    <td>
                                        <a href="#" class="btn btn-outline-danger btn-sm"
                                           wire:click="itemDelete({{$item['id']}})"><i class="fa fa-trash"></i></a>
                                    </td>
                                </tr>
                            @empty
                            @endforelse
                            </tbody>
                        </table>
                    </div>

                    <textarea wire:model.live="description" class="form-control mb-3"
                              placeholder="Job Description"></textarea>

                    <a href="#" class="btn btn-primary w-100" wire:click="store">Save</a>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal Customer Edit -->
    <div class="modal fade" wire:ignore.self id="modal-customer-edit" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">Change Customer</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="mb-3">
                        @livewire('customer-search-component', key('customer-search-work-order-mobile'))
                    </div>
                    <a class="btn btn-primary w-100 mt-2" wire:click="changeCustomer">Change</a>
                </div>
            </div>
        </div>
    </div>

    <!-- PDF Preview Modal -->
    <div class="modal fade" id="modal-pdf-preview" tabindex="-1" role="dialog" aria-labelledby="modal-pdf-preview-label" aria-hidden="true">
        <div class="modal-dialog modal-dialog-scrollable" role="document">
            <div class="modal-content">
                <div class="modal-header bg-info text-white">
                    <h5 class="modal-title" id="modal-pdf-preview-label">
                        <i class="fa fa-file-pdf me-2"></i>PDF Preview
                    </h5>
                    <button type="button" class="btn-close btn-close-white" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body p-0">
                    <div class="alert alert-info m-3 mb-0 small">
                        <i class="fa fa-info-circle me-1"></i>
                        Preview of the PDF export. Tap "Download" to save.
                    </div>
                    <div id="pdf-preview-container" class="p-3" style="max-height: 50vh; overflow-y: auto; background: #f8f9fa;">
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">Cancel</button>
                    <button type="button" class="btn btn-info btn-sm" onclick="DownloadPDF()">
                        <i class="fa fa-download me-1"></i>Download
                    </button>
                </div>
            </div>
        </div>
    </div>

    {{-- ======================== --}}
    {{-- SCRIPTS (shared)          --}}
    {{-- ======================== --}}
    @push('scripts')
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"
                integrity="sha512-GsLlZN/3F2ErC5ifS5QtgpiJtWd43JWSuIgh7mbzZ8zBps+dvLusV+eNQATqgA/HdeKFVgA5v3S/cIrLF7QnIg=="
                crossorigin="anonymous" referrerpolicy="no-referrer"></script>
        <script>
            Livewire.on('closeMaterialModal', () => {
                $('#modal-material').modal('hide')
            })

            Livewire.on('closeMoveModal', () => {
                $('#modal-work_order').modal('hide')
            })

            Livewire.on('closeCustomerChange', () => {
                $('#modal-customer-edit').modal('hide')
            })

            // Show preview when modal opens
            document.getElementById('modal-pdf-preview').addEventListener('show.coreui.modal', function () {
                let printer = document.getElementById('printer');
                let previewContainer = document.getElementById('pdf-preview-container');

                printer.style.display = 'block';
                previewContainer.innerHTML = printer.outerHTML;
                previewContainer.querySelector('#printer').style.display = 'block';
                printer.style.display = 'none';
            });

            function DownloadPDF() {
                let printer = document.getElementById('printer');

                printer.style.display = 'block';

                let element = printer.cloneNode(true);
                let checkboxes = element.getElementsByClassName('input-check');

                for (let i = checkboxes.length - 1; i >= 0; i--) {
                    if(checkboxes[i].checked){
                        let span = document.createElement('span');
                        span.style.width = '20px';
                        span.style.height = '20px';
                        span.style.display = 'inline-block';
                        span.style.backgroundColor = '#e51010';
                        checkboxes[i].parentNode.replaceChild(span, checkboxes[i]);
                    }
                }

                let opt = {
                    margin: 0.1,
                    filename: 'Job#{{$workOrder->work_number ?? $workOrder->id}}.pdf',
                    image: {type: 'jpeg', quality: 0.98},
                    html2canvas: {scale: 1.5, letterRendering: true, useCORS: true},
                    jsPDF: {unit: 'in', format: 'A4', orientation: 'portrait'}
                };

                html2pdf(element, opt).then(function() {
                    printer.style.display = 'none';
                    var modal = coreui.Modal.getInstance(document.getElementById('modal-pdf-preview'));
                    if (modal) modal.hide();
                });
            }

            function PrintJob() {
                var modal = new coreui.Modal(document.getElementById('modal-pdf-preview'));
                modal.show();
            }
        </script>
    @endpush
</div>
