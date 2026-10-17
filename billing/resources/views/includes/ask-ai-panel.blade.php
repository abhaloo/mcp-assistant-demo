{{-- Ask Assistant push sidebar — opened via topbar, Ctrl+L, or the Ctrl+K assistant option. --}}
@php
    $ragEnabled = $ragEnabled ?? config('services.rag.enabled', false);
    $ragAvailable = $ragAvailable ?? false;
    $askV2Enabled = $askV2Enabled ?? (bool) (config('services.rag.ask_v2_enabled', false) || config('ask_ai.ask_v2_enabled', false));
    $askV2Modules = $askV2Enabled ? (array) config('ask_ai.v2_modules', []) : [];
    $askAiAnswerParts = (array) config('ask_ai.answer_parts', []);
    $askAiRecordRefs = (bool) ($askAiAnswerParts['record_refs'] ?? true);
    $askAiCollapsibleSources = (bool) ($askAiAnswerParts['collapsible_sources'] ?? true);
    $askAiCredits = $askAiCredits ?? null;
@endphp

<div class="mcp-ask-ai-backdrop" hidden aria-hidden="true"></div>

<aside class="mcp-ask-ai-sidebar"
       id="mcpAskAiSidebar"
       role="complementary"
       aria-label="Ask Assistant"
       aria-hidden="true"
       hidden>
    <div class="mcp-ask-ai-sidebar__resize" data-mcp-ask-ai-resize aria-hidden="true"></div>

    <header class="mcp-ask-ai-sidebar__header">
        <h2 class="mcp-ask-ai-sidebar__title">
            <svg class="mcp-ask-ai-sidebar__title-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path fill="currentColor" d="M12 2.2 14.35 9.65 21.8 12 14.35 14.35 12 21.8 9.65 14.35 2.2 12 9.65 9.65Z"/>
            </svg>
            Ask Assistant
        </h2>
        <span class="mcp-ask-ai-sidebar__badge">Preview</span>
        <div class="mcp-ask-ai-sidebar__actions">
            <button type="button"
                    class="mcp-ask-ai-sidebar__icon-btn"
                    data-mcp-ask-new
                    data-mcp-tip="New conversation"
                    data-mcp-tip-pos="bottom"
                    aria-label="New conversation">
                <i class="fa fa-plus" aria-hidden="true"></i>
            </button>
            <button type="button"
                    class="mcp-ask-ai-sidebar__icon-btn"
                    data-mcp-ask-ai-close
                    data-mcp-tip="Close panel"
                    data-mcp-tip-pos="bottom"
                    aria-label="Close Ask Assistant panel">
                <i class="fa fa-xmark" aria-hidden="true"></i>
            </button>
        </div>
    </header>

    <div class="mcp-ask-ai-sidebar__body"
         id="mcp-ask-ai-root"
         data-stream-url="{{ $askV2Enabled ? route('ai-search.v2.stream') : route('ai-search.stream') }}"
         data-v2-enabled="{{ $askV2Enabled ? '1' : '0' }}"
         data-v2-modules='@json($askV2Modules, JSON_HEX_APOS | JSON_HEX_QUOT)'
         data-record-refs="{{ $askAiRecordRefs ? '1' : '0' }}"
         data-collapsible-sources="{{ $askAiCollapsibleSources ? '1' : '0' }}"
         data-feedback-url="{{ route('ai-search.feedback') }}"
         data-timing-url="{{ route('ai-search.timing') }}"
         data-cancel-url="{{ route('ai-search.cancel') }}"
         data-credits-url="{{ route('ai-search.credits') }}"
         @if($askAiCredits !== null) data-credits='@json($askAiCredits, JSON_HEX_APOS | JSON_HEX_QUOT)' @endif
         data-ask-ai-available="{{ $ragEnabled && $ragAvailable ? '1' : '0' }}"
         data-rag-enabled="{{ $ragEnabled && $ragAvailable ? '1' : '0' }}"
         data-starters='@json($askAiStarters)'>

        <div class="mcp-ask-ai__thread"
             role="log"
             aria-label="Conversation"
             aria-busy="false"
             tabindex="-1"></div>

        <div class="mcp-ask-ai__feedback-layer"
             data-mcp-ask-ai-feedback-layer
             hidden></div>

        <div class="visually-hidden" id="mcp-ask-ai-sr" aria-live="polite" aria-atomic="false"></div>

        <div class="mcp-ask-ai__composer">
            <div class="mcp-ask-ai__input-wrap">
                <label class="visually-hidden" for="mcp-ask-ai-input">Ask a question</label>
                <textarea id="mcp-ask-ai-input"
                          class="mcp-ask-ai__input"
                          rows="2"
                          maxlength="2000"
                          placeholder="Ask about orders, jobs, invoices..."
                          aria-describedby="mcp-ask-ai-counter"
                          data-mcp-ask-ai-focus></textarea>
                <div class="mcp-ask-ai__composer-controls">
                    <div class="mcp-ask-ai__composer-pills">
                        <x-mcp.dropdown
                            variant="chip"
                            icon="fa-layer-group"
                            menu-id="mcp-ask-ai-scope-menu"
                            menu-label="Select context"
                            class="mcp-ask-ai__scope"
                            data-mcp-ask-scope>
                            <x-slot:label>All records</x-slot:label>
                        </x-mcp.dropdown>
                        <x-mcp.dropdown
                            variant="chip"
                            icon="fa-comment"
                            menu-id="mcp-ask-ai-mode-menu"
                            menu-label="Mode"
                            align="start"
                            class="mcp-ask-ai__mode"
                            data-mcp-ask-mode>
                            <x-slot:label>Ask</x-slot:label>
                            <x-mcp.dropdown.option checked data-mcp-ask-mode-option="ask">Ask</x-mcp.dropdown.option>
                            <x-mcp.dropdown.option disabled icon="fa-bolt" data-mcp-ask-mode-option="act">Act — Coming soon</x-mcp.dropdown.option>
                        </x-mcp.dropdown>
                    </div>
                    <div class="mcp-ask-ai__composer-actions">
                        <span class="mcp-ask-ai__counter" id="mcp-ask-ai-counter" aria-live="polite" hidden>0/2000</span>
                        <button type="button" class="mcp-ask-ai__send" aria-label="Send question">
                            <i class="fa fa-arrow-up" aria-hidden="true"></i>
                            <span class="visually-hidden">Send</span>
                        </button>
                    </div>
                </div>
            </div>
            <div class="mcp-ask-ai__composer-foot">
                <p class="mcp-ask-ai__disclaimer">Answers can be wrong. Check important figures.</p>
                {{-- The person's own monthly assistant spend; painted by mcp-ask-ai-usage-meter.js. --}}
                <div class="mcp-ask-ai__usage" data-mcp-ask-usage hidden></div>
            </div>
            <div class="mcp-ask-ai__meta-note" hidden>
                <span class="mcp-ask-ai__mode-label"></span>
            </div>
        </div>
    </div>
</aside>
