@props([
    'icon' => 'fa-inbox',
    'title' => 'No records found',
    'hint' => null,
])

<div class="mcp-empty">
    <div class="mcp-empty__icon"><i class="fa {{ $icon }}"></i></div>
    <div class="mcp-empty__title">{{ $title }}</div>
    @if ($hint)
        <div class="mcp-empty__hint">{{ $hint }}</div>
    @endif
    @isset($action)
        <div class="mcp-empty__action">{{ $action }}</div>
    @endisset
</div>
