@props([
    'items' => [],
])

<nav class="mcp-breadcrumb" aria-label="Breadcrumb">
    @foreach ($items as $i => $item)
        @if ($i > 0)
            <i class="fa fa-chevron-right mcp-breadcrumb__sep" aria-hidden="true"></i>
        @endif
        @if ($i < count($items) - 1 && !empty($item['href']))
            <a href="{{ $item['href'] }}" class="mcp-breadcrumb__link">{{ $item['label'] }}</a>
        @else
            <span class="mcp-breadcrumb__current" @if ($i === count($items) - 1) aria-current="page" @endif>{{ $item['label'] }}</span>
        @endif
    @endforeach
</nav>
