@if ($paginator->hasPages())
    <nav class="mcp-pagination d-none d-md-flex" role="navigation" aria-label="Pagination Navigation">
        <span class="mcp-pagination__pages">
            @if ($paginator->onFirstPage())
                <span class="mcp-pagination__nav" aria-disabled="true" aria-label="@lang('pagination.previous')">
                    <i class="fa fa-chevron-left"></i>
                </span>
            @else
                <a href="{{ $paginator->previousPageUrl() }}" class="mcp-pagination__nav" rel="prev" aria-label="@lang('pagination.previous')">
                    <i class="fa fa-chevron-left"></i>
                </a>
            @endif

            @foreach ($elements as $element)
                @if (is_string($element))
                    <span class="mcp-pagination__ellipsis">{{ $element }}</span>
                @endif

                @if (is_array($element))
                    @foreach ($element as $page => $url)
                        @if ($page == $paginator->currentPage())
                            <span class="mcp-pagination__page mcp-pagination__page--active" aria-current="page">{{ $page }}</span>
                        @else
                            <a href="{{ $url }}" class="mcp-pagination__page">{{ $page }}</a>
                        @endif
                    @endforeach
                @endif
            @endforeach

            @if ($paginator->hasMorePages())
                <a href="{{ $paginator->nextPageUrl() }}" class="mcp-pagination__nav" rel="next" aria-label="@lang('pagination.next')">
                    <i class="fa fa-chevron-right"></i>
                </a>
            @else
                <span class="mcp-pagination__nav" aria-disabled="true" aria-label="@lang('pagination.next')">
                    <i class="fa fa-chevron-right"></i>
                </span>
            @endif
        </span>

        <span class="mcp-pagination__summary">
            @if ($paginator->firstItem())
                Showing {{ $paginator->firstItem() }}–{{ $paginator->lastItem() }} of {{ $paginator->total() }}
            @else
                {{ $paginator->total() }} {{ \Illuminate\Support\Str::plural('result', $paginator->total()) }}
            @endif
        </span>
    </nav>

    <nav class="mcp-pagination d-md-none" aria-label="Pagination">
        @if ($paginator->onFirstPage())
            <span class="mcp-pagination__nav" aria-disabled="true" aria-label="Previous page">
                <i class="fa fa-chevron-left"></i> Prev
            </span>
        @else
            <a href="{{ $paginator->previousPageUrl() }}" class="mcp-pagination__nav" rel="prev" aria-label="Previous page">
                <i class="fa fa-chevron-left"></i> Prev
            </a>
        @endif

        <span class="mcp-pagination__summary" aria-live="polite">
            Page <strong>{{ $paginator->currentPage() }}</strong> of {{ $paginator->lastPage() }}
        </span>

        @if ($paginator->hasMorePages())
            <a href="{{ $paginator->nextPageUrl() }}" class="mcp-pagination__nav" rel="next" aria-label="Next page">
                Next <i class="fa fa-chevron-right"></i>
            </a>
        @else
            <span class="mcp-pagination__nav" aria-disabled="true" aria-label="Next page">
                Next <i class="fa fa-chevron-right"></i>
            </span>
        @endif
    </nav>
@endif
