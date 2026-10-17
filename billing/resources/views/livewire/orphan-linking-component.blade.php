<div>
    <div class="row align-items-center mb-3">
        <div class="col">
            <h3 class="mb-0">
                <i class="fa fa-link text-primary"></i> Orphan Linking Tool
                <small class="text-muted fs-6">(Temporary)</small>
            </h3>
            <small class="text-muted">Link orphaned jobs to invoices for data cleanup</small>
        </div>
        <div class="col text-end">
            <x-back-link :fallback="route('home')" class="btn btn-secondary btn-sm">
                <i class="fa fa-arrow-left"></i> Back
            </x-back-link>
        </div>
    </div>

    {{-- Actions & Status --}}
    <div class="card border-0 shadow-sm mb-3">
        <div class="card-body py-3">
            {{-- Selection Status Row --}}
            <div class="row mb-3">
                <div class="col-12">
                    @if($selectedJob && $selectedBill)
                        <div class="d-flex align-items-center justify-content-center flex-wrap">
                            <x-badge color="warning" class="me-2 p-2" style="font-size: 0.9rem;">
                                <i class="fa fa-briefcase"></i> Job #{{ $selectedJob->work_number ?? $selectedJob->id }}
                            </x-badge>
                            <i class="fa fa-arrow-right text-primary mx-2 fs-5"></i>
                            <x-badge color="info" class="me-2 p-2" style="font-size: 0.9rem;">
                                <i class="fa fa-file-invoice"></i> {{ $selectedBill->invoice_number ?? 'INV-' . $selectedBill->id }}
                            </x-badge>
                            @if($selectedJob->customer_id === $selectedBill->customer_id)
                                <span class="text-success fw-bold">
                                    <i class="fa fa-check-circle"></i> Ready to link!
                                </span>
                            @else
                                <span class="text-warning fw-bold">
                                    <i class="fa fa-exclamation-triangle"></i> Different customers
                                </span>
                            @endif
                        </div>
                        @if($selectedJob->customer_id !== $selectedBill->customer_id || ($selectedBill->customer_order_id && $selectedJob->customer_order_id !== $selectedBill->customer_order_id))
                            <div class="text-center mt-2">
                                <small class="text-warning">
                                    <i class="fa fa-info-circle"></i>
                                    @if($selectedJob->customer_id !== $selectedBill->customer_id)
                                        Job belongs to <strong>{{ $selectedJob->customer->name ?? 'Unknown' }}</strong>,
                                        Invoice belongs to <strong>{{ $selectedBill->customer->name ?? 'Unknown' }}</strong>.
                                    @endif
                                    @if($selectedBill->customer_order_id && $selectedJob->customer_order_id !== $selectedBill->customer_order_id)
                                        Job will be moved to the invoice's customer order.
                                    @endif
                                </small>
                            </div>
                        @endif
                    @elseif($selectedJob)
                        <div class="text-center text-warning">
                            <i class="fa fa-hand-pointer"></i> <strong>Job #{{ $selectedJob->work_number ?? $selectedJob->id }}</strong> selected — Now click an invoice or create a new one
                        </div>
                    @elseif($selectedBill)
                        <div class="text-center text-info">
                            <i class="fa fa-hand-pointer"></i> <strong>Invoice {{ $selectedBill->invoice_number ?? 'INV-' . $selectedBill->id }}</strong> selected — Now click a job
                        </div>
                    @else
                        <div class="text-center text-muted">
                            <i class="fa fa-info-circle"></i> Click a job and an invoice to link them, or create a new invoice
                        </div>
                    @endif
                </div>
            </div>

            {{-- Actions Row --}}
            <div class="row">
                <div class="col-12 d-flex justify-content-center flex-wrap gap-2">
                    @if($selectedJobId && $selectedBillId)
                        <button wire:click="linkSelected" class="btn btn-primary">
                            <i class="fa fa-link"></i> Link Job to Invoice
                        </button>
                    @endif
                    <button wire:click="openCreateInvoice({{ $selectedJobId ?? 'null' }})" 
                            class="btn btn-success"
                            @if(!$selectedJobId) disabled title="Select a job first" @endif>
                        <i class="fa fa-plus-circle"></i> Create New Invoice
                    </button>
                    @if($lastAction)
                        <button wire:click="undoLast" class="btn btn-outline-danger">
                            <i class="fa fa-undo"></i> Undo Last
                        </button>
                    @endif
                </div>
            </div>

            {{-- Stats Row --}}
            <div class="row mt-3">
                <div class="col-12 text-center small text-muted">
                    @if($lastAction)
                        <span class="text-success me-3">
                            <i class="fa fa-check"></i> Last: Job #{{ $lastAction['job_id'] }} → {{ $lastAction['bill_number'] }}
                        </span>
                    @endif
                    <span>{{ $linkedCount }} linked this session</span>
                    <span class="mx-2">|</span>
                    <span>{{ $totalOrphanedJobs }} jobs</span>
                    <span class="mx-2">|</span>
                    <span>{{ $totalOrphanedBills }} invoices</span>
                </div>
            </div>
        </div>
    </div>

    {{-- Main Two-Panel Layout --}}
    <div class="row gy-3">
        {{-- Left Panel: Orphaned Jobs --}}
        <div class="col-md-6">
            <div class="card border-0 shadow-sm">
                <div class="card-header bg-warning text-dark">
                    <div class="d-flex justify-content-between align-items-center">
                        <strong><i class="fa fa-briefcase"></i> {{ $jobPanelTitle }}</strong>
                        <x-badge color="secondary">{{ $jobPanelTotalCount }} total</x-badge>
                    </div>
                </div>
                <div class="card-body p-2">
                    <div class="input-group input-group-sm mb-2">
                        <input type="text"
                               wire:model.live="jobSearch"
                               wire:keydown.enter="searchJobs"
                               class="form-control"
                               placeholder="ID, job #, title, or customer...">
                        <button wire:click="searchJobs" class="btn btn-primary" type="button">
                            <i class="fa fa-search"></i>
                        </button>
                    </div>

                    <div class="form-check mb-2 ms-1">
                        <input class="form-check-input" type="checkbox" wire:model.live="showAllJobs" wire:change="searchJobs" id="showAllJobsCheck">
                        <label class="form-check-label small user-select-none" for="showAllJobsCheck">
                            Search all jobs
                        </label>
                    </div>

                    <div class="row g-1 mb-2">
                        <div class="col-5">
                            <input type="date" wire:model.live="jobDateFrom" class="form-control form-control-sm">
                        </div>
                        <div class="col-5">
                            <input type="date" wire:model.live="jobDateTo" class="form-control form-control-sm">
                        </div>
                        <div class="col-2">
                            @if($jobDateFrom || $jobDateTo)
                                <button wire:click="clearJobDateFilter" class="btn btn-outline-secondary btn-sm w-100" title="Clear dates">
                                    <i class="fa fa-times"></i>
                                </button>
                            @endif
                        </div>
                    </div>
                    @if($selectedJobId)
                        <button wire:click="clearJobSelection" class="btn btn-outline-danger btn-sm w-100 mb-2">
                            <i class="fa fa-times-circle"></i> Clear Selection
                        </button>
                    @endif
                </div>
                <div class="list-group list-group-flush" style="max-height: 450px; overflow-y: auto;">
                    @forelse($orphanedJobs as $job)
                        <a href="#" 
                           wire:click.prevent="selectJob({{ $job->id }})"
                           class="list-group-item list-group-item-action py-2 
                                  {{ $selectedJobId == $job->id ? 'bg-primary text-white border-primary' : '' }}"
                           style="{{ $selectedJobId == $job->id ? 'box-shadow: inset 0 0 0 3px #0d6efd; font-weight: bold;' : '' }}">
                            <div class="d-flex justify-content-between align-items-start">
                                <div>
                                    @if($selectedJobId == $job->id)
                                        <i class="fa fa-check-circle me-1"></i>
                                    @endif
                                    <strong>#{{ $job->work_number ?? $job->id }}</strong>
                                    <span class="text-truncate d-inline-block" style="max-width: 150px;">
                                        {{ $job->title }}
                                    </span>
                                </div>
                                <div class="d-flex align-items-center gap-2">
                                    <small class="{{ $selectedJobId == $job->id ? 'text-white' : 'text-muted' }}">
                                        {{ $job->created_at->format('d M y') }}
                                    </small>
                                    <button wire:click.stop="viewJob({{ $job->id }})" 
                                            class="btn btn-sm btn-outline-{{ $selectedJobId == $job->id ? 'light' : 'primary' }} py-0 px-2"
                                            title="View Details">
                                        <i class="fa fa-eye"></i>
                                    </button>
                                </div>
                            </div>
                            <small class="{{ $selectedJobId == $job->id ? 'text-white-50' : 'text-muted' }}">
                                <i class="fa fa-user"></i> {{ $job->customer->name ?? 'N/A' }}
                            </small>
                            @if($job->messages->first())
                                <div class="mt-1">
                                    <small class="{{ $selectedJobId == $job->id ? 'text-white-50' : 'text-secondary' }} fst-italic text-truncate d-block" style="max-width: 280px;">
                                        <i class="fa fa-comment"></i> {{ Str::limit($job->messages->first()->message, 50) }}
                                    </small>
                                </div>
                            @endif
                        </a>
                    @empty
                        <div class="list-group-item text-center text-muted py-4">
                            <i class="fa fa-check-circle text-success fs-3"></i>
                            <p class="mb-0 mt-2">No orphaned jobs found!</p>
                        </div>
                    @endforelse
                </div>
                <div class="card-footer bg-light py-1 text-center small text-muted">
                    Showing {{ $orphanedJobs->count() }} of {{ $jobPanelTotalCount }}
                </div>
            </div>
        </div>

        {{-- Right Panel: Bills --}}
        <div class="col-md-6">
            <div class="card border-0 shadow-sm">
                <div class="card-header bg-info text-white">
                    <div class="d-flex justify-content-between align-items-center">
                        <strong><i class="fa fa-file-invoice"></i> {{ $billPanelTitle }}</strong>
                        <x-badge color="secondary">{{ $billPanelTotalCount }} total</x-badge>
                    </div>
                </div>
                <div class="card-body p-2">
                    <div class="input-group input-group-sm mb-2">
                        <input type="text"
                               wire:model.live="billSearch"
                               wire:keydown.enter="searchBills"
                               class="form-control"
                               placeholder="Invoice # or customer...">
                        <button wire:click="searchBills" class="btn btn-info" type="button">
                            <i class="fa fa-search"></i>
                        </button>
                    </div>

                    <div class="d-flex gap-3 mb-2 ms-1">
                        <div class="form-check">
                            <input class="form-check-input" type="checkbox" wire:model.live="showAllInvoices" wire:change="searchBills" id="showAllInvoicesCheck">
                            <label class="form-check-label small user-select-none" for="showAllInvoicesCheck">
                                Search all invoices
                            </label>
                        </div>
                        <div class="form-check">
                            <input class="form-check-input" type="checkbox" wire:model.live="showAllQuotations" wire:change="searchBills" id="showAllQuotationsCheck">
                            <label class="form-check-label small user-select-none" for="showAllQuotationsCheck">
                                Search all quotations
                            </label>
                        </div>
                    </div>

                    <div class="row g-1 mb-2">
                        <div class="col-5">
                            <input type="date" wire:model.live="billDateFrom" class="form-control form-control-sm">
                        </div>
                        <div class="col-5">
                            <input type="date" wire:model.live="billDateTo" class="form-control form-control-sm">
                        </div>
                        <div class="col-2">
                            @if($billDateFrom || $billDateTo)
                                <button wire:click="clearBillDateFilter" class="btn btn-outline-secondary btn-sm w-100" title="Clear dates">
                                    <i class="fa fa-times"></i>
                                </button>
                            @endif
                        </div>
                    </div>
                    @if($selectedBillId)
                        <button wire:click="clearBillSelection" class="btn btn-outline-danger btn-sm w-100 mb-2">
                            <i class="fa fa-times-circle"></i> Clear Selection
                        </button>
                    @endif
                </div>
                <div class="list-group list-group-flush" style="max-height: 450px; overflow-y: auto;">
                    @forelse($orphanedBills as $bill)
                        <a href="#" 
                           wire:click.prevent="selectBill({{ $bill->id }})"
                           class="list-group-item list-group-item-action py-2 
                                  {{ $selectedBillId == $bill->id ? 'bg-info text-white border-info' : '' }}"
                           style="{{ $selectedBillId == $bill->id ? 'box-shadow: inset 0 0 0 3px #0dcaf0; font-weight: bold;' : '' }}">
                            <div class="d-flex justify-content-between align-items-start">
                                <div>
                                    @if($selectedBillId == $bill->id)
                                        <i class="fa fa-check-circle me-1"></i>
                                    @endif
                                    @if($bill->type === 'Quotation')
                                        <x-badge color="secondary" class="me-1">Q</x-badge>
                                    @endif
                                    <strong>{{ $bill->invoice_number ?? ($bill->type === 'Quotation' ? 'QT-' : 'INV-') . $bill->id }}</strong>
                                    {{-- Show job count badge if bill has jobs --}}
                                    @if($bill->workOrders && $bill->workOrders->count() > 0)
                                        <x-badge color="warning" class="ms-1" title="Has {{ $bill->workOrders->count() }} linked job(s)">
                                            <i class="fa fa-briefcase"></i> {{ $bill->workOrders->count() }}
                                        </x-badge>
                                    @endif
                                </div>
                                <div class="d-flex align-items-center gap-2">
                                    <span class="{{ $selectedBillId == $bill->id ? 'text-white' : 'text-success' }} fw-bold">
                                        {{ number_format($bill->amount(), 0) }}
                                    </span>
                                    <button wire:click.stop="viewBill({{ $bill->id }})" 
                                            class="btn btn-sm btn-outline-{{ $selectedBillId == $bill->id ? 'light' : 'info' }} py-0 px-2"
                                            title="View Details">
                                        <i class="fa fa-eye"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="d-flex justify-content-between">
                                <small class="{{ $selectedBillId == $bill->id ? 'text-white-50' : 'text-muted' }}">
                                    <i class="fa fa-user"></i> {{ $bill->customer->name ?? 'N/A' }}
                                </small>
                                <small class="{{ $selectedBillId == $bill->id ? 'text-white' : 'text-muted' }}">
                                    {{ $bill->created_at->format('d M y') }}
                                </small>
                            </div>
                        </a>
                    @empty
                        <div class="list-group-item text-center text-muted py-4">
                            <i class="fa fa-check-circle text-success fs-3"></i>
                            <p class="mb-0 mt-2">No bills found matching criteria</p>
                        </div>
                    @endforelse
                </div>
                <div class="card-footer bg-light py-1 text-center small text-muted">
                    Showing {{ $orphanedBills->count() }} of {{ $billPanelTotalCount }}
                </div>
            </div>
        </div>
    </div>


    {{-- Job Details Modal --}}
    @if($viewingJob)
        <div class="modal fade show" style="display: block; background: rgba(0,0,0,0.5);" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header bg-warning text-dark">
                        <h5 class="modal-title">
                            <i class="fa fa-briefcase"></i> Job Details - #{{ $viewingJob->work_number ?? $viewingJob->id }}
                        </h5>
                        <button type="button" class="btn-close" wire:click="closeJobModal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <strong>Title:</strong>
                                <p>{{ $viewingJob->title }}</p>
                            </div>
                            <div class="col-md-6">
                                <strong>Customer:</strong>
                                <p><i class="fa fa-user"></i> {{ $viewingJob->customer->name ?? 'N/A' }}</p>
                            </div>
                        </div>
                        <div class="row mb-3">
                            <div class="col-md-4">
                                <strong>Department:</strong>
                                <p>{{ $viewingJob->department->name ?? 'N/A' }}</p>
                            </div>
                            <div class="col-md-4">
                                <strong>Status:</strong>
                                <p>{!! $viewingJob->getStatus() !!}</p>
                            </div>
                            <div class="col-md-4">
                                <strong>Created:</strong>
                                <p>{{ $viewingJob->created_at->format('d M Y, g:i A') }}</p>
                            </div>
                        </div>

                        @if($viewingJob->bill)
                            <div class="alert alert-info">
                                <strong><i class="fa fa-link"></i> Linked Invoice:</strong>
                                {{ $viewingJob->bill->invoice_number ?? 'INV-' . $viewingJob->bill->id }}
                            </div>
                        @endif

                        @if($viewingJob->items && $viewingJob->items->count() > 0)
                            <div class="mb-3">
                                <strong>Items:</strong>
                                <div class="table-responsive mt-2">
                                    <table class="table table-sm table-bordered">
                                        <thead class="table-light">
                                            <tr>
                                                <th>Description</th>
                                                <th>Quantity</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            @foreach($viewingJob->items as $item)
                                                <tr>
                                                    <td>{{ $item->description ?? 'N/A' }}</td>
                                                    <td>{{ $item->quantity ?? 'N/A' }}</td>
                                                </tr>
                                            @endforeach
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        @endif

                        @if($viewingJob->messages && $viewingJob->messages->count() > 0)
                            <div class="mb-3">
                                <strong>Recent Messages:</strong>
                                <div class="mt-2">
                                    @foreach($viewingJob->messages as $message)
                                        <div class="border-start border-3 border-primary ps-3 mb-2">
                                            <small class="text-muted">{{ $message->created_at->format('d M Y, g:i A') }}</small>
                                            <p class="mb-0">{{ $message->message }}</p>
                                        </div>
                                    @endforeach
                                </div>
                            </div>
                        @endif
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" wire:click="closeJobModal">Close</button>
                    </div>
                </div>
            </div>
        </div>
    @endif

    {{-- Invoice Details Modal --}}
    @if($viewingBill)
        <div class="modal fade show" style="display: block; background: rgba(0,0,0,0.5);" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header bg-info text-white">
                        <h5 class="modal-title">
                            <i class="fa fa-file-invoice"></i> Invoice Details - {{ $viewingBill->invoice_number ?? 'INV-' . $viewingBill->id }}
                        </h5>
                        <button type="button" class="btn-close btn-close-white" wire:click="closeBillModal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <strong>Customer:</strong>
                                <p><i class="fa fa-user"></i> {{ $viewingBill->customer->name ?? 'N/A' }}</p>
                            </div>
                            <div class="col-md-6">
                                <strong>Created:</strong>
                                <p>{{ $viewingBill->created_at->format('d M Y, g:i A') }}</p>
                            </div>
                        </div>
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <strong>Total Amount:</strong>
                                <p class="text-success fw-bold fs-5">{{ number_format($viewingBill->amount(), 2) }}</p>
                            </div>
                            <div class="col-md-6">
                                <strong>Balance Status:</strong>
                                <p>{!! $viewingBill->balanceStatus() !!}</p>
                            </div>
                        </div>

                        @if($viewingBill->workOrders && $viewingBill->workOrders->count() > 0)
                            <div class="alert alert-success">
                                <strong><i class="fa fa-link"></i> Linked Jobs:</strong>
                                @foreach($viewingBill->workOrders as $workOrder)
                                    <x-badge color="warning" class="me-1">
                                        #{{ $workOrder->work_number ?? $workOrder->id }}
                                    </x-badge>
                                @endforeach
                            </div>
                        @endif

                        @if($viewingBill->billItems && $viewingBill->billItems->count() > 0)
                            <div class="mb-3">
                                <strong>Items:</strong>
                                <div class="table-responsive mt-2">
                                    <table class="table table-sm table-bordered">
                                        <thead class="table-light">
                                            <tr>
                                                <th>Description</th>
                                                <th>Quantity</th>
                                                <th>Price</th>
                                                <th>Total</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            @foreach($viewingBill->billItems as $item)
                                                <tr>
                                                    <td>{{ $item->description ?? 'N/A' }}</td>
                                                    <td>{{ $item->quantity ?? 'N/A' }}</td>
                                                    <td>{{ number_format($item->price ?? 0, 2) }}</td>
                                                    <td>{{ number_format(($item->price ?? 0) * ($item->quantity ?? 0), 2) }}</td>
                                                </tr>
                                            @endforeach
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        @endif
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" wire:click="closeBillModal">Close</button>
                    </div>
                </div>
            </div>
        </div>
    @endif

    {{-- Create Invoice Modal --}}
    @if($creatingInvoiceForJob)
        <div class="modal fade show" style="display: block; background: rgba(0,0,0,0.5);" tabindex="-1">
            <div class="modal-dialog modal-xl">
                <div class="modal-content">
                    <div class="modal-header bg-success text-white">
                        <h5 class="modal-title">
                            <i class="fa fa-plus-circle"></i> Create New Invoice
                        </h5>
                        <button type="button" class="btn-close btn-close-white" wire:click="closeCreateInvoiceModal"></button>
                    </div>
                    <div class="modal-body bg-light p-3">
                        <div class="alert alert-info py-2 mb-3">
                            <strong><i class="fa fa-info-circle"></i> Creating Invoice For:</strong>
                            Job #{{ $creatingInvoiceForJob->work_number ?? $creatingInvoiceForJob->id }} - 
                            <small>{{ $creatingInvoiceForJob->title }}</small>
                            <span class="float-end">
                                <i class="fa fa-user"></i> {{ $creatingInvoiceForJob->customer->name ?? 'N/A' }}
                            </span>
                        </div>
                        
                        @if($creatingInvoiceForJobId)
                            <livewire:orphan-invoice-editor-component 
                                :jobId="$creatingInvoiceForJobId"
                                :wire:key="'orphan-editor-' . $creatingInvoiceForJobId" 
                            />
                        @endif
                    </div>
                </div>
            </div>
        </div>
    @endif

    {{-- Keyboard Shortcut Hint --}}
    <div class="text-center mt-3 text-muted small">
        <kbd>Click Job</kbd> → <kbd>Click Invoice</kbd> → <kbd>Click Link</kbd> (or press <kbd>Enter</kbd>)
    </div>

    @push('scripts')
        <script>
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !e.target.matches('input, textarea')) {
                    @this.linkSelected();
                }
            });
        </script>
    @endpush
</div>
