import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
    parseSseEvents,
    mapPreStreamError,
} from '../../public/js/mcp-ai-core.js';
import {
    escapeHtml,
    formatParagraphs,
    openAskAi,
    buildRecordContext,
    recordContextLabel,
    isAskOption,
    isRowAskOption,
} from '../../public/js/mcp-global-search.js';

describe('parseSseEvents', () => {
    it('returns parsed events and the trailing partial as rest', () => {
        const buffer =
            'event: token\ndata: {"d":"Hello"}\n\n' +
            'event: token\ndata: {"d":" world"}\n\n' +
            'event: done\ndata: {"model":"x"}'; // no terminating blank line -> partial

        const { events, rest } = parseSseEvents(buffer);

        expect(events).toEqual([
            { event: 'token', data: '{"d":"Hello"}' },
            { event: 'token', data: '{"d":" world"}' },
        ]);
        // The incomplete final block is preserved for the next chunk.
        expect(rest).toBe('event: done\ndata: {"model":"x"}');
    });

    it('skips heartbeat comment (":") blocks', () => {
        const buffer = ': ping\n\nevent: token\ndata: {"d":"hi"}\n\n';

        const { events, rest } = parseSseEvents(buffer);

        expect(events).toEqual([{ event: 'token', data: '{"d":"hi"}' }]);
        expect(rest).toBe('');
    });

    it('joins multiple data: lines within one event with newlines', () => {
        const buffer = 'event: message\ndata: line one\ndata: line two\n\n';

        const { events } = parseSseEvents(buffer);

        expect(events).toHaveLength(1);
        expect(events[0].event).toBe('message');
        expect(events[0].data).toBe('line one\nline two');
    });

    it('defaults the event name to "message" when only data: is present', () => {
        const { events } = parseSseEvents('data: bare\n\n');
        expect(events).toEqual([{ event: 'message', data: 'bare' }]);
    });
});

describe('escapeHtml', () => {
    it('neutralizes a <script> payload (XSS)', () => {
        expect(escapeHtml('<script>alert(1)</script>')).toBe(
            '&lt;script&gt;alert(1)&lt;/script&gt;'
        );
    });

    it('escapes ampersands and double quotes', () => {
        expect(escapeHtml('Tom & "Jerry"')).toBe('Tom &amp; &quot;Jerry&quot;');
    });

    it('coerces non-string input to string', () => {
        expect(escapeHtml(42)).toBe('42');
    });
});

describe('formatParagraphs', () => {
    it('splits on a blank line into separate <p> elements', () => {
        expect(formatParagraphs('one\n\ntwo')).toBe('<p>one</p><p>two</p>');
    });

    it('converts a single newline into <br> within a paragraph', () => {
        expect(formatParagraphs('line a\nline b')).toBe('<p>line a<br>line b</p>');
    });

    it('escapes content before wrapping (no raw HTML leaks)', () => {
        expect(formatParagraphs('<b>x</b>\n\n<i>y</i>')).toBe(
            '<p>&lt;b&gt;x&lt;/b&gt;</p><p>&lt;i&gt;y&lt;/i&gt;</p>'
        );
    });
});

describe('mapPreStreamError', () => {
    it('maps 419 and 401 to the session-expired message', () => {
        expect(mapPreStreamError(419)).toBe('Session expired — refresh to continue');
        expect(mapPreStreamError(401)).toBe('Session expired — refresh to continue');
    });

    it('maps 429 to the rate-limit message', () => {
        expect(mapPreStreamError(429)).toBe(
            "You're asking quickly — try again in a moment"
        );
    });

    it('maps 402 to a budget message using used/cap/reset_date from the body', () => {
        expect(
            mapPreStreamError(402, { used: 5, cap: 10, reset_date: '2026-07-01' })
        ).toBe('Monthly AI budget reached (5 / 10). Resets 2026-07-01.');
    });

    it('falls back to placeholders when the 402 body is missing fields', () => {
        expect(mapPreStreamError(402, {})).toBe(
            'Monthly AI budget reached (? / ?). Resets next month.'
        );
        expect(mapPreStreamError(402, null)).toBe(
            'Monthly AI budget reached (? / ?). Resets next month.'
        );
    });

    it('mixes real and placeholder fields for a partially-populated 402 body', () => {
        expect(mapPreStreamError(402, { used: 5 })).toBe(
            'Monthly AI budget reached (5 / ?). Resets next month.'
        );
    });

    it('maps 503 to the temporarily-unavailable message', () => {
        expect(mapPreStreamError(503)).toBe('AI search temporarily unavailable');
    });

    it('returns the generic message for any other status', () => {
        expect(mapPreStreamError(500)).toBe('Something went wrong. Please try again.');
    });
});

