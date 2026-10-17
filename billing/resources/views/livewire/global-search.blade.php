@php

    $typeIcons = [

        'invoice' => 'fa-file-invoice',

        'quotation' => 'fa-file-lines',

        'payable_quotation' => 'fa-file-circle-check',

        'credit_note' => 'fa-file-circle-minus',

        'customer_order' => 'fa-cart-shopping',

        'job' => 'fa-briefcase',

        'customer' => 'fa-user',

        'supplier' => 'fa-truck',

        'product' => 'fa-box',

        'inventory' => 'fa-warehouse',

    ];

    $typeIconTones = [

        'invoice' => 'info',

        'quotation' => 'violet',

        'payable_quotation' => 'sky',

        'credit_note' => 'danger',

        'customer_order' => 'warning',

        'job' => 'primary',

        'customer' => 'success',

        'supplier' => 'neutral',

        'product' => 'info',

        'inventory' => 'neutral',

    ];

    $showPanel = mb_strlen(trim($query)) >= 2;

    $groups = $showPanel ? $this->results->groups() : [];

    $nonEmptyGroups = collect($groups)->filter(fn ($group) => ! empty($group['results']));

    $showListbox = $showPanel && ($nonEmptyGroups->isNotEmpty() || $ragEnabled);

    $showEmpty = $showPanel && $nonEmptyGroups->isEmpty() && ! $ragEnabled;

    // Task B4 / R1: the generic "Ask AI about <query>" row attaches context
    // ONLY when there is exactly one unique exact match — never first-result or
    // all-results. $uniqueExactMatch is untrusted-identifier-only (resource_type
    // + id); Billing re-resolves and re-authorizes it server-side before any of
    // it reaches RAG (AiSearchController -> RecordContextResolver).
    $uniqueExactMatch = $showPanel && $ragAvailable ? $this->uniqueExactMatch : null;

    // Task B7 / R1: presentation-only chip label ("E8520 (Quotation)") built from
    // data ALREADY rendered/authorized for this user ($result->title/type) — not
    // sent anywhere, just exposed as a data attribute so the composer chip can
    // read it. Never becomes part of the untrusted record_context wire payload.
    $recordLabel = fn ($result) => $result->title.' ('.\Illuminate\Support\Str::headline($result->type).')';

@endphp



