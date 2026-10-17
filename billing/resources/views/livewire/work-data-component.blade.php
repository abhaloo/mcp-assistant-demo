<div style="position: relative">
    <div class="row" id="header">
        <div class="col-md-12 mb-2">
            <h3 class="float-start">
                {{ $workOrder->title }}
            </h3>
            <div class="btn-group float-end">
                @can('edit job')
                    <a href="#" wire:key="wd-btn-save" class="btn btn-success float-end" wire:click="store">
                        <i class="fa fa-save"></i> Save Changes</a>
                @endcan

                @can('add job')
                    @if($workOrder->status == 'CREATED' || $workOrder->editing == 'OPEN')
                        <a href="#" wire:key="wd-btn-submit" class="btn btn-primary" wire:click="submit"> <i class="fa fa-upload"></i>
                            Submit Job</a>
                    @endif
                @endcan

                {{-- @can('issue item') --}}
                    @if($workOrder->status == 'CREATED')
                        <a href="#" wire:key="wd-btn-materials" class="btn btn-secondary" data-coreui-toggle="modal"
                           data-coreui-target="#modal-material"> <i class="fa fa-cubes"></i> Job
                            Materials</a>
                    @endif
                {{-- @endcan --}}


                @can(['edit job','add job'])
                    @if($workOrder->editing == 'CLOSED' && $workOrder->status != 'FINISHED')
                        <a href="#" wire:key="wd-btn-edit" class="btn btn-warning" wire:click="edit">
                            <i class="fa fa-edit"></i> Edit Job</a>
                    @endif
                @endcan
                <a href="#" wire:key="wd-btn-print" class="btn btn-info" data-coreui-toggle="modal" data-coreui-target="#modal-pdf-preview">
                    <i class="fa fa-file-pdf"></i> Export PDF</a>
                @can('move job')
                    @if($workOrder->status == 'PENDING MOVE APPROVAL')
                        <a href="#" wire:key="wd-btn-cancel-move" class="btn btn-danger btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-cancel-move">
                            <i class="fa fa-times"></i>
                            Cancel Move Request</a>
                        <x-modal.confirm id="modal-cancel-move" 
                                         title="Cancel Move Request" 
                                         method="cancelMoveRequest"
                                         confirmClass="btn-danger"
                                         confirmIcon="fa-times"
                                         confirmText="Cancel Request">
                            Are you sure you want to cancel the move request for <strong>{{ $workOrder->title }}</strong>?
                        </x-modal.confirm>
                    @else
                        <a href="#" wire:key="wd-btn-update-job" class="btn btn-secondary btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-work_order">
                            <i class="fa fa-angle-double-right"></i>
                            Update</a>
                    @endif
                @endcan

                @can('approve job requests')
                    @if($workOrder->status == 'PENDING MOVE APPROVAL')
                        <a href="#" wire:key="wd-btn-approve" class="btn btn-success btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-approve-move">
                            <i class="fa fa-check"></i>
                            Approve</a>
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
        </div>
    </div>
    <!-- Modal Content -->
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
                                           id="delivery">
                                    <label class="form-check-label" for="delivery">DELIVERED TO CUSTOMER ?</label>
                                </div>
                            </div>
                        @endcan

                        <div class="mb-4">
                            <label>Current Department</label>
                            <input type="text" class="form-control" value="{{ $workOrder->department->name ?? 'None' }}" readonly disabled>
                        </div>

                        <div class="mb-4">
                            <label for="section">Move To <span class="text-danger">*</span> </label>
                            <select wire:model.live="department"
                                    class="form-control @error('department') is-invalid @enderror" id="department">
                                <option value="">--Select--</option>
                                @forelse($departments as $department)
                                    @if($department->id != $workOrder->department_id)
                                        <option value="{{ $department->id }}">{{ $department->name }}</option>
                                    @endif
                                @empty
                                @endforelse
                            </select>
                            @error('department')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="priority">Priority</label>
                            <select wire:model.live="priority" class="form-control" id="priority">
                                <option value="normal">Normal</option>
                                <option value="high">High</option>
                            </select>
                        </div>


                        <div class="mb-4">
                            <label for="remark">Description <span class="text-danger">*</span></label>
                            <textarea wire:model.live="remark"
                                      class="form-control @error('remark') is-invalid @enderror"
                                      id="remark"></textarea>
                            @error('remark')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="modal-footer">

                            <button type="button" wire:click.prevent="move" class="btn btn-secondary">
                                Update
                            </button>
                            <button type="button" class="btn btn-danger text-gray-600 ms-auto"
                                    data-coreui-dismiss="modal">
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!-- End of Modal Content -->
    
    <!-- ============================================== -->
    <!-- MODERN EDIT VIEW (Visible, Interactive)        -->
    <!-- ============================================== -->
    <style>
        /* View mode - keep disabled fields legible for operators */
        .view-mode input:disabled,
        .view-mode textarea:disabled,
        .view-mode select:disabled {
            color: #333 !important;
            opacity: 1 !important;
            -webkit-text-fill-color: #333 !important;
            background: transparent !important;
            cursor: default;
        }

        /* Larger, clearer checkboxes in view mode */
        .view-mode .form-check-input {
            width: 1.3em;
            height: 1.3em;
        }

        .view-mode .form-check-input:disabled {
            opacity: 1 !important;
        }

        /* Red checked boxes in view mode for visibility */
        .view-mode .form-check-input:checked:disabled {
            background-color: #dc3545 !important;
            border-color: #dc3545 !important;
        }

        /* ============================================== */
        /* HIGHLIGHTED STYLING FOR SELECTED/FILLED FIELDS */
        /* ============================================== */
        
        /* Selected checkbox option - subtle highlight wrapper */
        .view-mode .option-selected {
            background-color: rgba(220, 53, 69, 0.08);
            border-radius: 4px;
            padding: 0.15rem 0.4rem;
            margin: -0.15rem 0;
        }

        /* Filled text/number fields - same red highlight as options */
        .view-mode .text-field-filled {
            background-color: rgba(220, 53, 69, 0.08);
            border-radius: 4px;
            padding: 0.15rem 0.4rem;
            margin: -0.15rem 0;
        }

        /* Selected checkbox labels - bold, dark, prominent */
        .view-mode .label-selected {
            font-weight: 700 !important;
            color: #1a1a1a !important;
            font-size: 0.95rem !important;
        }

        /* Filled text/number inputs - bold, slightly larger */
        .view-mode .input-has-value:disabled {
            font-weight: 600 !important;
            font-size: 1rem !important;
            color: #1a1a1a !important;
            -webkit-text-fill-color: #1a1a1a !important;
        }

        /* Unselected checkbox labels remain subdued for contrast */
        .view-mode .form-check-label:not(.label-selected) {
            color: #999 !important;
            font-weight: 400 !important;
        }
    </style>
    <div id="edit-view" class="mb-4 {{ $workOrder->editing == 'CLOSED' ? 'view-mode' : '' }}">
        @php
            $sortedItems = $workOrder->specification->items->sortBy('sort_order');
            $grouped = $sortedItems->groupBy(fn($item) => $item->group_name ?? '');
            $ungrouped = $grouped->get('', collect());
            $grouped = $grouped->forget('');
        @endphp

        <!-- Header (Original Style) -->
        <div class="row mb-3">
            <div class="col-md-12">
                <table style="width: 100%">
                    <tr>
                        <td>
                            <img src="{{ asset('img/logo/logo.gif') }}" width="120">
                        </td>
                        <td style="text-align: right">
                            <strong>Job #:</strong>{{ $workOrder->work_number ?: $workOrder->id }}<br>
                            <strong>Bill #:</strong>{{ !$workOrder->bill ? 'None' : ($workOrder->bill->invoice_number ?? $workOrder->bill->id) }}
                            |
                            <strong>Created At:</strong>{{ $workOrder->created_at->format('d F Y') }}
                            <strong>Created By:</strong>{{ $workOrder->user->name ?? 'N/A' }}
                        </td>
                    </tr>
                </table>
            </div>
        </div>
        <hr>

        <!-- Job Basic Info (Row-level borders) -->
        <!-- Row 1: Customer Name -->
        <div class="row" style="border: 1px solid #e0e0e0; border-radius: 4px; padding: 0.5rem; margin-bottom: 0.75rem; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
            <div class="col-12 d-flex align-items-center">
                <span class="me-2" style="min-width: 120px; font-weight: 600; font-size: 0.9rem; color: #333;">Customer Name:</span>
                <span style="font-size: 0.9rem;">{{ $workOrder->customer->name ?? 'N/A' }}</span>
                @if($workOrder->editing == 'OPEN')
                    <a href="#" class="text-primary ms-2" data-coreui-toggle="modal" data-coreui-target="#modal-customer-edit">
                        <i class="fa fa-edit"></i>
                    </a>
                @endif
            </div>
        </div>
        
        <!-- Row 2: Job Description -->
        <div class="row" style="border: 1px solid #e0e0e0; border-radius: 4px; padding: 0.5rem; margin-bottom: 0.75rem; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
            <div class="col-12 d-flex align-items-center">
                <span class="me-2" style="min-width: 120px; font-weight: 600; font-size: 0.9rem; color: #333;">Job Description:</span>
                <input type="text" class="flex-grow-1" wire:model.live="title"
                       style="border: none; border-bottom: 1px solid #333; border-radius: 0; padding: 0 0.25rem; font-size: 0.9rem; background: transparent;"
                       @if($title && $workOrder->editing == 'CLOSED') disabled @endif>
            </div>
        </div>
        
        <!-- Row 3: Ordered Qty, No. of Pages -->
        <div class="row" style="border: 1px solid #e0e0e0; border-radius: 4px; padding: 0.5rem; margin-bottom: 0.75rem; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
            <div class="col-6 d-flex align-items-center">
                <span class="me-2" style="min-width: 90px; font-weight: 600; font-size: 0.9rem; color: #333;">Ordered Qty:</span>
                <input type="text" class="flex-grow-1" wire:model.live="ordered_qty"
                       style="border: none; border-bottom: 1px solid #333; border-radius: 0; padding: 0 0.25rem; font-size: 0.9rem; background: transparent;"
                       @if($ordered_qty > 1 && $workOrder->editing == 'CLOSED') disabled @endif>
            </div>
            <div class="col-6 d-flex align-items-center">
                <span class="me-2" style="min-width: 90px; font-weight: 600; font-size: 0.9rem; color: #333;">No. of Pages:</span>
                <input type="text" class="flex-grow-1" wire:model.live="no_pages"
                       style="border: none; border-bottom: 1px solid #333; border-radius: 0; padding: 0 0.25rem; font-size: 0.9rem; background: transparent;"
                       @if($no_pages && $workOrder->editing == 'CLOSED') disabled @endif>
            </div>
        </div>

        <!-- Ungrouped Fields (Direct, no card wrapper) -->
        @if($ungrouped->count() > 0)
        <div class="row" style="border: 1px solid #e0e0e0; border-radius: 4px; padding: 0.5rem; margin-bottom: 0.75rem; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
            @foreach($ungrouped as $item)
                <div class="col-{{ $item->columns }}">
                    @include('livewire.partials.modern-field', ['item' => $item, 'payload' => $payload, 'workOrder' => $workOrder])
                </div>
            @endforeach
        </div>
        @endif

        <!-- Grouped Fields - Slim Collapsible Dividers -->
        @foreach($grouped as $groupName => $items)
            @php
                $isExpanded = $this->isGroupExpanded($groupName);
                $filledCount = $this->getFilledFieldCount($groupName);
                $totalCount = $items->count();
            @endphp
            
            <!-- Group Header (Slim Divider Style) -->
            <div class="d-flex justify-content-between align-items-center mt-3 mb-2 pb-1" 
                 style="border-bottom: 2px solid #333; cursor: pointer;"
                 wire:click="toggleGroup('{{ $groupName }}')">
                <div>
                    <i class="fa fa-{{ $isExpanded ? 'chevron-down' : 'chevron-right' }} me-2" style="font-size: 0.8rem;"></i>
                    <strong style="font-size: 0.9rem; text-transform: uppercase;">{{ $groupName }}</strong>
                </div>
                <x-badge :color="$filledCount > 0 ? 'success' : 'secondary'" style="font-size: 0.7rem;">
                    {{ $filledCount }}/{{ $totalCount }}
                </x-badge>
            </div>
            
            @if($isExpanded)
            <div class="row" style="border: 1px solid #e0e0e0; border-radius: 4px; padding: 0.5rem; margin-bottom: 0.75rem; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                @foreach($items as $item)
                    <div class="col-{{ $item->columns }}">
                        @include('livewire.partials.modern-field', ['item' => $item, 'payload' => $payload, 'workOrder' => $workOrder])
                    </div>
                @endforeach
            </div>
            @endif
        @endforeach
    </div>

    <!-- ============================================== -->
    <!-- PRINT VIEW (Hidden, for PDF Export)            -->
    <!-- ============================================== -->
    @include('livewire.partials.work-data-print')
    <!-- Modal Add Materials -->
    <div class="modal fade" wire:ignore.self id="modal-material" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog  modal-lg" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">Job Materials</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="col">
                            <div class="row d-flex justify-content-between">
                                <div class="col-4">
                                    <label for="warehouse">Warehouse</label>
                                    <select class="form-control" wire:model.live="warehouse_id" id="warehouse">
                                        <option>Choose a warehouse..</option>
                                        @forelse($warehouses as $warehouse)
                                            <option value="{{$warehouse->id}}">{{$warehouse->name}}</option>
                                        @empty
                                        @endforelse
                                    </select>
                                    @error('warehouse_id')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                                <div class="col-6 float-end mb-2">
                                    @livewire('product-search-component', ['Material'])
                                </div>
                            </div>
                            <table class="table table-bordered table-sm bg-light">
                                <tr>
                                    <th>Item</th>
                                    <th>Qty</th>
                                    <th>Unit</th>
                                </tr>

                                <tbody>
                                @forelse($work_items as $item)
                                    <tr>
                                        <td>{{$item['name']}}</td>
                                        <td>
                                            <input type="number" step="0.01" class="form-control form-control-sm boxed"
                                                   wire:model.live="work_items.{{$item['id']}}.quantity"
                                                   wire:keyup="checkBalance({{$item['id']}})">
                                        </td>

                                        <td>

                                            <input type="text" disabled class="form-control form-control-sm"
                                                   wire:model.live="work_items.{{$item['id']}}.unit">
                                        </td>

                                        <td>
                                            <a href="#" class="btn btn-outline-danger text-dark btn-sm"
                                               wire:click="itemDelete({{$item['id']}})"
                                            ><i class="fa fa-trash"></i> </a>
                                        </td>

                                    </tr>
                                @empty
                                @endforelse
                                </tbody>
                            </table>

                            <textarea wire:model.live="description" class="form-control mb-4"
                                      placeholder="Job Description"></textarea>

                            <a href="#" class="btn btn-primary float-end" wire:click="store">Save</a>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!-- End of Modal Content -->

    <!-- Modal Customer Edit -->
    <div class="modal fade" wire:ignore.self id="modal-customer-edit" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog  modal-lg" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">Change Customer</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="mb-3">
                        @livewire('customer-search-component', key('customer-search-work-order'))
                    </div>

                    <a class="btn btn-primary mt-2 float-end" wire:click="changeCustomer">Change</a>
                </div>
            </div>
        </div>
    </div>
    <!-- End of Modal Content -->

    <!-- PDF Preview Modal -->
    <div class="modal fade" id="modal-pdf-preview" tabindex="-1" role="dialog" aria-labelledby="modal-pdf-preview-label" aria-hidden="true">
        <div class="modal-dialog modal-xl modal-dialog-scrollable" role="document">
            <div class="modal-content">
                <div class="modal-header bg-info text-white">
                    <h5 class="modal-title" id="modal-pdf-preview-label">
                        <i class="fa fa-file-pdf me-2"></i>PDF Preview - Job #{{ $workOrder->work_number ?? $workOrder->id }}
                    </h5>
                    <button type="button" class="btn-close btn-close-white" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body p-0">
                    <div class="alert alert-info m-3 mb-0">
                        <i class="fa fa-info-circle me-1"></i>
                        This is how the PDF will look when exported. Click "Download PDF" to proceed.
                    </div>
                    <div id="pdf-preview-container" class="p-3" style="max-height: 60vh; overflow-y: auto; background: #f8f9fa;">
                        <!-- Print content will be cloned here -->
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-coreui-dismiss="modal">Cancel</button>
                    <button type="button" class="btn btn-info" onclick="DownloadPDF()">
                        <i class="fa fa-download me-1"></i>Download PDF
                    </button>
                </div>
            </div>
        </div>
    </div>

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
                
                // Temporarily show printer to clone it
                printer.style.display = 'block';
                previewContainer.innerHTML = printer.outerHTML;
                previewContainer.querySelector('#printer').style.display = 'block';
                printer.style.display = 'none';
            });

            function DownloadPDF() {
                let printer = document.getElementById('printer');
                
                // Show printer for PDF generation
                printer.style.display = 'block';
                
                let element = printer.cloneNode(true);
                let checkboxes = element.getElementsByClassName('input-check');
                
                // Convert checked checkboxes to visual indicators
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
                    // Hide printer again and close modal
                    printer.style.display = 'none';
                    var modal = coreui.Modal.getInstance(document.getElementById('modal-pdf-preview'));
                    if (modal) modal.hide();
                });
            }

            // Legacy function for backwards compatibility
            function PrintJob() {
                var modal = new coreui.Modal(document.getElementById('modal-pdf-preview'));
                modal.show();
            }

        </script>
    @endpush
</div>

