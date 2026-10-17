{{-- Modern field rendering partial - inline label layout with underline inputs --}}
@php
    $canEditField = $item->canBeEditedBy(auth()->user());
    $isViewMode = $workOrder->editing == 'CLOSED';
    $isDisabled = $isViewMode || !$canEditField;
    
    // Dynamic label width based on column allocation
    // For narrow columns (<=3), use auto width; for wider columns, use fixed min-width
    $labelStyle = $item->columns <= 3 
        ? 'font-weight: 600; font-size: 0.9rem; color: #333; white-space: nowrap;' 
        : 'min-width: 120px; font-weight: 600; font-size: 0.9rem; color: #333;';
    
    // Get field value for state-based styling
    $fieldValue = $payload[$item->name] ?? null;
@endphp

<div class="d-flex align-items-center" style="padding: 0.4rem 1rem 0.4rem 0;">
    @if($item->type == "Options")
        <span class="field-label me-2" style="{{ $labelStyle }}">{{ $item->name }}:</span>
        <div class="d-flex flex-wrap align-items-center gap-2">
            @foreach(json_decode($item->value) ?? [] as $key => $value)
                @php
                    $isChecked = isset($payload[$item->name][$value]) && $payload[$item->name][$value];
                @endphp
                <div class="form-check form-check-inline mb-0 {{ $isChecked && $isViewMode ? 'option-selected' : '' }}">
                    <input class="form-check-input" type="checkbox" 
                           wire:model.live="payload.{{ $item->name }}.{{ $value }}"
                           id="edit_{{ $item->id }}_{{ $key }}"
                           @if($isDisabled) disabled @endif>
                    <label class="form-check-label {{ $isChecked && $isViewMode ? 'label-selected' : '' }}" style="font-size: 0.9rem;" for="edit_{{ $item->id }}_{{ $key }}">{{ $value }}</label>
                </div>
            @endforeach
        </div>
    @elseif($item->type == "Box")
        @php
            $isChecked = !empty($fieldValue);
        @endphp
        <div class="form-check mb-0 {{ $isChecked && $isViewMode ? 'option-selected' : '' }}">
            <input class="form-check-input" type="checkbox"
                   wire:model.live="payload.{{ $item->name }}"
                   id="edit_{{ $item->id }}"
                   @if($isDisabled) disabled @endif>
            <label class="form-check-label {{ $isChecked && $isViewMode ? 'label-selected' : '' }}" style="font-weight: 600; font-size: 0.9rem; color: #333;" for="edit_{{ $item->id }}">{{ $item->name }}</label>
        </div>
    @elseif($item->type == "Description")
        @php
            $hasValue = !empty($fieldValue);
        @endphp
        @if($hasValue && $isViewMode)
            {{-- View mode: display as expandable div that shows full content without scrolling --}}
            <div class="text-field-filled d-flex flex-grow-1">
                <span class="field-label me-2" style="{{ $labelStyle }} align-self: flex-start;">{{ $item->name }}:</span>
                <div class="flex-grow-1 input-has-value"
                     style="border-bottom: 1px solid #ccc; padding: 0 0.25rem; font-size: 0.9rem; white-space: pre-wrap; min-height: 1.5em;">{{ $fieldValue }}</div>
            </div>
        @else
            {{-- Edit mode: keep textarea with improved defaults --}}
            <span class="field-label me-2" style="{{ $labelStyle }} align-self: flex-start;">{{ $item->name }}:</span>
            <textarea class="form-control-line flex-grow-1" rows="2"
                      wire:model.live="payload.{{ $item->name }}"
                      id="edit_{{ $item->id }}"
                      style="border: none; border-bottom: 1px solid #333; border-radius: 0; padding: 0 0.25rem; font-size: 0.9rem; resize: vertical; background: transparent;"
                      @if($isDisabled) disabled @endif></textarea>
        @endif
    @elseif($item->type == "Soft List" || $item->type == "Hard List")
        @php
            $hasValue = !empty($fieldValue);
        @endphp
        @if($hasValue && $isViewMode)<div class="text-field-filled d-flex align-items-center flex-grow-1">@endif
        <span class="field-label me-2" style="{{ $labelStyle }}">{{ $item->name }}:</span>
        <input type="text" class="form-control-line flex-grow-1 {{ $hasValue && $isViewMode ? 'input-has-value' : '' }}"
               wire:model.live="payload.{{ $item->name }}"
               list="edit_list_{{ $item->id }}"
               id="edit_{{ $item->id }}"
               style="border: none; border-bottom: 1px solid #333; border-radius: 0; padding: 0 0.25rem; font-size: 0.9rem; background: transparent;"
               @if($isDisabled) disabled style="border-bottom-color: #ccc;" @endif>
        @if($item->type == "Soft List")
            <datalist id="edit_list_{{ $item->id }}">
                @foreach($workOrder->items ?? [] as $mat)
                    <option>{{ $mat->product->name }}</option>
                @endforeach
            </datalist>
        @else
            <datalist id="edit_list_{{ $item->id }}">
                <option>NCR blue-CFB 61x86</option>
                <option>NCR green-CFB 61x86</option>
                <option>NCR pink-CFB 61x86</option>
                <option>NCR white-CB 61x86</option>
                <option>NCR yellow-CF-55GSM 61x86</option>
            </datalist>
        @endif
        @if($hasValue && $isViewMode)</div>@endif
    @else
        @php
            $hasValue = !empty($fieldValue) || (is_numeric($fieldValue) && $fieldValue !== '');
        @endphp
        @if($hasValue && $isViewMode)<div class="text-field-filled d-flex align-items-center flex-grow-1">@endif
        <span class="field-label me-2" style="{{ $labelStyle }}">{{ $item->name }}:</span>
        <input type="{{ $item->type == 'Numbers' ? 'number' : 'text' }}" class="form-control-line flex-grow-1 {{ $hasValue && $isViewMode ? 'input-has-value' : '' }}"
               wire:model.live="payload.{{ $item->name }}"
               id="edit_{{ $item->id }}"
               style="border: none; border-bottom: 1px solid #333; border-radius: 0; padding: 0 0.25rem; font-size: 0.9rem; background: transparent;"
               @if($isDisabled) disabled style="border-bottom-color: #ccc;" @endif>
        @if($hasValue && $isViewMode)</div>@endif
    @endif
</div>
