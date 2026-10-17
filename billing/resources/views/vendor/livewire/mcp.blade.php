@php
if (! isset($scrollTo)) {
    $scrollTo = 'body';
}

$scrollIntoViewJsSnippet = ($scrollTo !== false)
    ? <<<JS
       (\$el.closest('{$scrollTo}') || document.querySelector('{$scrollTo}')).scrollIntoView()
    JS
    : '';
@endphp

<div>
    @if ($paginator->hasPages())
        <nav class="mcp-pagination d-none d-md-flex" role="navigation" aria-label="Pagination Navigation">
            <span class="mcp-pagination__pages">
                @if ($paginator->onFirstPage())
                    <span class="mcp-pagination__nav" aria-disabled="true" aria-label="@lang('pagination.previous')">
                        <i class="fa fa-chevron-left"></i>
                    </span>
                @else
                    <button type="button"
                            dusk="previousPage{{ $paginator->getPageName() == 'page' ? '' : '.' . $paginator->getPageName() }}"
                            class="mcp-pagination__nav"
                            wire:click="previousPage('{{ $paginator->getPageName() }}')"
                            x-on:click="{{ $scrollIntoViewJsSnippet }}"
                            wire:loading.attr="disabled"
                            rel="prev"
                            aria-label="@lang('pagination.previous')">
                        <i class="fa fa-chevron-left"></i>
                    </button>
                @endif

                @foreach ($elements as $element)
                    @if (is_string($element))
                        <span class="mcp-pagination__ellipsis">{{ $element }}</span>
                    @endif

                    @if (is_array($element))
                        @foreach ($element as $page => $url)
                            @if ($page == $paginator->currentPage())
                                <span class="mcp-pagination__page mcp-pagination__page--active"
                                      wire:key="paginator-{{ $paginator->getPageName() }}-page-{{ $page }}"
                                      aria-current="page">{{ $page }}</span>
                            @else
                                <button type="button"
                                        class="mcp-pagination__page"
                                        wire:key="paginator-{{ $paginator->getPageName() }}-page-{{ $page }}"
                                        wire:click="gotoPage({{ $page }}, '{{ $paginator->getPageName() }}')"
                                        x-on:click="{{ $scrollIntoViewJsSnippet }}"
                                        wire:loading.attr="disabled">{{ $page }}</button>
                            @endif
                        @endforeach
                    @endif
                @endforeach

                @if ($paginator->hasMorePages())
                    <button type="button"
                            dusk="nextPage{{ $paginator->getPageName() == 'page' ? '' : '.' . $paginator->getPageName() }}"
                            class="mcp-pagination__nav"
                            wire:click="nextPage('{{ $paginator->getPageName() }}')"
                            x-on:click="{{ $scrollIntoViewJsSnippet }}"
                            wire:loading.attr="disabled"
                            rel="next"
                            aria-label="@lang('pagination.next')">
                        <i class="fa fa-chevron-right"></i>
                    </button>
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
                <button type="button"
                        dusk="previousPage{{ $paginator->getPageName() == 'page' ? '' : '.' . $paginator->getPageName() }}"
                        class="mcp-pagination__nav"
                        wire:click="previousPage('{{ $paginator->getPageName() }}')"
                        x-on:click="{{ $scrollIntoViewJsSnippet }}"
                        wire:loading.attr="disabled"
                        rel="prev"
                        aria-label="Previous page">
                    <i class="fa fa-chevron-left"></i> Prev
                </button>
            @endif

            <span class="mcp-pagination__summary" aria-live="polite">
                Page <strong>{{ $paginator->currentPage() }}</strong> of {{ $paginator->lastPage() }}
            </span>

            @if ($paginator->hasMorePages())
                <button type="button"
                        dusk="nextPage{{ $paginator->getPageName() == 'page' ? '' : '.' . $paginator->getPageName() }}"
                        class="mcp-pagination__nav"
                        wire:click="nextPage('{{ $paginator->getPageName() }}')"
                        x-on:click="{{ $scrollIntoViewJsSnippet }}"
                        wire:loading.attr="disabled"
                        rel="next"
                        aria-label="Next page">
                    Next <i class="fa fa-chevron-right"></i>
                </button>
            @else
                <span class="mcp-pagination__nav" aria-disabled="true" aria-label="Next page">
                    Next <i class="fa fa-chevron-right"></i>
                </span>
            @endif
        </nav>
    @endif
</div>