describe('status events', () => {
    it('parses a status frame with phase + label', () => {
        const buf =
            'event: status\ndata: {"phase":"running_query","label":"Searching the database…"}\n\n';
        const { events } = parseSseEvents(buf);
        expect(events).toHaveLength(1);
        expect(events[0].event).toBe('status');
        expect(JSON.parse(events[0].data).label).toBe('Searching the database…');
    });
});

describe('Ctrl+K Ask AI integration', () => {
    it('openAskAi delegates to AskAi.open with the seed query and no record context', () => {
        window.AskAi = { open: vi.fn() };
        document.body.innerHTML = `
            <div class="mcp-topbar__search" data-rag-enabled="1" data-rag-available="1" data-ask-ai-available="1"></div>
        `;

        openAskAi('pricing for banners');

        expect(window.AskAi.open).toHaveBeenCalledWith('pricing for banners', null, null);
    });

    it('openAskAi forwards a record context when given one', () => {
        window.AskAi = { open: vi.fn() };
        document.body.innerHTML = `
            <div class="mcp-topbar__search" data-rag-enabled="1" data-rag-available="1" data-ask-ai-available="1"></div>
        `;
        const recordContext = {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        };

        openAskAi('E8520', recordContext);

        expect(window.AskAi.open).toHaveBeenCalledWith('E8520', recordContext, null);
    });

    it('openAskAi is a no-op when Ask AI is unavailable', () => {
        window.AskAi = { open: vi.fn() };
        document.body.innerHTML = `
            <div class="mcp-topbar__search" data-rag-enabled="0" data-rag-available="0" data-ask-ai-available="0"></div>
        `;

        openAskAi('pricing for banners');

        expect(window.AskAi.open).not.toHaveBeenCalled();
    });
});

describe('Task B4 / R1 — record_context selection semantics', () => {
    // buildRecordContext() reads the CURRENT query via the module's internal
    // `input` reference, which is only (re)captured by cacheDom() — wired to
    // the real 'input' DOM event. The wrapper (.mcp-topbar__search) must be
    // present too: cacheDom() bails out (leaving `input` null) when it can't
    // find it, matching the real markup structure.
    function makeInput(query = 'E8520') {
        document.body.innerHTML = `
            <div class="mcp-topbar__search" data-rag-enabled="1" data-rag-available="1" data-ask-ai-available="1">
                <input id="mcp-global-search" value="${query}">
            </div>
        `;
        const input = document.getElementById('mcp-global-search');
        input.dispatchEvent(new Event('input', { bubbles: true }));

        return input;
    }

    it('buildRecordContext builds identifiers-only untrusted context from data attributes', () => {
        makeInput();
        const el = document.createElement('button');
        el.dataset.recordResourceType = 'quotation';
        el.dataset.recordId = '1234';

        const context = buildRecordContext(el, 'explicit_selection');

        expect(context).toEqual({
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        });
    });

    it('buildRecordContext never carries labels, totals, or any other client-side data', () => {
        makeInput();
        const el = document.createElement('button');
        el.dataset.recordResourceType = 'quotation';
        el.dataset.recordId = '1234';
        // Any other dataset attribute (e.g. a title/label an author might be
        // tempted to add later) must never leak into the untrusted payload —
        // the shape is exactly {version, origin, query, references}.
        el.dataset.recordLabel = 'E8520';
        el.dataset.recordTotal = '9999.99';

        const context = buildRecordContext(el, 'global_search');

        expect(Object.keys(context)).toEqual(['version', 'origin', 'query', 'references']);
        expect(Object.keys(context.references[0])).toEqual(['resource_type', 'record_id']);
    });

    it('buildRecordContext returns null when the element has no record data attributes', () => {
        makeInput();
        const el = document.createElement('button');

        expect(buildRecordContext(el, 'global_search')).toBeNull();
    });

    it('buildRecordContext returns null when the query is below the minimum length', () => {
        makeInput('x');
        const el = document.createElement('button');
        el.dataset.recordResourceType = 'quotation';
        el.dataset.recordId = '1234';

        expect(buildRecordContext(el, 'global_search')).toBeNull();
    });

    it('isAskOption/isRowAskOption distinguish the generic row from a per-result row', () => {
        const askEl = document.createElement('button');
        askEl.setAttribute('role', 'option');
        askEl.className = 'mcp-search-ask';

        const rowEl = document.createElement('button');
        rowEl.setAttribute('role', 'option');
        rowEl.className = 'mcp-search-result-ask';

        expect(isAskOption(askEl)).toBe(true);
        expect(isRowAskOption(askEl)).toBe(false);
        expect(isAskOption(rowEl)).toBe(false);
        expect(isRowAskOption(rowEl)).toBe(true);
    });

    it('a row-specific ask click always attaches its own record (explicit_selection), never the generic query alone', () => {
        window.AskAi = { open: vi.fn() };
        makeInput();
        const rowEl = document.createElement('button');
        rowEl.dataset.recordResourceType = 'quotation';
        rowEl.dataset.recordId = '1234';

        openAskAi('E8520', buildRecordContext(rowEl, 'explicit_selection'));

        expect(window.AskAi.open).toHaveBeenCalledWith('E8520', {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        }, null);
    });
});

