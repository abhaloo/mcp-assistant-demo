@props([
    'eyebrow'  => null,
    'title'    => null,
    'subtitle' => null,
    'mark'     => true,   // red accent bar before the title. Pass :mark="false" to hide.
])

<div class="mcp-page-header">
    <div class="mcp-page-header__main">
        @isset($breadcrumb)
            <div class="mcp-page-header__eyebrow">{{ $breadcrumb }}</div>
        @elseif ($eyebrow)
            <div class="mcp-page-header__eyebrow">{{ $eyebrow }}</div>
        @endisset

        <div class="mcp-page-header__title-row">
            @if ($mark)
                <span class="mcp-page-header__mark" aria-hidden="true"></span>
            @endif
            <h1 class="mcp-page-header__title">
                @isset($titleSlot)
                    {{ $titleSlot }}
                @else
                    {{ $title }}
                @endisset
            </h1>
            {{ $titleAdornment ?? '' }}
        </div>

        @isset($subtitleSlot)
            <p class="mcp-page-header__subtitle">{{ $subtitleSlot }}</p>
        @elseif ($subtitle)
            <p class="mcp-page-header__subtitle">{{ $subtitle }}</p>
        @endisset
    </div>

    @isset($actions)
        <div class="mcp-page-header__actions">
            {{ $actions }}
        </div>
    @endisset
</div>
