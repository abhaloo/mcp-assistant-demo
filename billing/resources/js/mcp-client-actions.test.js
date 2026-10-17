import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { escapeHtml } from '../../public/js/mcp-ai-core.js';

function mountAskAiPanel() {
    document.body.className = 'mcp-shifted';
    document.body.innerHTML = `
        <meta name="csrf-token" content="test-csrf">
        <meta name="user-id" content="test-user">
        <div id="invoice-totals-card" class="card">Invoice Totals</div>
        <div id="target-tab" class="tab-pane" role="tabpanel"></div>
        <button id="tab-btn" data-bs-target="#target-tab" data-bs-toggle="tab">Tab</button>
        <div class="mcp-ask-ai-backdrop" hidden></div>
        <aside class="mcp-ask-ai-sidebar" id="mcpAskAiSidebar" hidden aria-hidden="true">
            <div class="mcp-ask-ai-sidebar__body">
                <div id="mcp-ask-ai-root"
                     data-stream-url="/ai-search/stream"
                     data-feedback-url="/ai-search/feedback"
                     data-cancel-url="/ai-search/cancel"
                     data-ask-ai-available="1"
                     data-rag-enabled="1">
                    <div class="mcp-ask-ai__thread"></div>
                    <div class="mcp-ask-ai__feedback-layer" data-mcp-ask-ai-feedback-layer hidden></div>
                    <div class="mcp-ask-ai__composer">
                        <div class="mcp-ask-ai__input-wrap">
                            <div data-mcp-ask-ai-context hidden></div>
                            <textarea class="mcp-ask-ai__input" maxlength="2000" data-mcp-ask-ai-focus></textarea>
                            <span class="mcp-ask-ai__counter">0/2000</span>
                            <button type="button" class="mcp-ask-ai__send">Send</button>
                        </div>
                    </div>
                    <button type="button" data-mcp-ask-new aria-label="New chat">New</button>
                    <span class="mcp-ask-ai__mode-label"></span>
                    <div id="mcp-ask-ai-sr"></div>
                </div>
            </div>
        </aside>
    `;
}

describe('Cycle UX-7B: Frontend DOM Element Pulse Highlighter & Tab Synchronizer', () => {
    let sendQuestionMock;

    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();

        window.matchMedia = vi.fn((query) => ({
            matches: false,
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));

        sendQuestionMock = vi.fn();

        window.McpAiCore = {
            sendQuestion: sendQuestionMock,
            renderMarkdown: (t) => '<p>' + escapeHtml(t) + '</p>',
            escapeHtml,
            sha256Hex: vi.fn().mockResolvedValue('deadbeef'),
            isSameOriginAppPath: (url) => typeof url === 'string' && url.startsWith('/') && !url.startsWith('//'),
            scheduleSrBatch: () => ({ start: () => {}, flush: () => {}, stop: () => {} }),
        };

        window.McpPageContext = {
            PageContextRegistry: {
                captureForSubmit: vi.fn().mockReturnValue(null),
                suppress: vi.fn(),
            },
        };

        global.fetch = vi.fn().mockResolvedValue({ ok: true });

        mountAskAiPanel();
        vi.resetModules();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
        await import('../../public/js/mcp-ask-ai-persist.js');
        await import('../../public/js/mcp-ask-ai-stream-ui.js');
        await import('../../public/js/mcp-ask-stream-client.js');
        await import('../../public/js/mcp-ask-ai-feedback.js');
        await import('../../public/js/mcp-ask-ai.js');
    });

    afterEach(() => {
        document.body.innerHTML = '';
        document.body.className = '';
        vi.restoreAllMocks();
    });

    it('test_client_action_pulse_highlight_and_tab_switch_with_bounded_regex', async () => {
        const actionListener = vi.fn();
        window.addEventListener('mcp:action', actionListener);

        const targetCard = document.getElementById('invoice-totals-card');
        targetCard.scrollIntoView = vi.fn();

        const tabBtn = document.getElementById('tab-btn');
        tabBtn.click = vi.fn();

        sendQuestionMock.mockImplementation(async (handlers) => {
            handlers.onClientAction?.({
                type: 'highlight_element',
                selector: '#invoice-totals-card',
                switch_tab: '#target-tab',
            });
            handlers.onToken?.('Here are the invoice totals highlighted on your screen.');
            handlers.onDone?.({ thread_id: 'th-action' });
            return { terminal: 'done' };
        });

        await window.AskAi.sendTurn('Highlight the totals');

        // CustomEvent dispatched
        expect(actionListener).toHaveBeenCalledTimes(1);
        expect(actionListener.mock.calls[0][0].detail.selector).toBe('#invoice-totals-card');

        // Pulse highlighter class added
        expect(targetCard.classList.contains('mcp-highlight-pulse')).toBe(true);
        expect(targetCard.scrollIntoView).toHaveBeenCalled();

        // Tab switch clicked
        expect(tabBtn.click).toHaveBeenCalled();
    });

    it('fails closed and ignores unsafe DOM selectors without throwing unhandled errors', async () => {
        const unsafeSelectors = [
            'body',
            '<script>alert(1)</script>',
            'input[type="password"]',
            'div.leak',
            '#very-long-id-that-exceeds-sixty-four-characters-and-should-fail-the-bounded-regex-guard-completely',
            'javascript:void(0)',
        ];

        for (const unsafe of unsafeSelectors) {
            sendQuestionMock.mockImplementation(async (handlers) => {
                handlers.onClientAction?.({
                    type: 'highlight_element',
                    selector: unsafe,
                });
                handlers.onDone?.({ thread_id: 'th-unsafe' });
                return { terminal: 'done' };
            });

            // Must NOT throw
            await expect(window.AskAi.sendTurn('Test unsafe selector')).resolves.not.toThrow();
        }
    });
});
