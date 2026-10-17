@props(['label', 'count', 'max', 'href' => '#'])

@php
    $pct = $max > 0 ? min(100, ($count / $max) * 100) : 0;
@endphp

<a href="{{ $href }}" class="mcp-bar-row text-decoration-none"
   aria-label="{{ $label }}: {{ $count }} {{ $count === 1 ? 'job' : 'jobs' }}"
   style="--mcp-bar-width: {{ $pct }}%">
    <span class="lbl">{{ $label }}</span>
    <span class="mcp-bar-track" aria-hidden="true">
        <span class="mcp-bar-fill"></span>
    </span>
    <span class="v" aria-hidden="true">{{ $count }}</span>
</a>
