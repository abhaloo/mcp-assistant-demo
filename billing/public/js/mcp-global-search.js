/*
 * MCP Global Search controller — vanilla IIFE, no build step required.
 * AI answers live in the Ask AI dock (mcp-ask-ai.js); Ctrl+K seeds that panel.
 */
(function () {
    'use strict';

    const WRAP_SEL = '.mcp-topbar__search';
    const INPUT_ID = 'mcp-global-search';
    const PANEL_SEL = '.mcp-search-panel';
    const PREFLIGHT_SEL = '.mcp-search-panel__preflight';
    const LOADING_SEL = '[data-mcp-search-loading]';
    const BACKDROP_SEL = '.mcp-search-backdrop';
    const KBD_SEL = '[data-mcp-search-kbd]';
    const OPTION_SEL = '.mcp-search-result[role="option"], .mcp-search-ask[role="option"], .mcp-search-result-ask[role="option"]';
    const MIN_QUERY = 2;

    let wrap = null;
    let input = null;
    let trigger = null;
    let clearBtn = null;
    let panel = null;
    let preflight = null;
    let loading = null;
    let backdrop = null;
    let options = [];
    let activeIndex = -1;
    let panelOpen = false;
    let escArmed = false;
    let booted = false;

    const isMobile = () => window.matchMedia('(max-width: 767.98px)').matches;
    const isDesktop = () => !isMobile();

    function cacheDom() {
        wrap = document.querySelector(WRAP_SEL);
        if (!wrap) {
            input = trigger = clearBtn = panel = preflight = loading = backdrop = null;
            options = [];
            return false;
        }
        input = document.getElementById(INPUT_ID);
        trigger = wrap.querySelector('.mcp-topbar__search-trigger');
        clearBtn = wrap.querySelector('.mcp-topbar__search-clear');
        panel = wrap.querySelector(PANEL_SEL);
        preflight = wrap.querySelector(PREFLIGHT_SEL);
        loading = wrap.querySelector(LOADING_SEL);
        backdrop = wrap.querySelector(BACKDROP_SEL);
        refreshOptions();
        return !!input;
    }

    function refreshOptions() {
        options = panel ? Array.from(panel.querySelectorAll(OPTION_SEL)) : [];
        if (activeIndex >= options.length) {
            activeIndex = options.length - 1;
        }
    }

    function queryLength() {
        return (input?.value || '').trim().length;
    }

    function currentQuery() {
        return (input?.value || '').trim();
    }

    function hasRenderableResults() {
        if (!panel) return false;
        return !!panel.querySelector('#mcp-search-listbox, .mcp-search-panel__empty');
    }

    function shouldShowPanel() {
        if (!panel) return false;
        return document.activeElement === input || wrap.contains(document.activeElement);
    }

    function setPanelOpen(open) {
        if (!wrap || !input) return;
        panelOpen = open;
        wrap.classList.toggle('is-panel-open', open);
        input.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (!open) {
            clearActiveOption();
            escArmed = false;
        }
        syncBackdrop();
    }

    function syncPreflight() {
        if (!preflight) return;
        const show = panelOpen && queryLength() < MIN_QUERY;
        preflight.hidden = !show;
    }

    function syncPendingSearch() {
        if (!wrap) return;
        const pending = queryLength() >= MIN_QUERY && !hasRenderableResults();
        wrap.classList.toggle('is-pending-search', pending);
        syncLoading();
    }

    function syncLoading() {
        if (!wrap || !loading) return;
        const show = wrap.classList.contains('is-pending-search')
            || wrap.classList.contains('is-searching');
        loading.hidden = !show;
    }

    function syncBackdrop() {
        if (!backdrop) return;
        const show = isMobile()
            && wrap.classList.contains('is-open')
            && panelOpen;
        backdrop.hidden = !show;
    }

    function syncKbdLabel() {
        const kbd = wrap?.querySelector(KBD_SEL);
        if (!kbd) return;
        const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform);
        kbd.textContent = isMac ? '⌘K' : 'Ctrl+K';
    }

    function syncPanel() {
        refreshOptions();
        setPanelOpen(shouldShowPanel());
        syncPreflight();
        syncPendingSearch();
        syncLoading();
    }

    function clearActiveOption() {
        activeIndex = -1;
        input?.setAttribute('aria-activedescendant', '');
        options.forEach(opt => {
            opt.classList.remove('is-active');
            opt.setAttribute('aria-selected', 'false');
        });
    }

    function setActiveOption(index) {
        if (!options.length) {
            clearActiveOption();
            return;
        }
        if (index < 0) index = options.length - 1;
        if (index >= options.length) index = 0;
        activeIndex = index;

        options.forEach((opt, i) => {
            const active = i === activeIndex;
            opt.classList.toggle('is-active', active);
            opt.setAttribute('aria-selected', active ? 'true' : 'false');
        });

        const active = options[activeIndex];
        input.setAttribute('aria-activedescendant', active.id);
        active.scrollIntoView({ block: 'nearest' });
    }

    function navigateActive(delta) {
        if (!options.length) return;
        if (activeIndex === -1) {
            setActiveOption(delta > 0 ? 0 : options.length - 1);
            return;
        }
        setActiveOption(activeIndex + delta);
    }

    function isAskOption(el) {
        return el?.matches?.('.mcp-search-ask[role="option"]');
    }

    // Task B4 / R1: the per-row "Ask AI about this" action — reuses the same
    // role="option" pattern as the generic ask row above.
    function isRowAskOption(el) {
        return el?.matches?.('.mcp-search-result-ask[role="option"]');
    }

    /**
     * Untrusted record_context — identifiers only. Billing re-resolves and
     * re-authorizes every reference server-side before anything reaches RAG
     * (see AiSearchController -> RecordContextResolver); nothing sent here is
     * trusted, so no other data (labels, totals, permissions) belongs in it.
     */
    function buildRecordContext(el, origin) {
        const resourceType = el?.dataset?.recordResourceType;
        const recordId = el?.dataset?.recordId;
        const query = currentQuery();
        if (!resourceType || !recordId || query.length < MIN_QUERY) return null;
        return {
            version: 1,
            origin,
            query,
            references: [{ resource_type: resourceType, record_id: String(recordId) }],
        };
    }

    /**
     * Task B7 / R1 — Billing-authorized DISPLAY label for the composer chip
     * (e.g. "E8520 (Quotation)"), read from the already-rendered row's own
     * data attribute. This is a display-only companion to buildRecordContext()
     * and is NEVER merged into it — the untrusted wire payload stays
     * identifiers-only (see the test asserting Object.keys never includes it).
     */
    function recordContextLabel(el) {
        return el?.dataset?.recordLabel || null;
    }

    function openAskAi(seed, recordContext, label) {
        const searchWrap = document.querySelector(WRAP_SEL);
        if (searchWrap?.dataset.askAiAvailable !== '1') return;
        if (window.AskAi?.open) {
            window.AskAi.open(seed, recordContext || null, label || null);
        }
        setPanelOpen(false);
    }

    function navigateToActive() {
        const target = options[activeIndex] || options[0];
        if (!target) return;

        if (isAskOption(target)) {
            openAskAi(currentQuery(), buildRecordContext(target, 'global_search'), recordContextLabel(target));
            return;
        }

        if (isRowAskOption(target)) {
            openAskAi(
                currentQuery(),
                buildRecordContext(target, 'explicit_selection'),
                recordContextLabel(target)
            );
            return;
        }

        const url = target.getAttribute('href');
        if (url && url !== '#') {
            window.location.href = url;
        }
    }

    function clearInput() {
        if (!input) return;
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        clearActiveOption();
        wrap?.classList.remove('is-pending-search');
        setPanelOpen(false);
        escArmed = false;
    }

    function closeMobileSearch() {
        if (!wrap) return;
        wrap.classList.remove('is-open');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
        setPanelOpen(false);
    }

    function escapeHtml(text) {
        if (window.McpAiCore?.escapeHtml) {
            return window.McpAiCore.escapeHtml(text);
        }
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function formatParagraphs(text) {
        return escapeHtml(text).split(/\n{2,}/).map(p => '<p>' + p.replace(/\n/g, '<br>') + '</p>').join('');
    }

    function onInputKeydown(e) {
        if (e.target.id !== INPUT_ID) return;

        switch (e.key) {
            case 'ArrowDown':
                if (panelOpen && options.length) {
                    e.preventDefault();
                    navigateActive(1);
                }
                break;
            case 'ArrowUp':
                if (panelOpen && options.length) {
                    e.preventDefault();
                    navigateActive(-1);
                }
                break;
            case 'Enter':
                if (panelOpen && options.length) {
                    e.preventDefault();
                    navigateToActive();
                }
                break;
            case 'Escape':
                e.preventDefault();
                if (panelOpen) {
                    setPanelOpen(false);
                    escArmed = true;
                } else if (escArmed || input.value) {
                    clearInput();
                    input.blur();
                    if (isMobile() && wrap.classList.contains('is-open')) {
                        closeMobileSearch();
                    }
                    escArmed = false;
                } else if (isMobile() && wrap.classList.contains('is-open')) {
                    closeMobileSearch();
                }
                break;
            case 'Tab':
                setPanelOpen(false);
                break;
            default:
                escArmed = false;
                break;
        }
    }

    function onDocumentKeydown(e) {
        const mod = e.metaKey || e.ctrlKey;
        if (!mod || e.key.toLowerCase() !== 'k') return;
        if (!isDesktop()) return;
        if (!document.getElementById(INPUT_ID)) return;
        e.preventDefault();
        document.getElementById(INPUT_ID).focus();
        document.getElementById(INPUT_ID).select();
        cacheDom();
        syncPanel();
    }

    function onDocumentClick(e) {
        if (!wrap) return;

        if (clearBtn && e.target.closest('.mcp-topbar__search-clear')) {
            clearInput();
            input.focus();
            return;
        }

        if (backdrop && e.target === backdrop) {
            closeMobileSearch();
            input.blur();
            return;
        }

        if (trigger && e.target.closest('.mcp-topbar__search-trigger')) {
            const open = wrap.classList.toggle('is-open');
            trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) {
                setTimeout(() => input.focus(), 0);
            } else {
                setPanelOpen(false);
            }
            return;
        }

        const askOption = e.target.closest('.mcp-search-ask[role="option"]');
        if (askOption && wrap.contains(askOption)) {
            e.preventDefault();
            openAskAi(currentQuery(), buildRecordContext(askOption, 'global_search'), recordContextLabel(askOption));
            input?.focus();
            return;
        }

        const rowAskOption = e.target.closest('.mcp-search-result-ask[role="option"]');
        if (rowAskOption && wrap.contains(rowAskOption)) {
            e.preventDefault();
            openAskAi(
                currentQuery(),
                buildRecordContext(rowAskOption, 'explicit_selection'),
                recordContextLabel(rowAskOption)
            );
            input?.focus();
            return;
        }

        if (wrap.contains(e.target)) return;

        setPanelOpen(false);
        if (isMobile() && wrap.classList.contains('is-open')) {
            closeMobileSearch();
        }
    }

    function onFocusIn(e) {
        if (e.target.id !== INPUT_ID) return;
        cacheDom();
        syncPanel();
    }

    function onFocusOut(e) {
        if (e.target.id !== INPUT_ID) return;
        setTimeout(() => {
            if (document.activeElement === input || wrap?.contains(document.activeElement)) return;
            setPanelOpen(false);
            if (isMobile() && wrap?.classList.contains('is-open') && queryLength() === 0) {
                closeMobileSearch();
            }
        }, 0);
    }

    function onInput(e) {
        if (e.target.id !== INPUT_ID) return;
        escArmed = false;
        cacheDom();
        syncPanel();
    }

    function boot() {
        if (booted) {
            cacheDom();
            syncKbdLabel();
            syncPanel();
            return;
        }
        booted = true;

        document.addEventListener('keydown', onDocumentKeydown);
        document.addEventListener('keydown', onInputKeydown);
        document.addEventListener('click', onDocumentClick);
        document.addEventListener('focusin', onFocusIn);
        document.addEventListener('focusout', onFocusOut);
        document.addEventListener('input', onInput);
        document.addEventListener('livewire:init', () => {
            if (window.Livewire?.hook) {
                window.Livewire.hook('morph.updated', () => {
                    cacheDom();
                    wrap?.classList.remove('is-pending-search');
                    syncPanel();
                });
            }
        });

        cacheDom();
        syncKbdLabel();
        syncPanel();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    document.addEventListener('livewire:init', boot);

    if (typeof module !== 'undefined' && module.exports) {
        const core = typeof window !== 'undefined' && window.McpAiCore
            ? window.McpAiCore
            : require('../../public/js/mcp-ai-core.js');
        module.exports = {
            parseSseEvents: core.parseSseEvents,
            mapPreStreamError: core.mapPreStreamError,
            escapeHtml,
            formatParagraphs,
            openAskAi,
            buildRecordContext,
            recordContextLabel,
            isAskOption,
            isRowAskOption,
        };
    }
})();