<div class="mcp-topbar__search"

     wire:loading.class="is-searching"

     wire:target="query"

     data-rag-enabled="{{ $ragEnabled ? '1' : '0' }}"

     data-rag-available="{{ $ragAvailable ? '1' : '0' }}"

     data-ask-ai-available="{{ $ragAvailable ? '1' : '0' }}">

    <button type="button"

            class="mcp-topbar__search-trigger"

            aria-label="Open search"

            aria-expanded="false">

        <i class="fa fa-search mcp-topbar__search-trigger-icon mcp-topbar__search-trigger-icon--open" aria-hidden="true"></i>

        <i class="fa fa-times mcp-topbar__search-trigger-icon mcp-topbar__search-trigger-icon--close" aria-hidden="true"></i>

    </button>



    <label for="mcp-global-search" class="visually-hidden">Global search</label>

    <i class="fa fa-search mcp-topbar__search-icon" aria-hidden="true"></i>

    <input id="mcp-global-search"

           type="search"

           class="mcp-topbar__search-input"

           placeholder="Search jobs, orders, customers…"

           autocomplete="off"

           role="combobox"

           aria-autocomplete="list"

           aria-expanded="false"

           aria-controls="mcp-search-listbox"

           aria-activedescendant=""

           aria-describedby="mcp-search-hint"

           aria-keyshortcuts="Control+K"

           wire:model.live.debounce.250ms="query"

           wire:loading.attr="aria-busy"

           wire:target="query">

    <span id="mcp-search-hint" class="visually-hidden">Type at least 2 characters to search. Press Control+K to focus search from anywhere.</span>

    <kbd class="mcp-topbar__search-kbd" aria-hidden="true" data-mcp-search-kbd>Ctrl+K</kbd>

    <button type="button"

            class="mcp-topbar__search-clear"

            aria-label="Clear search"

            tabindex="-1">

        <i class="fa fa-times" aria-hidden="true"></i>

    </button>



    <div class="mcp-search-backdrop" aria-hidden="true" hidden></div>



    <div class="mcp-search-panel" role="presentation">

        <div class="mcp-search-panel__preflight" id="mcp-search-preflight" hidden>

            <i class="fa fa-keyboard mcp-search-panel__preflight-icon" aria-hidden="true"></i>

            <span class="mcp-search-panel__preflight-text">Type at least 2 characters to search</span>

        </div>



        <div class="mcp-search-panel__loading"

             data-mcp-search-loading

             role="status"

             aria-live="polite"

             aria-label="Searching"

             hidden>

            @for ($i = 0; $i < 3; $i++)

                <div class="mcp-search-skeleton__row" aria-hidden="true">

                    <span class="mcp-search-skeleton__icon"></span>

                    <span class="mcp-search-skeleton__lines">

                        <span class="mcp-search-skeleton__line mcp-search-skeleton__line--title"></span>

                        <span class="mcp-search-skeleton__line mcp-search-skeleton__line--subtitle"></span>

                    </span>

                </div>

            @endfor

        </div>



        @if ($showPanel)

            <div wire:loading.remove wire:target="query">

                @if ($showListbox)

                    <ul id="mcp-search-listbox"

                        class="mcp-search-panel__list mcp-search-panel__list--animated"

                        role="listbox"

                        aria-label="Search results">

                        @foreach ($nonEmptyGroups as $slug => $group)

                            @php $groupIndex = $loop->index; @endphp

                            <li class="mcp-search-panel__group" role="presentation">

                                <div class="mcp-search-panel__group-label" id="mcp-search-group-{{ $groupIndex }}">

                                    {{ $group['label'] }}

                                    <span class="mcp-search-panel__group-count">{{ count($group['results']) }}</span>

                                </div>

                                <ul role="group" aria-labelledby="mcp-search-group-{{ $groupIndex }}">

                                    @foreach ($group['results'] as $result)

                                        @php

                                            $tone = $typeIconTones[$result->type] ?? 'neutral';

                                            $url = $result->url ?? '#';

                                        @endphp

                                        <li role="presentation" class="mcp-search-result-row">

                                            <a id="mcp-search-option-{{ $groupIndex }}-{{ $loop->index }}"

                                               href="{{ $url }}"

                                               class="mcp-search-result"

                                               role="option"

                                               aria-selected="false"

                                               tabindex="-1">

                                                <span class="mcp-search-result__icon mcp-search-result__icon--{{ $tone }}" aria-hidden="true">

                                                    <i class="fa {{ $typeIcons[$result->type] ?? 'fa-file' }}"></i>

                                                </span>

                                                <span class="mcp-search-result__body">

                                                    <span class="mcp-search-result__title">{!! $this->highlightMatch($result->title, $query) !!}</span>

                                                    @if ($result->subtitle)

                                                        <span class="mcp-search-result__subtitle">{!! $this->highlightMatch($result->subtitle, $query) !!}</span>

                                                    @endif

                                                </span>

                                            </a>

                                            @if ($ragAvailable)

                                                {{-- Task B4 / R1: row-specific "Ask AI about this" action. Identifiers
                                                     only (resource_type + record_id) — Billing re-resolves and
                                                     re-authorizes server-side before anything reaches RAG. Reuses the
                                                     existing generic-ask-row role="option" pattern (mcp-search-ask)
                                                     so it participates in the SAME roving-tabindex arrow-key
                                                     navigation the combobox already implements — no new keyboard
                                                     paradigm. --}}

                                                <button type="button"

                                                        id="mcp-search-option-{{ $groupIndex }}-{{ $loop->index }}-ask"

                                                        class="mcp-search-result-ask"

                                                        role="option"

                                                        aria-selected="false"

                                                        aria-label="Ask Assistant about {{ $result->title }}"

                                                        tabindex="-1"

                                                        data-ask-query="{{ $query }}"

                                                        data-record-resource-type="{{ $result->type }}"

                                                        data-record-id="{{ $result->id }}"

                                                        data-record-label="{{ $recordLabel($result) }}">

                                                    <i class="fa fa-wand-magic-sparkles" aria-hidden="true"></i>

                                                </button>

                                            @endif

                                        </li>

                                    @endforeach

                                </ul>

                            </li>

                        @endforeach



                        @if ($ragAvailable)

                            <li class="mcp-search-panel__ask-wrap" role="presentation">

                                    {{-- Task B4 / R1: attaches record_context ONLY when $uniqueExactMatch is
                                         non-null (exactly one unique exact match across every result group) —
                                         the data-record-* attributes are simply absent otherwise, so
                                         mcp-global-search.js never has anything to attach. Never first-result
                                         or all-results implicitly. --}}

                                    <button type="button"

                                            id="mcp-search-ask-ai"

                                            class="mcp-search-ask"

                                            role="option"

                                            aria-selected="false"

                                            tabindex="-1"

                                            data-ask-query="{{ $query }}"

                                            @if ($uniqueExactMatch)

                                            data-record-resource-type="{{ $uniqueExactMatch->type }}"

                                            data-record-id="{{ $uniqueExactMatch->id }}"

                                            data-record-label="{{ $recordLabel($uniqueExactMatch) }}"

                                            @endif>

                                        <i class="fa fa-wand-magic-sparkles" aria-hidden="true"></i>

                                        <span class="mcp-search-ask__label">Ask Assistant about &ldquo;{{ $query }}&rdquo;</span>

                                        <kbd class="mcp-search-ask__hint" aria-hidden="true">Enter</kbd>

                                    </button>

                            </li>

                        @endif

                    </ul>

                @elseif ($showEmpty)

                    <div class="mcp-search-panel__empty" role="status">

                        <i class="fa fa-search" aria-hidden="true"></i>

                        <span>No results for &ldquo;{{ $query }}&rdquo;</span>

                    </div>

                @endif

            </div>

        @endif

    </div>

</div>



{{-- Controller: public/js/mcp-global-search.js (defer in layouts/app.blade.php) --}}