describe('Task B7 / R1 + R7 — record-context label passthrough (chip text source)', () => {
    it('recordContextLabel reads data-record-label without it ever entering buildRecordContext', () => {
        const el = document.createElement('button');
        el.dataset.recordResourceType = 'quotation';
        el.dataset.recordId = '1234';
        el.dataset.recordLabel = 'E8520 (Quotation)';

        expect(recordContextLabel(el)).toBe('E8520 (Quotation)');
        expect(Object.keys(buildRecordContext(el, 'global_search'))).not.toContain('label');
    });

    it('recordContextLabel returns null when no label attribute is present', () => {
        const el = document.createElement('button');
        expect(recordContextLabel(el)).toBeNull();
    });

    it('openAskAi forwards the label as a third argument to AskAi.open', () => {
        window.AskAi = { open: vi.fn() };
        document.body.innerHTML = `
            <div class="mcp-topbar__search" data-rag-enabled="1" data-rag-available="1" data-ask-ai-available="1"></div>
        `;
        const recordContext = {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        };

        openAskAi('E8520', recordContext, 'E8520 (Quotation)');

        expect(window.AskAi.open).toHaveBeenCalledWith('E8520', recordContext, 'E8520 (Quotation)');
    });
});

describe('Task B7 / R7 — keyboard and click activation on Global Search ask rows', () => {
    function mountSearchPanel() {
        document.body.innerHTML = `
            <div class="mcp-topbar__search" data-rag-enabled="1" data-rag-available="1" data-ask-ai-available="1">
                <input id="mcp-global-search" value="E8520">
                <div class="mcp-search-panel">
                    <ul id="mcp-search-listbox">
                        <li class="mcp-search-result-row">
                            <a id="opt-0" class="mcp-search-result" role="option" href="/quotations/1234">E8520</a>
                            <button id="opt-0-ask" class="mcp-search-result-ask" role="option"
                                    data-record-resource-type="quotation" data-record-id="1234"
                                    data-record-label="E8520 (Quotation)"></button>
                        </li>
                    </ul>
                </div>
            </div>
        `;
    }

    beforeEach(async () => {
        window.AskAi = { open: vi.fn() };
        mountSearchPanel();
        // jsdom does not implement scrollIntoView; roving-tabindex navigation calls it.
        Element.prototype.scrollIntoView = vi.fn();
        vi.resetModules();
        await import('../../public/js/mcp-global-search.js');
        document.getElementById('mcp-global-search').focus();
        document.getElementById('mcp-global-search').dispatchEvent(new Event('input', { bubbles: true }));
    });

    afterEach(() => {
        document.body.innerHTML = '';
        delete window.AskAi;
    });

    const expectedCall = [
        'E8520',
        {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        },
        'E8520 (Quotation)',
    ];

    it('click activation on the row-specific ask button opens Ask AI with the record context + label', () => {
        document.getElementById('opt-0-ask').click();
        expect(window.AskAi.open).toHaveBeenCalledWith(...expectedCall);
    });

    it('keyboard activation (ArrowDown, ArrowDown, Enter) opens Ask AI the same way as a click', () => {
        const input = document.getElementById('mcp-global-search');
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));

        expect(window.AskAi.open).toHaveBeenCalledWith(...expectedCall);
    });
});
