@props([
    'title' => null,
    'count' => null,
    'flush' => false,
    'class' => '',
])

<div {{ $attributes->merge(['class' => 'mcp-card '.$class]) }}>
    @if ($title || isset($actions))
        <div class="mcp-card__header">
            <div>
                @if ($title)
                    <h2 class="mcp-card__title">
                        {{ $title }}
                        @if (!is_null($count))
                            <span class="mcp-card__count">{{ $count }}</span>
                        @endif
                    </h2>
                @endif
            </div>
            @isset($actions)
                <div class="mcp-card__actions">{{ $actions }}</div>
            @endisset
        </div>
    @endif

    <div @class([
        'mcp-card__body',
        'mcp-card__body--flush' => $flush,
    ])>
        {{ $slot }}
    </div>
</div>
