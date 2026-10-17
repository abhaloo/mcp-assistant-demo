<div>
    <!-- Loading Overlays -->
    <x-loading-overlay target="downloadTemplate" message="Generating template..." />
    <x-loading-overlay target="uploadStockCheck" message="Processing stock check..." />
    <x-loading-overlay target="reconcileStock" message="Reconciling inventory..." />

    <!-- Page Header -->
    <div class="d-sm-flex align-items-center justify-content-between mb-4">
        <h1 class="h3 mb-0 text-gray-800">Physical Stock Check</h1>
    </div>

    <!-- Alert Messages -->
    @if (session()->has('message'))
        <div class="alert alert-success alert-dismissible fade show border-start border-success border-4 shadow-sm" role="alert">
            <div class="d-flex align-items-start">
                <i class="bi bi-check-circle-fill text-success me-3 fs-4"></i>
                <div class="flex-grow-1">
                    <strong class="d-block mb-1">Success!</strong>
                    <span>{{ session('message') }}</span>
                </div>
                <button type="button" class="btn-close" data-coreui-dismiss="alert" aria-label="Close"></button>
            </div>
        </div>
    @endif

    @if (session()->has('error'))
        <div class="alert alert-danger alert-dismissible fade show border-start border-danger border-4 shadow-sm" role="alert">
            <div class="d-flex align-items-start">
                <i class="bi bi-exclamation-triangle-fill text-danger me-3 fs-4"></i>
                <div class="flex-grow-1">
                    <strong class="d-block mb-1">Error!</strong>
                    <span>{{ session('error') }}</span>
                </div>
                <button type="button" class="btn-close" data-coreui-dismiss="alert" aria-label="Close"></button>
            </div>
        </div>
    @endif

    <div class="card" wire:ignore.self>
        <div class="card-header">
            <ul class="nav nav-tabs card-header-tabs" role="tablist" wire:ignore>
                <li class="nav-item">
                    <a class="nav-link {{ $activeTab === 'download' ? 'active' : '' }}"
                       id="download-tab"
                       data-coreui-toggle="tab"
                       href="#download"
                       role="tab"
                       wire:click="$set('activeTab', 'download')">
                        <i class="bi bi-download me-1"></i>Download Template
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link {{ $activeTab === 'upload' ? 'active' : '' }}"
                       id="upload-tab"
                       data-coreui-toggle="tab"
                       href="#upload"
                       role="tab"
                       wire:click="$set('activeTab', 'upload')">
                        <i class="bi bi-upload me-1"></i>Upload Check
                    </a>
                </li>
                @if($reportData)
                    <li class="nav-item">
                        <a class="nav-link {{ $activeTab === 'results' ? 'active' : '' }}"
                           id="results-tab"
                           data-coreui-toggle="tab"
                           href="#results"
                           role="tab"
                           wire:click="$set('activeTab', 'results')">
                            <i class="bi bi-list-check me-1"></i>Results
                        </a>
                    </li>
                @endif
                @can('view stock check history')
                    <li class="nav-item">
                        <a class="nav-link {{ $activeTab === 'history' ? 'active' : '' }}"
                           id="history-tab"
                           data-coreui-toggle="tab"
                           href="#history"
                           role="tab"
                           wire:click="showHistory">
                            <i class="bi bi-clock-history me-1"></i>History
                        </a>
                    </li>
                @endcan
            </ul>
        </div>

        <div class="card-body">
            <div class="tab-content">
                <!-- Download Template Tab -->
                <div class="tab-pane fade {{ $activeTab === 'download' ? 'show active' : '' }}"
                     id="download"
                     role="tabpanel"
                     wire:ignore.self>
                    <div class="p-3">
                        <h5 class="card-title">Download Stock Check Template</h5>
                        <p class="card-text text-muted">
                            Download the Excel template containing current stock levels. Fill in the physical count for each item and upload it back.
                        </p>

                        <div class="mt-4">
                            <button wire:click="downloadTemplate" class="btn btn-primary">
                                <i class="bi bi-download me-2"></i>Download Template
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Upload Check Tab -->
                <div class="tab-pane fade {{ $activeTab === 'upload' ? 'show active' : '' }}"
                     id="upload"
                     role="tabpanel"
                     wire:ignore.self>
                    <div class="p-3">
                        <h5 class="card-title">Upload Completed Stock Check</h5>

                        <form wire:submit="uploadStockCheck" class="mt-4">
                            <div class="row g-3">
                                <!-- Check Date -->
                                <div class="col-md-4">
                                    <label for="checkDate" class="form-label">Check Date</label>
                                    <input type="date"
                                           class="form-control @error('checkDate') is-invalid @enderror"
                                           id="checkDate"
                                           wire:model.live="checkDate">
                                    @error('checkDate')
                                    <div class="invalid-feedback">{{ $message }}</div>
                                    @enderror
                                </div>

                                <!-- Warehouse -->
                                <div class="col-md-4">
                                    <label for="warehouseId" class="form-label">Warehouse</label>
                                    <select class="form-select @error('warehouseId') is-invalid @enderror"
                                            id="warehouseId"
                                            wire:model.live="warehouseId">
                                        @foreach($warehouses as $warehouse)
                                            <option value="{{ $warehouse->id }}">{{ $warehouse->name }}</option>
                                        @endforeach
                                    </select>
                                    @error('warehouseId')
                                    <div class="invalid-feedback">{{ $message }}</div>
                                    @enderror
                                </div>

                                <!-- Adjustment Reason -->
                                <div class="col-md-4">
                                    <label for="adjustmentReason" class="form-label">Adjustment Reason</label>
                                    <input type="text"
                                           class="form-control @error('adjustmentReason') is-invalid @enderror"
                                           id="adjustmentReason"
                                           wire:model.live="adjustmentReason"
                                           placeholder="e.g., Monthly physical count">
                                    @error('adjustmentReason')
                                    <div class="invalid-feedback">{{ $message }}</div>
                                    @enderror
                                </div>

                                <!-- File Upload -->
                                <div class="col-12">
                                    <label for="uploadedFile" class="form-label">Stock Check File</label>
                                    <input type="file"
                                           class="form-control @error('uploadedFile') is-invalid @enderror"
                                           id="uploadedFile"
                                           wire:model.live="uploadedFile"
                                           accept=".xlsx,.xls">
                                    @error('uploadedFile')
                                    <div class="invalid-feedback">{{ $message }}</div>
                                    @enderror
                                </div>

                                <!-- Submit Button -->
                                <div class="col-12">
                                    <button type="submit" class="btn btn-primary" wire:loading.attr="disabled">
                                        <span wire:loading.remove wire:target="uploadStockCheck">
                                            <i class="bi bi-upload me-2"></i>Process Stock Check
                                        </span>
                                        <span wire:loading wire:target="uploadStockCheck">
                                            <span class="spinner-border spinner-border-sm me-2" role="status"></span>
                                            Processing...
                                        </span>
                                    </button>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>

                <!-- Results Tab -->
                @if($reportData)
                    <div class="tab-pane fade {{ $activeTab === 'results' ? 'show active' : '' }}"
                         id="results"
                         role="tabpanel"
                         wire:ignore.self>
                        <div class="p-3">
                            <div class="d-flex justify-content-between align-items-center mb-4">
                                <h5 class="card-title mb-0">Stock Check Results</h5>
                                <div class="d-flex gap-2">
                                    @if(count($reportData['variances'] ?? []) > 0)
                                        <button 
                                            data-coreui-toggle="modal" 
                                            data-coreui-target="#modal-reconcile-confirm"
                                            class="btn btn-success">
                                            <i class="bi bi-check-circle me-2"></i>Reconcile Stock
                                        </button>
                                    @endif
                                    <button wire:click="downloadVarianceReport" class="btn btn-outline-primary">
                                        <i class="bi bi-download me-2"></i>Download Report
                                    </button>
                                </div>
                            </div>

                            <!-- Summary Cards -->
                            <div class="row g-3 mb-4">
                                <div class="col-6 col-md-3">
                                    <div class="card h-100">
                                        <div class="card-body">
                                            <h6 class="card-subtitle mb-2 text-muted">Check Date</h6>
                                            <h5 class="card-title mb-0">{{ $reportData['check_date'] }}</h5>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-6 col-md-3">
                                    <div class="card h-100">
                                        <div class="card-body">
                                            <h6 class="card-subtitle mb-2 text-muted">Reference</h6>
                                            <h5 class="card-title mb-0">{{ $reportData['reference'] }}</h5>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-6 col-md-3">
                                    <div class="card h-100">
                                        <div class="card-body">
                                            <h6 class="card-subtitle mb-2 text-muted">Items with Variance</h6>
                                            <h5 class="card-title mb-0">{{ $reportData['total_items'] }}</h5>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-6 col-md-3">
                                    <div class="card h-100">
                                        <div class="card-body">
                                            <h6 class="card-subtitle mb-2 text-muted">Total Variance Value</h6>
                                            <h5 class="card-title mb-0">{{ number_format($reportData['total_variance_value'], 2) }}</h5>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <!-- Variances Table (Desktop) -->
                            <div class="table-responsive d-none d-md-block">
                                <table class="table table-striped table-hover">
                                    <thead>
                                    <tr>
                                        <th>Product</th>
                                        <th>SKU</th>
                                        <th>Unit</th>
                                        <th class="text-end">System Count</th>
                                        <th class="text-end">Physical Count</th>
                                        <th class="text-end">Difference</th>
                                        <th class="text-end">Cost</th>
                                        <th class="text-end">Value</th>
                                    </tr>
                                    </thead>
                                    <tbody>
                                    @foreach($reportData['variances'] as $variance)
                                        <tr>
                                            <td>{{ $variance['product_name'] }}</td>
                                            <td>{{ $variance['sku'] }}</td>
                                            <td>{{ $variance['unit'] }}</td>
                                            <td class="text-end">{{ number_format($variance['system_count'], 2) }}</td>
                                            <td class="text-end">{{ number_format($variance['physical_count'], 2) }}</td>
                                            <td class="text-end {{ $variance['difference'] < 0 ? 'text-danger' : 'text-success' }}">
                                                {{ number_format($variance['difference'], 2) }}
                                            </td>
                                            <td class="text-end">{{ number_format($variance['cost'], 2) }}</td>
                                            <td class="text-end">{{ number_format($variance['value'], 2) }}</td>
                                        </tr>
                                    @endforeach
                                    </tbody>
                                    <tfoot>
                                    <tr class="fw-bold">
                                        <td colspan="7" class="text-end">Total Variance Value:</td>
                                        <td class="text-end">{{ number_format($reportData['total_variance_value'], 2) }}</td>
                                    </tr>
                                    </tfoot>
                                </table>
                            </div>

                            <!-- Variances Cards (Mobile) -->
                            <div class="d-md-none">
                                @forelse($reportData['variances'] as $variance)
                                    <div class="card mb-2">
                                        <div class="card-body p-3">
                                            <div class="d-flex justify-content-between align-items-start">
                                                <span class="fw-bold">{{ $variance['product_name'] }}</span>
                                                <span class="{{ $variance['difference'] < 0 ? 'text-danger' : 'text-success' }} fw-bold">{{ number_format($variance['difference'], 2) }}</span>
                                            </div>
                                            <div class="small text-muted mt-1">
                                                <i class="fa fa-barcode me-1"></i>{{ $variance['sku'] }}
                                                <span class="ms-3"><i class="fa fa-ruler me-1"></i>{{ $variance['unit'] }}</span>
                                            </div>
                                            <div class="d-flex justify-content-between small mt-2 pt-2 border-top">
                                                <span>System: {{ number_format($variance['system_count'], 2) }}</span>
                                                <span>Physical: {{ number_format($variance['physical_count'], 2) }}</span>
                                            </div>
                                            <div class="d-flex justify-content-between mt-2 pt-2 border-top">
                                                <span class="small text-muted">Cost: {{ number_format($variance['cost'], 2) }}</span>
                                                <span class="fw-bold">{{ number_format($variance['value'], 2) }}</span>
                                            </div>
                                        </div>
                                    </div>
                                @empty
                                    <div class="card border-0 shadow-sm mb-2">
                                        <div class="card-body text-center py-4 text-muted">No variances found.</div>
                                    </div>
                                @endforelse
                                <div class="d-flex justify-content-between p-2 bg-light rounded mt-2">
                                    <span class="fw-bold">Total Variance Value</span>
                                    <span class="fw-bold">{{ number_format($reportData['total_variance_value'], 2) }}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                @endif

                @can('view stock check history')
                    <!-- History Tab -->
                    <div class="tab-pane fade {{ $activeTab === 'history' ? 'show active' : '' }}"
                         id="history" role="tabpanel" wire:ignore.self>
                        <div class="p-3">
                            <h5 class="card-title mb-4">Stock Check History</h5>
                            
                            @forelse($stockCheckHistory as $entry)
                                <div class="card mb-3">
                                    <div class="card-header d-flex justify-content-between align-items-center"
                                         data-coreui-toggle="collapse"
                                         data-coreui-target="#history-{{ $loop->index }}"
                                         style="cursor: pointer">
                                        <div>
                                            <strong>{{ $entry['reference'] }}</strong>
                                            <span class="text-muted ms-3">{{ $entry['date'] }}</span>
                                        </div>
                                        <div class="text-end">
                                            <x-badge color="primary">{{ count($entry['items']) }} items</x-badge>
                                            <span class="text-muted ms-2">by {{ $entry['user'] }}</span>
                                        </div>
                                    </div>
                                    <div class="collapse" id="history-{{ $loop->index }}">
                                        <div class="card-body">
                                            <p><strong>Warehouse:</strong> {{ $entry['warehouse'] }}</p>
                                            <p><strong>Reason:</strong> {{ $entry['reason'] }}</p>
                                            <div class="table-responsive">
                                                <table class="table table-sm table-striped">
                                                    <thead>
                                                        <tr>
                                                            <th>Product</th>
                                                            <th>SKU</th>
                                                            <th class="text-end">Adjustment</th>
                                                            <th class="text-end">Cost</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        @foreach($entry['items'] as $item)
                                                            <tr>
                                                                <td>{{ $item['product'] }}</td>
                                                                <td>{{ $item['sku'] }}</td>
                                                                <td class="text-end {{ $item['quantity'] > 0 ? 'text-success' : 'text-danger' }}">
                                                                    {{ $item['quantity'] > 0 ? '+' : '' }}{{ $item['quantity'] }}
                                                                </td>
                                                                <td class="text-end">{{ number_format($item['cost'], 2) }}</td>
                                                            </tr>
                                                        @endforeach
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            @empty
                                <div class="text-center text-muted py-5">
                                    <i class="bi bi-clock-history fs-1"></i>
                                    <p class="mt-3">No stock check history found.</p>
                                </div>
                            @endforelse
                        </div>
                    </div>
                @endcan
            </div>
        </div>
    </div>

    <!-- Reconcile Confirmation Modal -->
    <div class="modal fade" wire:ignore.self id="modal-reconcile-confirm" tabindex="-1" role="dialog"
         aria-labelledby="modal-reconcile-confirm-label" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header bg-success text-white">
                    <h5 class="modal-title" id="modal-reconcile-confirm-label">
                        <i class="bi bi-check-circle me-2"></i>Confirm Stock Reconciliation
                    </h5>
                    <button type="button" class="btn-close btn-close-white" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="alert alert-info mb-3">
                        <i class="bi bi-info-circle me-2"></i>
                        <strong>What is Stock Reconciliation?</strong>
                    </div>
                    <p class="mb-3">
                        Stock reconciliation adjusts your system inventory to match the physical count from your stock check.
                        This will create <strong>{{ count($reportData['variances'] ?? []) }} inventory adjustment(s)</strong> 
                        for items with differences between system and physical counts.
                    </p>
                    <div class="bg-light p-3 rounded mb-3">
                        <h6 class="fw-bold mb-2">What will happen:</h6>
                        <ul class="mb-0">
                            <li>System stock levels will be updated to match physical counts</li>
                            <li>Adjustments logged with reference: <code>{{ $reportData['reference'] ?? '' }}</code></li>
                            <li>Changes will be visible on the inventory page</li>
                            <li>This action cannot be undone automatically</li>
                        </ul>
                    </div>
                    <p class="text-danger fw-bold mb-0">
                        <i class="bi bi-exclamation-triangle me-2"></i>
                        Are you sure you want to proceed with reconciliation?
                    </p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-coreui-dismiss="modal">
                        Cancel
                    </button>
                    <button 
                        type="button" 
                        wire:click="reconcileStock" 
                        class="btn btn-success"
                        data-coreui-dismiss="modal"
                        wire:loading.attr="disabled">
                        <span wire:loading.remove wire:target="reconcileStock">
                            <i class="bi bi-check-circle me-2"></i>Yes, Reconcile Stock
                        </span>
                        <span wire:loading wire:target="reconcileStock">
                            <span class="spinner-border spinner-border-sm me-2"></span>
                            Reconciling...
                        </span>
                    </button>
                </div>
            </div>
        </div>
    </div>
    <!-- End of Reconcile Confirmation Modal -->

    @push('scripts')
        <script>
            document.addEventListener('livewire:init', function () {
                // Set initial tab based on Livewire activeTab property
                var activeTab = @this.activeTab;
                var tab = document.querySelector('a[href="#' + activeTab + '"]');
                if (tab) {
                    var bsTab = new bootstrap.Tab(tab);
                    bsTab.show();
                }
            });

            // Handle successful stock check processing
            window.addEventListener('stock-check-processed', event => {
                @this.set('activeTab', 'results');
                var resultTab = document.querySelector('a[href="#results"]');
                if (resultTab) {
                    var bsTab = new bootstrap.Tab(resultTab);
                    bsTab.show();
                }
            });
        </script>
    @endpush
</div>
