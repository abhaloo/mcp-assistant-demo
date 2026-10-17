@props(['label'])

<dt>{{ $label }}</dt>
<dd>
    <span class="mcp-deflist__value-text">{{ $slot }}</span>
    @isset($action)
        <span class="mcp-deflist__action">{{ $action }}</span>
    @endisset
</dd>
