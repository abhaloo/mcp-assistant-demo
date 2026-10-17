<div class="position-relative" wire:click.away="closeDropdown" id="supplier-search-{{ $this->getId() }}">
    {{-- Label --}}
    <label for="supplier-input-{{ $this->getId() }}" class="form-label mb-1">
        {{ $label }} @if($required)<span class="text-danger">*</span>@endif
    </label>

    {{-- Search Input with Add Button --}}
    <div class="input-group input-group-sm">
        @if($selectedSupplier)
            <input
                type="text"
                class="form-control bg-light"
                value="{{ $selectedSupplier['name'] }}"
                readonly
            />
            <button type="button" class="btn btn-outline-secondary" wire:click="clearSelection" title="Clear">
                <i class="fa fa-times"></i>
            </button>
        @else
            <input
                type="text"
                id="supplier-input-{{ $this->getId() }}"
                class="form-control"
                placeholder="{{ $placeholder }}"
                wire:model.live.debounce.300ms="query"
                autocomplete="off"
            />
            <button type="button" class="btn btn-outline-primary" title="Add New Supplier"
                    onclick="openSupplierModal('{{ $this->getId() }}')">
                <i class="fa fa-plus"></i>
            </button>
        @endif
    </div>

    {{-- Loading State --}}
    <div wire:loading wire:target="query" class="position-absolute w-100 text-muted small py-2 px-3 bg-light border" style="z-index: 1050; top: 100%;">
        <i class="fa fa-spinner fa-spin me-1"></i> Searching...
    </div>

    {{-- Search Results Dropdown --}}
    @if(!empty($query) && empty($selectedSupplier))
        <div class="supplier-search-dropdown position-absolute w-100 bg-white border rounded shadow-sm" style="z-index: 1050; top: 100%; max-height: 280px; overflow-y: auto;">
            @if(!empty($suppliers))
                @foreach($suppliers as $supplier)
                    <a href="#"
                        wire:click.prevent="selectSupplier({{ $supplier['id'] }})"
                        wire:key="supplier-{{ $supplier['id'] }}"
                        class="supplier-search-item d-flex justify-content-between align-items-center px-3 py-2 text-decoration-none border-bottom"
                    >
                        <div class="d-flex flex-column">
                            <span class="text-truncate">{{ $supplier['name'] }}</span>
                        </div>
                        <x-badge :color="$supplier['type'] === 'Company' ? 'primary' : 'secondary'" class="flex-shrink-0 ms-2">
                            {{ $supplier['type'] }}
                        </x-badge>
                    </a>
                @endforeach
            @else
                <div class="px-3 py-2 text-muted small">
                    <i class="fa fa-exclamation-circle me-1"></i> No suppliers found.
                    <a href="#" onclick="openSupplierModal('{{ $this->getId() }}'); return false;" class="text-primary">Create new?</a>
                </div>
            @endif
        </div>
    @endif

    {{-- New Supplier Modal --}}
    <div class="modal fade" id="newSupplierModal-{{ $this->getId() }}" tabindex="-1" aria-labelledby="newSupplierModalLabel-{{ $this->getId() }}" aria-hidden="true" wire:ignore.self>
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header bg-primary text-white py-2">
                    <h6 class="modal-title" id="newSupplierModalLabel-{{ $this->getId() }}">
                        <i class="fa fa-user-plus me-2"></i>Add New Supplier
                    </h6>
                    <button type="button" class="btn-close btn-close-white" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row g-3">
                        {{-- Name --}}
                        <div class="col-12">
                            <label class="form-label">Supplier Name <span class="text-danger">*</span></label>
                            <input type="text"
                                   wire:model.live="newSupplierName"
                                   class="form-control @error('newSupplierName') is-invalid @enderror"
                                   placeholder="Enter supplier name">
                            @error('newSupplierName')
                                <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>

                        {{-- Type --}}
                        <div class="col-md-6">
                            <label class="form-label">Type <span class="text-danger">*</span></label>
                            <select wire:model.live="newSupplierType"
                                    class="form-select @error('newSupplierType') is-invalid @enderror">
                                <option value="Company">Company</option>
                                <option value="Individual">Individual</option>
                            </select>
                            @error('newSupplierType')
                                <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>

                        {{-- Currency --}}
                        <div class="col-md-6">
                            <label class="form-label">Currency <span class="text-danger">*</span></label>
                            <select wire:model.live="newSupplierCurrency"
                                    class="form-select @error('newSupplierCurrency') is-invalid @enderror">
                                <option value="">Select currency...</option>
                                @foreach($currencies as $currency)
                                    <option value="{{ $currency->id }}">{{ $currency->name }} ({{ $currency->symbol }})</option>
                                @endforeach
                            </select>
                            @error('newSupplierCurrency')
                                <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>

                        {{-- Phone --}}
                        <div class="col-md-6">
                            <label class="form-label">Phone <span class="text-muted fw-normal">(optional)</span></label>
                            <input type="text"
                                   wire:model.live="newSupplierPhone"
                                   class="form-control @error('newSupplierPhone') is-invalid @enderror"
                                   placeholder="Phone number">
                            @error('newSupplierPhone')
                                <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>

                        {{-- Email --}}
                        <div class="col-md-6">
                            <label class="form-label">Email <span class="text-muted fw-normal">(optional)</span></label>
                            <input type="email"
                                   wire:model.live="newSupplierEmail"
                                   class="form-control @error('newSupplierEmail') is-invalid @enderror"
                                   placeholder="Email address">
                            @error('newSupplierEmail')
                                <div class="invalid-feedback">{{ $message }}</div>
                            @enderror
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-coreui-dismiss="modal">Cancel</button>
                    <button type="button"
                            wire:click="createAndSelectSupplier"
                            wire:loading.attr="disabled"
                            class="btn btn-primary">
                        <span wire:loading.remove wire:target="createAndSelectSupplier">
                            <i class="fa fa-plus me-1"></i> Create Supplier
                        </span>
                        <span wire:loading wire:target="createAndSelectSupplier">
                            <i class="fa fa-spinner fa-spin me-1"></i> Creating...
                        </span>
                    </button>
                </div>
            </div>
        </div>
    </div>

    @once
    @push('scripts')
    <script>
        function openSupplierModal(componentId) {
            var modalEl = document.getElementById('newSupplierModal-' + componentId);
            if (modalEl) {
                var modal = new coreui.Modal(modalEl);
                modal.show();
            }
        }

        document.addEventListener('close-supplier-modal', function(e) {
            var modalId = 'newSupplierModal-' + e.detail.id;
            $('#' + modalId).modal('hide');
        });
    </script>
    @endpush
    <style>
        .supplier-search-item {
            color: #212529 !important;
            background-color: #fff;
            transition: background-color 0.15s ease;
        }
        .supplier-search-item:hover {
            background-color: #e9ecef;
            color: #212529 !important;
        }
    </style>
    @endonce
</div>
