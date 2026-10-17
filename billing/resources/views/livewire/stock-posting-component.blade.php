<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-md-0">
            <h2 class="h4">Monthly <strong>Stock Posting</strong></h2>
            <p class="mb-0 text-muted">Manage monthly closing stock entries</p>
        </div>

        <div class="btn-toolbar mb-md-0">
            @if(!$show_form)
                <button type="button" class="btn btn-primary btn-sm" wire:click="showCreateForm">
                    <i class="fas fa-plus"></i> New Stock Entry
                </button>
            @endif
        </div>
    </div>

    {{-- Summary Cards --}}
    <div class="row mb-4">
        <div class="col-6 col-md-4">
            <div class="card bg-primary text-white">
                <div class="card-body">
                    <div class="d-flex justify-content-between">
                        <div>
                            <h6 class="card-title">Total Entries</h6>
                            <h3 class="mb-0">{{ $total_entries }}</h3>
                        </div>
                        <div class="align-self-center">
                            <i class="fas fa-list-alt fa-2x opacity-75"></i>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="col-6 col-md-4">
            <div class="card bg-success text-white">
                <div class="card-body">
                    <div class="d-flex justify-content-between">
                        <div>
                            <h6 class="card-title">Current Stock Value</h6>
                            <h3 class="mb-0">{{ number_format($last_stock_value, 2) }}</h3>
                        </div>
                        <div class="align-self-center">
                            <i class="fas fa-boxes fa-2x opacity-75"></i>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="col-6 col-md-4">
            <div class="card bg-info text-white">
                <div class="card-body">
                    <div class="d-flex justify-content-between">
                        <div>
                            <h6 class="card-title">Opening Stock</h6>
                            <h3 class="mb-0">{{ number_format($opening_stock, 2) }}</h3>
                        </div>
                        <div class="align-self-center">
                            <i class="fas fa-warehouse fa-2x opacity-75"></i>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    {{-- Stock Entry Form --}}
    @if($show_form)
        <div class="card card-body mb-4">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h5 class="mb-0">
                    @if($editing_entry)
                        <i class="fas fa-edit text-warning"></i> Edit Stock Entry
                    @else
                        <i class="fas fa-plus text-success"></i> New Stock Entry
                    @endif
                </h5>
                <button type="button" class="btn btn-outline-secondary btn-sm" wire:click="hideForm">
                    <i class="fas fa-times"></i> Cancel
                </button>
            </div>

            @if ($errors->any())
                <div class="alert alert-danger">
                    <ul class="mb-0">
                        @foreach ($errors->all() as $error)
                            <li>{{ $error }}</li>
                        @endforeach
                    </ul>
                </div>
            @endif

            <form wire:submit="createStockEntry">
                <div class="row">
                    <div class="col-md-3">
                        <label class="form-label text-primary fw-bold">Period *</label>
                        <select class="form-control" wire:model.live="selected_period" wire:change="updatedSelectedPeriod($event.target.value)">
                            <option value="">--Select Period--</option>
                            @foreach($periods as $period)
                                <option value="{{ $period->id }}">
                                    {{ $period->calendar_year }} - Period {{ $period->period_count }}
                                    @if($period->status == 'CLOSED') (Closed) @endif
                                </option>
                            @endforeach
                        </select>
                    </div>

                    <div class="col-md-3">
                        <label class="form-label text-primary fw-bold">Currency</label>
                        <select class="form-control" wire:model.live="selected_currency">
                            @foreach($currencies as $currency)
                                <option value="{{ $currency->id }}">{{ $currency->currency_code }} - {{ $currency->name }}</option>
                            @endforeach
                        </select>
                    </div>

                    <div class="col-md-3">
                        <label class="form-label text-primary fw-bold">Post Date *</label>
                        <input type="date" class="form-control" wire:model.live="post_date">
                    </div>

                    <div class="col-md-3">
                        <label class="form-label text-primary fw-bold">Reference</label>
                        <input type="text" class="form-control" wire:model.live="reference" placeholder="Reference number">
                    </div>
                </div>

                {{-- Stock Calculation Display --}}
                @if($selected_period)
                    <div class="row mt-3">
                        <div class="col-md-12">
                            <div class="card bg-light">
                                <div class="card-body">
                                    <h6 class="card-title">Stock Calculation</h6>
                                    {{-- Desktop: 4 columns with border dividers --}}
                                    <div class="row text-center d-none d-md-flex">
                                        <div class="col-md-3">
                                            <div class="border-end">
                                                <h6 class="text-muted">Opening Stock</h6>
                                                <h4 class="text-info">{{ number_format($opening_stock, 2) }}</h4>
                                            </div>
                                        </div>
                                        <div class="col-md-3">
                                            <div class="border-end">
                                                <h6 class="text-muted">Current Stock</h6>
                                                <h4 class="text-warning">{{ number_format($current_stock_value, 2) }}</h4>
                                            </div>
                                        </div>
                                        <div class="col-md-3">
                                            <div class="border-end">
                                                <h6 class="text-muted">Adjustment</h6>
                                                <h4 class="@if($stock_adjustment >= 0) text-success @else text-danger @endif">
                                                    {{ $stock_adjustment >= 0 ? '+' : '' }}{{ number_format($stock_adjustment, 2) }}
                                                </h4>
                                            </div>
                                        </div>
                                        <div class="col-md-3">
                                            <h6 class="text-muted">Closing Stock</h6>
                                            <h4 class="text-primary">{{ number_format($closing_stock_value, 2) }}</h4>
                                        </div>
                                    </div>
                                    {{-- Mobile: 2x2 grid without border dividers --}}
                                    <div class="row text-center d-md-none">
                                        <div class="col-6 mb-2">
                                            <h6 class="text-muted">Opening Stock</h6>
                                            <h4 class="text-info">{{ number_format($opening_stock, 2) }}</h4>
                                        </div>
                                        <div class="col-6 mb-2">
                                            <h6 class="text-muted">Current Stock</h6>
                                            <h4 class="text-warning">{{ number_format($current_stock_value, 2) }}</h4>
                                        </div>
                                        <div class="col-6">
                                            <h6 class="text-muted">Adjustment</h6>
                                            <h4 class="{{ $stock_adjustment >= 0 ? 'text-success' : 'text-danger' }}">
                                                {{ $stock_adjustment >= 0 ? '+' : '' }}{{ number_format($stock_adjustment, 2) }}
                                            </h4>
                                        </div>
                                        <div class="col-6">
                                            <h6 class="text-muted">Closing Stock</h6>
                                            <h4 class="text-primary">{{ number_format($closing_stock_value, 2) }}</h4>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                @endif

                <div class="row mt-3">
                    <div class="col-md-4">
                        <label class="form-label text-primary fw-bold">Closing Stock Value *</label>
                        <input type="number" step="0.01" class="form-control"
                               wire:model.live="closing_stock_value"
                               wire:keyup="calculateAdjustment"
                               placeholder="0.00">
                    </div>

                    <div class="col-md-8">
                        <label class="form-label text-primary fw-bold">Narration *</label>
                        <input type="text" class="form-control" wire:model.live="narration"
                               placeholder="Stock adjustment narration">
                    </div>
                </div>

                <div class="row mt-3">
                    <div class="col-md-12">
                        <button type="submit" class="btn btn-success">
                            <i class="fas fa-save"></i>
                            @if($editing_entry) Update @else Create @endif Stock Entry
                        </button>
                    </div>
                </div>
            </form>
        </div>
    @endif

    {{-- Stock Entries History --}}
    <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center">
            <h5 class="mb-0">
                <i class="fas fa-history"></i> Stock Posting History
            </h5>
            <small class="text-muted">{{ $stock_entries->total() }} total entries</small>
        </div>

        <div class="card-body p-0">
            @if($stock_entries->count() > 0)
                {{-- Desktop Table --}}
                <div class="table-responsive d-none d-md-block">
                    <table class="table table-hover table-bordered mb-0">
                        <thead class="table-light">
                        <tr>
                            <th>Period</th>
                            <th>Post Date</th>
                            <th>Document No.</th>
                            <th class="text-end">Opening Stock</th>
                            <th class="text-end">Closing Stock</th>
                            <th class="text-end">Adjustment</th>
                            <th>Reference</th>
                            <th>Actions</th>
                        </tr>
                        </thead>
                        <tbody>
                        @php
                            $processed_docs = [];
                        @endphp

                        @foreach($stock_entries as $entry)
                            @if(!in_array($entry->document_no, $processed_docs))
                                @php
                                    $processed_docs[] = $entry->document_no;

                                    // Get both journal entries for this document
                                    $entries = \App\Models\Journal::where('document_no', $entry->document_no)
                                        ->where('type', 'Closing Stock')
                                        ->get();

                                    $inventory_entry = $entries->where('account_id', $inventory_account?->id)->first();
                                    $adjustment_entry = $entries->where('account_id', '!=', $inventory_account?->id)->first();

                                    $adjustment = 0;
                                    $period_display = '';

                                    if ($inventory_entry && $inventory_entry->debit > 0) {
                                        $adjustment = $inventory_entry->debit;
                                    } elseif ($inventory_entry && $inventory_entry->credit > 0) {
                                        $adjustment = -$inventory_entry->credit;
                                    }

                                    // Extract period info from post_date since period_id is hardcoded
                                    $post_date = \Carbon\Carbon::parse($entry->post_date);
                                    $period_display = $post_date->format('Y') . ' - Period ' . $post_date->format('m');

                                    // Calculate closing stock for display
                                    $closing_stock = $this->getCurrentStockValue(auth()->user()->entity_id, (object)['calendar_year' => $post_date->year, 'period_count' => $post_date->month]);
                                    $opening = $closing_stock - $adjustment;
                                @endphp

                                <tr>
                                    <td>
                                        <strong>{{ $post_date->format('Y') }}</strong>
                                        <br>
                                        <small class="text-muted">{{ $post_date->format('M') }}</small>
                                    </td>
                                    <td>{{ $post_date->format('M d, Y') }}</td>
                                    <td>
                                        <code>{{ $entry->document_no }}</code>
                                    </td>
                                    <td class="text-end">
                                        <strong>{{ number_format($opening, 2) }}</strong>
                                    </td>
                                    <td class="text-end">
                                        <strong class="text-primary">{{ number_format($closing_stock, 2) }}</strong>
                                    </td>
                                    <td class="text-end">
                                            <span class="@if($adjustment >= 0) text-success @else text-danger @endif">
                                                {{ $adjustment >= 0 ? '+' : '' }}{{ number_format($adjustment, 2) }}
                                            </span>
                                    </td>
                                    <td>
                                        @if($entry->reference)
                                            <small>{{ $entry->reference }}</small>
                                        @else
                                            <small class="text-muted">-</small>
                                        @endif
                                    </td>
                                    <td>
                                        <div class="btn-group btn-group-sm">
                                            <button type="button" class="btn btn-outline-primary btn-sm"
                                                    wire:click="editEntry({{ $entry->id }})"
                                                    title="Edit Entry">
                                                <i class="fas fa-edit"></i>
                                            </button>
                                            <button type="button" class="btn btn-outline-danger btn-sm"
                                                    data-coreui-toggle="modal" data-coreui-target="#modal-delete-stock-{{ $entry->id }}"
                                                    title="Delete Entry">
                                                <i class="fas fa-trash"></i>
                                            </button>
                                            <x-modal.confirm id="modal-delete-stock-{{ $entry->id }}"
                                                             title="Delete Stock Entry"
                                                             method="deleteEntry({{ $entry->id }})"
                                                             confirmClass="btn-danger"
                                                             confirmIcon="fa-trash"
                                                             confirmText="Delete">
                                                Are you sure you want to delete this stock entry for period <strong>{{ $post_date->format('M Y') }}</strong>?
                                            </x-modal.confirm>
                                        </div>
                                    </td>
                                </tr>
                            @endif
                        @endforeach
                        </tbody>
                    </table>
                </div>

                {{-- Mobile Cards --}}
                <div class="d-md-none">
                    @php
                        $mobile_processed_docs = [];
                    @endphp

                    @foreach($stock_entries as $entry)
                        @if(!in_array($entry->document_no, $mobile_processed_docs))
                            @php
                                $mobile_processed_docs[] = $entry->document_no;

                                // Get both journal entries for this document
                                $m_entries = \App\Models\Journal::where('document_no', $entry->document_no)
                                    ->where('type', 'Closing Stock')
                                    ->get();

                                $m_inventory_entry = $m_entries->where('account_id', $inventory_account?->id)->first();
                                $m_adjustment_entry = $m_entries->where('account_id', '!=', $inventory_account?->id)->first();

                                $m_adjustment = 0;

                                if ($m_inventory_entry && $m_inventory_entry->debit > 0) {
                                    $m_adjustment = $m_inventory_entry->debit;
                                } elseif ($m_inventory_entry && $m_inventory_entry->credit > 0) {
                                    $m_adjustment = -$m_inventory_entry->credit;
                                }

                                $m_post_date = \Carbon\Carbon::parse($entry->post_date);

                                $m_closing_stock = $this->getCurrentStockValue(auth()->user()->entity_id, (object)['calendar_year' => $m_post_date->year, 'period_count' => $m_post_date->month]);
                                $m_opening = $m_closing_stock - $m_adjustment;
                            @endphp

                            <div class="border-bottom p-3">
                                {{-- Row 1: Period + Document No --}}
                                <div class="d-flex justify-content-between align-items-center">
                                    <span class="fw-bold">{{ $m_post_date->format('Y') }} - {{ $m_post_date->format('M') }}</span>
                                    <code class="small">{{ $entry->document_no }}</code>
                                </div>
                                {{-- Row 2: Post Date --}}
                                <div class="mt-1">
                                    <small class="text-muted"><i class="fas fa-calendar me-1"></i>{{ $m_post_date->format('M d, Y') }}</small>
                                </div>
                                {{-- Row 3: Opening + Closing --}}
                                <div class="d-flex justify-content-between align-items-center border-top mt-2 pt-2">
                                    <div>
                                        <small class="text-muted d-block">Opening</small>
                                        <strong>{{ number_format($m_opening, 2) }}</strong>
                                    </div>
                                    <div class="text-end">
                                        <small class="text-muted d-block">Closing</small>
                                        <strong class="text-primary fw-bold">{{ number_format($m_closing_stock, 2) }}</strong>
                                    </div>
                                </div>
                                {{-- Row 4: Adjustment + Reference --}}
                                <div class="d-flex justify-content-between align-items-center border-top mt-2 pt-2">
                                    <div>
                                        <small class="text-muted d-block">Adjustment</small>
                                        <span class="{{ $m_adjustment >= 0 ? 'text-success' : 'text-danger' }}">
                                            {{ $m_adjustment >= 0 ? '+' : '' }}{{ number_format($m_adjustment, 2) }}
                                        </span>
                                    </div>
                                    <div class="text-end">
                                        <small class="text-muted d-block">Reference</small>
                                        <small>{{ $entry->reference ?: '-' }}</small>
                                    </div>
                                </div>
                                {{-- Row 5: Actions --}}
                                <div class="d-flex border-top mt-2 pt-2 gap-2">
                                    <button type="button" class="btn btn-outline-primary btn-sm flex-fill"
                                            wire:click="editEntry({{ $entry->id }})">
                                        <i class="fas fa-edit"></i> Edit
                                    </button>
                                    <button type="button" class="btn btn-outline-danger btn-sm flex-fill"
                                            data-coreui-toggle="modal" data-coreui-target="#modal-delete-stock-mobile-{{ $entry->id }}">
                                        <i class="fas fa-trash"></i> Delete
                                    </button>
                                    <x-modal.confirm id="modal-delete-stock-mobile-{{ $entry->id }}"
                                                     title="Delete Stock Entry"
                                                     method="deleteEntry({{ $entry->id }})"
                                                     confirmClass="btn-danger"
                                                     confirmIcon="fa-trash"
                                                     confirmText="Delete">
                                        Are you sure you want to delete this stock entry for period <strong>{{ $m_post_date->format('M Y') }}</strong>?
                                    </x-modal.confirm>
                                </div>
                            </div>
                        @endif
                    @endforeach
                </div>

                <div class="card-footer">
                    {{ $stock_entries->links() }}
                </div>
            @else
                <div class="text-center py-5">
                    <i class="fas fa-inbox fa-3x text-muted mb-3"></i>
                    <h5 class="text-muted">No Stock Entries Found</h5>
                    <p class="text-muted">Start by creating your first monthly stock entry.</p>
                    <button type="button" class="btn btn-primary" wire:click="showCreateForm">
                        <i class="fas fa-plus"></i> Create First Entry
                    </button>
                </div>
            @endif
        </div>
    </div>
</div>

@push('scripts')
    <script>
        // SweetAlert integration
        window.addEventListener('swal:success', event => {
            Swal.fire({
                title: event.detail.title,
                text: event.detail.text,
                icon: 'success',
                confirmButtonText: 'OK'
            });
        });

        window.addEventListener('swal:error', event => {
            Swal.fire({
                title: event.detail.title,
                text: event.detail.text,
                icon: 'error',
                confirmButtonText: 'OK'
            });
        });

        window.addEventListener('swal:info', event => {
            Swal.fire({
                title: event.detail.title,
                text: event.detail.text,
                icon: 'info',
                confirmButtonText: 'OK'
            });
        });
    </script>
@endpush
