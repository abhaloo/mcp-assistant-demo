{{-- wire:ignore keeps the first-render href; Livewire re-renders would otherwise see this page as "previous". --}}
<a href="{{ $href }}" data-back-link wire:ignore {{ $attributes }}>{{ $slot }}</a>
