<div>
    {{-- Simple explanation --}}
    <p class="text-muted mb-4">
        Define how products in this category are counted and costed.
        <em>Example: Papers use Pallet → Ream → Sheet, where 1 Pallet = 20 Reams, 1 Ream = 500 Sheets.</em>
    </p>

    {{-- Invoice Unit --}}
    <div class="mb-4">
        <label for="invoice_unit_label" class="form-label fw-medium">Invoice Unit</label>
        <input type="text" wire:model.live="invoice_unit.label" class="form-control @error('invoice_unit.label') is-invalid @enderror" id="invoice_unit_label" placeholder="e.g., Pallet, Drum, Tin">
        @error('invoice_unit.label')
        <div class="invalid-feedback">{{ $message }}</div>
        @enderror
        <small class="text-muted">The unit shown on supplier invoices for this category.</small>
    </div>

    <hr class="my-4">

    {{-- Unit Levels --}}
    <div class="mb-4">
        <label class="form-label fw-medium">Unit Levels</label>
        <small class="text-muted d-block mb-3">Add levels from largest to smallest. Mark the smallest as the Stock Unit.</small>

        @if(count($count_levels) > 0)
        <div class="mb-3">
            @foreach($count_levels as $index => $level)
            <div class="d-flex align-items-center border rounded p-2 mb-2 bg-white">
                <x-badge color="primary" class="me-3">{{ $level['level'] }}</x-badge>
                <div class="flex-grow-1">
                    <strong>{{ $level['label'] }}</strong>
                    <small class="text-muted ms-2">({{ $level['key'] }})</small>
                </div>
                <div class="d-flex align-items-center gap-2">
                    @if($level['is_stock_unit'] ?? false)
                    <x-badge color="success">Stock Unit</x-badge>
                    @else
                    <button type="button" wire:click="setStockUnit({{ $index }})" class="btn btn-sm btn-outline-success">
                        Set as Stock
                    </button>
                    @endif
                    @if($index > 0)
                    <button type="button" wire:click="moveUp({{ $index }})" class="btn btn-sm btn-outline-secondary" title="Move Up">
                        <i class="fa fa-arrow-up"></i>
                    </button>
                    @endif
                    @if($index < count($count_levels) - 1)
                    <button type="button" wire:click="moveDown({{ $index }})" class="btn btn-sm btn-outline-secondary" title="Move Down">
                        <i class="fa fa-arrow-down"></i>
                    </button>
                    @endif
                    <button type="button" wire:click="removeCountLevel({{ $index }})" class="btn btn-sm btn-outline-danger" title="Remove">
                        <i class="fa fa-times"></i>
                    </button>
                </div>
            </div>
            @endforeach
        </div>
        @endif

        {{-- Add New Level --}}
        <div class="row g-2 align-items-end">
            <div class="col">
                <input type="text" wire:model.live="new_level_label" class="form-control form-control-sm" placeholder="Label (e.g., Ream)">
            </div>
            <div class="col">
                <input type="text" wire:model.live="new_level_key" class="form-control form-control-sm" placeholder="Key (e.g., ream)">
            </div>
            <div class="col-auto">
                <button type="button" wire:click="addCountLevel" class="btn btn-sm btn-outline-primary">
                    <i class="fa fa-plus"></i> Add
                </button>
            </div>
        </div>
    </div>

    <hr class="my-4">

    {{-- Product Attributes --}}
    <div class="mb-2">
        <label class="form-label fw-medium">Product Attributes</label>
        <small class="text-muted d-block mb-3">Define extra fields for products in this category (e.g., GSM, Size, Color).</small>

        @if(count($attributes) > 0)
        <div class="mb-3">
            @foreach($attributes as $index => $attr)
            <div class="d-flex align-items-center border rounded p-2 mb-2 bg-white">
                <div class="flex-grow-1">
                    <strong>{{ $attr['label'] }}</strong>
                    <small class="text-muted ms-2">({{ $attr['key'] }})</small>
                    <x-badge color="light" class="border ms-2">{{ ucfirst($attr['type']) }}</x-badge>
                    @if($attr['required'] ?? false)<x-badge color="warning" class="ms-1">Required</x-badge>@endif
                    @if($attr['show_in_list'] ?? false)<x-badge color="secondary" class="ms-1">Visible</x-badge>@endif
                </div>
                @if($attr['type'] === 'select' && isset($attr['options']))
                <small class="text-muted me-3">{{ count($attr['options']) }} options</small>
                @endif
                <button type="button" wire:click="removeAttribute({{ $index }})" class="btn btn-sm btn-outline-danger">
                    <i class="fa fa-times"></i>
                </button>
            </div>
            @endforeach
        </div>
        @endif

        {{-- Add New Attribute --}}
        <div class="border rounded p-3 bg-light">
            <div class="row g-2 mb-2">
                <div class="col-md-3">
                    <input type="text" wire:model.live="new_attr_key" class="form-control form-control-sm" placeholder="Key (e.g., gsm)">
                </div>
                <div class="col-md-3">
                    <input type="text" wire:model.live="new_attr_label" class="form-control form-control-sm" placeholder="Label (e.g., GSM)">
                </div>
                <div class="col-md-3">
                    <select wire:model.live="new_attr_type" class="form-control form-control-sm">
                        <option value="text">Text</option>
                        <option value="number">Number</option>
                        <option value="dimensions">Dimensions</option>
                        <option value="select">Select (Dropdown)</option>
                    </select>
                </div>
                <div class="col-md-3">
                    <input type="text" wire:model.live="new_attr_placeholder" class="form-control form-control-sm" placeholder="Placeholder">
                </div>
            </div>
            <div class="d-flex align-items-center gap-3 mb-2">
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" wire:model.live="new_attr_required" id="new_attr_required">
                    <label class="form-check-label small" for="new_attr_required">Required</label>
                </div>
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" wire:model.live="new_attr_show_in_list" id="new_attr_show_in_list">
                    <label class="form-check-label small" for="new_attr_show_in_list">Show in Product List</label>
                </div>
            </div>

            @if($new_attr_type === 'select')
            <div class="mb-2">
                <div class="input-group input-group-sm">
                    <input type="text" wire:model.live="new_option_value" wire:keydown.enter.prevent="addOption" class="form-control" placeholder="Add option and press Enter">
                    <button type="button" wire:click="addOption" class="btn btn-outline-secondary">Add</button>
                </div>
                @if(count($new_attr_options) > 0)
                <div class="mt-2">
                    @foreach($new_attr_options as $optIndex => $opt)
                    <x-badge color="primary" class="me-1">
                        {{ $opt }}
                        <button type="button" wire:click="removeOption({{ $optIndex }})" class="btn-close btn-close-white btn-sm ms-1" style="font-size: 0.5rem;"></button>
                    </x-badge>
                    @endforeach
                </div>
                @endif
            </div>
            @endif

            <button type="button" wire:click="addAttribute" class="btn btn-sm btn-outline-primary">
                <i class="fa fa-plus"></i> Add Attribute
            </button>
        </div>
    </div>
</div>
