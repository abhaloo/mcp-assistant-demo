{{--
    Loading Overlay Component
    
    Usage:
    <x-loading-overlay target="filter, report" />
    <x-loading-overlay target="save" message="Saving changes..." />
    
    Props:
    - target: Comma-separated Livewire method names to trigger loading state
    - message: Optional loading message (default: "Loading...")
--}}

@props([
    'target' => '',
    'message' => 'Loading...'
])

<div 
    wire:loading.flex 
    wire:target="{{ $target }}" 
    class="loading-overlay"
    role="status"
    aria-live="polite"
>
    <div class="loading-content">
        <div class="spinner spinner-primary" aria-hidden="true"></div>
        <p class="loading-text">{{ $message }}</p>
    </div>
</div>

