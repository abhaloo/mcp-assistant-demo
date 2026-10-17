{{--
    Loading Button Component
    
    Usage:
    <x-loading-button target="save" label="Save" icon="fa-save" class="btn btn-primary" />
    <x-loading-button target="report" label="Export" loading-label="Exporting..." />
    
    Props:
    - target: Livewire method name to trigger loading state (required)
    - label: Button label text (required)
    - icon: FontAwesome icon class (optional, e.g., "fa-save")
    - loading-label: Text shown during loading (default: "Processing...")
--}}

@props([
    'target' => '',
    'label' => 'Submit',
    'icon' => null,
    'loadingLabel' => 'Processing...'
])

<button 
    {{ $attributes }}
    wire:loading.attr="disabled"
    wire:target="{{ $target }}"
>
    <span wire:loading.remove wire:target="{{ $target }}">
        @if($icon)
            <i class="fa {{ $icon }}"></i>
        @endif
        {{ $label }}
    </span>
    <span wire:loading wire:target="{{ $target }}">
        <i class="fa fa-spinner fa-spin"></i> {{ $loadingLabel }}
    </span>
</button>

