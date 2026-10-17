@props([
    'id',
    'title',
    'action' => null,      // For href links (e.g. route('foo'))
    'method' => null,      // For wire:click methods
    'confirmText' => 'Confirm',
    'confirmClass' => 'btn-primary',
    'confirmIcon' => 'fa-check',
    'cancelText' => 'Cancel',
    'bodyClass' => '',
])

<div class="modal fade" wire:ignore.self id="{{ $id }}" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
            <div class="modal-header py-2">
                <h5 class="modal-title">{{ $title }}</h5>
                <button type="button" class="btn-close" data-coreui-dismiss="modal"></button>
            </div>
            <div class="modal-body {{ $bodyClass }}">
                {{ $slot }}
            </div>
            <div class="modal-footer py-2">
                <button type="button" class="btn btn-secondary btn-sm" data-coreui-dismiss="modal">{{ $cancelText }}</button>
                
                @if($action)
                    <a href="{{ $action }}" class="btn {{ $confirmClass }} btn-sm">
                        @if($confirmIcon)<i class="fa {{ $confirmIcon }}"></i>@endif {{ $confirmText }}
                    </a>
                @elseif($method)
                    <button type="button" wire:click="{{ $method }}" class="btn {{ $confirmClass }} btn-sm" data-coreui-dismiss="modal">
                        @if($confirmIcon)<i class="fa {{ $confirmIcon }}"></i>@endif {{ $confirmText }}
                    </button>
                @endif
            </div>
        </div>
    </div>
</div>
