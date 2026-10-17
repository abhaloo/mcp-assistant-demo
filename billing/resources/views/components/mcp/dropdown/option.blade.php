@props([
    'checked' => false,
    'disabled' => false,
    'icon' => null,
])

@php
    $resolvedIcon = $icon ?? 'fa-check';
    $hideIcon = $icon === null && ! $checked;
@endphp

<button type="button"
        role="menuitemradio"
        aria-checked="{{ $checked ? 'true' : 'false' }}"
        @disabled($disabled)
        @if ($disabled) aria-disabled="true" @endif
        {{ $attributes->class('mcp-dropdown__option') }}>
    <i class="fa {{ $resolvedIcon }}" aria-hidden="true" @if ($hideIcon) hidden @endif></i>
    {{ $slot }}
</button>
