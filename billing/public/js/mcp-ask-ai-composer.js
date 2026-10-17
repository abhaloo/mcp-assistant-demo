/*
 * MCP Ask AI — composer scope and mode.
 *
 * The scope selector decides which existing context the next question carries:
 * nothing (All records), the page the reader is on, or a record they picked
 * for one turn. It adds no wire field; it only chooses whether the panel sends
 * the page_context or record_context it already had. The mode selector shows
 * Ask and a disabled Act; it sends nothing.
 */
(function () {
    'use strict';

    const ALL = 'all_records';
    const PAGE = 'current_page';
    const RECORD = 'selected_record';
    const NO_CONTEXT_REASON = 'No page context on this page';

    const state = { scope: ALL, mode: 'ask', routeContextKey: null, record: null };
    let root = null;
    let trigger = null;
    let labelEl = null;
    let menu = null;
    let contextSubject = () => null;

    const registry = () => window.McpPageContext?.PageContextRegistry || null;
    const snapshot = () => registry()?.captureForSubmit?.() || null;
    const SINGULAR = { job: 'Job', work_order: 'Job', invoice: 'Invoice', customer: 'Customer', customer_order: 'Customer order' };

    // Both snapshot shapes (v1 route-based, v2 profile-based) get one key and one title.
    function records(snap) {
        if (Array.isArray(snap?.records)) return snap.records;
        return (snap?.record_ids || []).map((id) => ({ id: String(id), resource_type: snap.resource_type }));
    }

    function routeKey(snap) {
        if (!snap) return null;
        if (snap.context_id) return snap.context_id;
        return (snap.profile || 'page') + ':' + records(snap).map((r) => r.id).join(',');
    }

    function pageTitle(snap) {
        if (!snap) return null;
        const list = records(snap);
        const single = list.length === 1 && (snap.version === 2 || /\.show$/.test(snap.route || ''));
        if (single) return (SINGULAR[list[0].resource_type] || 'Record') + ' ' + list[0].id;
        return contextSubject(snap) || 'This page';
    }

    function triggerLabel(snap) {
        if (state.scope === RECORD && state.record) return 'Selected record: ' + state.record.label;
        if (state.scope === PAGE) return 'Current page: ' + pageTitle(snap);
        return 'All records';
    }

    function render(snap = snapshot()) {
        if (!labelEl || !menu) return;
        labelEl.textContent = triggerLabel(snap);
        const items = [
            window.McpDropdown.option({ text: 'All records', checked: state.scope === ALL, onChoose: () => choose(ALL, snap) }),
            window.McpDropdown.option({
                text: 'Current page: ' + (pageTitle(snap) || ''),
                checked: state.scope === PAGE, disabled: !snap, reason: snap ? null : NO_CONTEXT_REASON,
                onChoose: () => choose(PAGE, snap),
            }),
        ];
        if (state.record) {
            items.push(window.McpDropdown.option({
                text: 'Selected record: ' + state.record.label,
                checked: state.scope === RECORD,
                onChoose: () => choose(RECORD, snap),
            }));
        }
        menu.replaceChildren(...items);
    }

    function choose(scope, snap) {
        state.scope = scope;
        state.routeContextKey = scope === PAGE ? routeKey(snap) : null;
        if (scope !== RECORD) state.record = null;
        window.McpDropdown.close(root.querySelector('[data-mcp-ask-scope][data-mcp-dropdown]'));
        render(snap);
    }

    // Route change: a current-page choice belongs to the page it was made on.
    function sync() {
        const snap = snapshot();
        if (state.scope === PAGE && routeKey(snap) !== state.routeContextKey) {
            state.scope = ALL;
            state.routeContextKey = null;
        }
        render(snap);
    }

    function contextForSend() {
        const snap = snapshot();
        if (state.scope === RECORD && state.record) {
            const recordContext = state.record.recordContext;
            state.record = null;
            state.scope = ALL;
            render(snap);
            return { pageContext: null, recordContext };
        }
        if (state.scope === PAGE && snap && routeKey(snap) === state.routeContextKey) {
            return { pageContext: snap, recordContext: null };
        }
        if (state.scope === PAGE) sync();
        return { pageContext: null, recordContext: null };
    }

    function stageRecord(recordContext, label) {
        if (!recordContext) return;
        const ref = recordContext.references?.[0];
        state.record = { recordContext, label: label || (ref ? ref.resource_type + ' #' + ref.record_id : 'record') };
        state.scope = RECORD;
        state.routeContextKey = null;
        render();
    }

    function reset() {
        state.scope = ALL;
        state.routeContextKey = null;
        state.record = null;
        render();
    }

    function init(rootEl, deps = {}) {
        if (typeof window.McpDropdown?.bind !== 'function'
            || typeof window.McpDropdown?.option !== 'function'
            || typeof window.McpDropdown?.close !== 'function') {
            throw new Error('McpDropdown is required');
        }
        root = rootEl;
        contextSubject = typeof deps.contextSubject === 'function' ? deps.contextSubject : () => null;
        const scopeEl = root?.querySelector('[data-mcp-ask-scope]') || null;
        trigger = scopeEl?.querySelector('[data-mcp-dropdown-trigger]') || null;
        labelEl = scopeEl?.querySelector('.mcp-dropdown__label') || null;
        menu = scopeEl?.querySelector('[data-mcp-dropdown-menu]') || null;
        if (!trigger || !menu) return;
        window.McpDropdown.bind(root);
        scopeEl.addEventListener('mcp-dropdown:beforeopen', () => render());
        registry()?.subscribe?.(sync);
        render();
    }

    window.McpAskAiComposer = { init, contextForSend, stageRecord, reset, scope: () => state.scope, mode: () => state.mode };
})();
