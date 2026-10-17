{{-- Mobile field renderer - stacked label-above-input layout with proper touch targets --}}
@php
    $canEditField = $item->canBeEditedBy(auth()->user());
    $isViewMode = $workOrder->editing == 'CLOSED';
    $isDisabled = $isViewMode || !$canEditField;
    $fieldValue = $payload[$item->name] ?? null;
@endphp

<div class="mb-4 pb-3" style="border-bottom: 1px solid #f0f0f0;">
    @if($item->type == "Options")
        <label class="form-label fw-semibold text-dark small mb-2">{{ $item->name }}</label>
        <div class="d-flex flex-wrap gap-2">
            @foreach(json_decode($item->value) ?? [] as $key => $value)
                @php
                    $isChecked = isset($payload[$item->name][$value]) && $payload[$item->name][$value];
                @endphp
                <div class="form-check mobile-check {{ $isChecked && $isViewMode ? 'option-selected' : '' }}" style="min-height: 2.5rem; padding-left: 2rem;">
                    <input class="form-check-input" type="checkbox"
                           wire:model.live="payload.{{ $item->name }}.{{ $value }}"
                           id="m_{{ $item->id }}_{{ $key }}"
                           @if($isDisabled) disabled @endif
                           style="width: 1.4em; height: 1.4em; margin-top: 0.15em;">
                    <label class="form-check-label {{ $isChecked && $isViewMode ? 'label-selected' : '' }}" for="m_{{ $item->id }}_{{ $key }}" style="padding-top: 0.1em;">
                        {{ $value }}
                    </label>
                </div>
            @endforeach
        </div>

    @elseif($item->type == "Box")
        @php $isChecked = !empty($fieldValue); @endphp
        <div class="form-check form-switch mobile-check {{ $isChecked && $isViewMode ? 'option-selected' : '' }}" style="min-height: 2.5rem; padding-left: 2.5rem;">
            <input class="form-check-input" type="checkbox"
                   wire:model.live="payload.{{ $item->name }}"
                   id="m_{{ $item->id }}"
                   @if($isDisabled) disabled @endif
                   style="width: 1.4em; height: 1.4em;">
            <label class="form-check-label fw-semibold {{ $isChecked && $isViewMode ? 'label-selected' : '' }}" for="m_{{ $item->id }}" style="padding-top: 0.1em;">
                {{ $item->name }}
            </label>
        </div>

    @elseif($item->type == "Description")
        @php $hasValue = !empty($fieldValue); @endphp
        <label class="form-label fw-semibold text-dark small mb-1">{{ $item->name }}</label>
        @if($hasValue && $isViewMode)
            <div class="form-control bg-light" style="min-height: 3rem; white-space: pre-wrap; font-size: 0.9rem;">{{ $fieldValue }}</div>
        @else
            <textarea class="form-control" rows="3"
                      wire:model.live="payload.{{ $item->name }}"
                      id="m_{{ $item->id }}"
                      placeholder="{{ $item->name }}"
                      style="font-size: 0.9rem;"
                      @if($isDisabled) disabled @endif></textarea>
        @endif

    @elseif($item->type == "Soft List" || $item->type == "Hard List")
        @php $hasValue = !empty($fieldValue); @endphp
        <label class="form-label fw-semibold text-dark small mb-1">{{ $item->name }}</label>
        <input type="text" class="form-control {{ $hasValue && $isViewMode ? 'bg-light fw-semibold' : '' }}"
               wire:model.live="payload.{{ $item->name }}"
               list="m_list_{{ $item->id }}"
               id="m_{{ $item->id }}"
               placeholder="{{ $item->name }}"
               style="font-size: 0.9rem;"
               @if($isDisabled) disabled @endif>
        @if($item->type == "Soft List")
            <datalist id="m_list_{{ $item->id }}">
                @foreach($workOrder->items ?? [] as $mat)
                    <option>{{ $mat->product->name }}</option>
                @endforeach
            </datalist>
        @else
            <datalist id="m_list_{{ $item->id }}">
                <option>NCR blue-CFB 61x86</option>
                <option>NCR green-CFB 61x86</option>
                <option>NCR pink-CFB 61x86</option>
                <option>NCR white-CB 61x86</option>
                <option>NCR yellow-CF-55GSM 61x86</option>
            </datalist>
        @endif

    @else
        {{-- Text / Numbers --}}
        @php $hasValue = !empty($fieldValue) || (is_numeric($fieldValue) && $fieldValue !== ''); @endphp
        <label class="form-label fw-semibold text-dark small mb-1">{{ $item->name }}</label>
        <input type="{{ $item->type == 'Numbers' ? 'number' : 'text' }}"
               class="form-control {{ $hasValue && $isViewMode ? 'bg-light fw-semibold' : '' }}"
               wire:model.live="payload.{{ $item->name }}"
               id="m_{{ $item->id }}"
               placeholder="{{ $item->name }}"
               style="font-size: 0.9rem;"
               @if($isDisabled) disabled @endif>
    @endif
</div>
