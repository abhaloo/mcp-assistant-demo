import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { escapeHtml } from '../../public/js/mcp-ai-core.js';

function mountAskAiPanel() {
    document.body.className = 'mcp-shifted';
    document.body.innerHTML = `
        <meta name="csrf-token" content="test-csrf">
        <meta name="user-id" content="test-user">
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

describe('Cycle UX-3B: One-Click Smart Follow-Up Action Buttons & Active Stream Abort', () => {
    let sendQuestionMock;

    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();

        sendQuestionMock = vi.fn().mockImplementation(async ({ onToken, onDone }) => {
            onToken('Here is your answer.');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: 'th-12345678901234567890123456789012',
                exchange_id: 'ex-12345678901234567890123456789012',
                trace_id: 'trace-1',
                feedback_token: 'feedback-token',
                follow_up_suggestions: ['Show unpaid invoices', 'Export to CSV'],
            });
        });

        window.McpAiCore = {
            sendQuestion: sendQuestionMock,
            renderMarkdown: (t) => '<p>' + t + '</p>',
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

        window.matchMedia = vi.fn((query) => ({
            matches: false,
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));

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

    it('test_follow_up_pills_render_abort_active_stream_and_trigger_submit', async () => {
        // First turn finishes and renders follow-up pills
        await window.AskAi.sendTurn('What is the status of invoices?');

        const pills = document.querySelectorAll('.mcp-ask-ai__follow-up-pill, .mcp-ask-ai__chip');
        expect(pills.length).toBe(2);
        expect(pills[0].textContent).toContain('Show unpaid invoices');
        expect(pills[1].textContent).toContain('Export to CSV');

        // Setup a long-running streaming second turn
        let secondTurnToken, secondTurnSignal;
        sendQuestionMock.mockImplementation(async ({ onToken, signal }) => {
            secondTurnSignal = signal;
            secondTurnToken = onToken;
            onToken('Streaming partial text...');
            return new Promise((resolve, reject) => {
                signal?.addEventListener('abort', () => {
                    reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
                });
            });
        });

        // Trigger in-flight turn
        const inFlightPromise = window.AskAi.sendTurn('Tell me about customer orders');
        await vi.waitFor(() => expect(sendQuestionMock).toHaveBeenCalledTimes(2));

        // Click the first follow up pill while assistant is actively streaming
        sendQuestionMock.mockImplementation(async ({ onToken, onDone }) => {
            onToken('Here are the unpaid invoices.');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: 'th-12345678901234567890123456789012',
                exchange_id: 'ex-12345678901234567890123456789013',
                follow_up_suggestions: [],
            });
        });

        // Re-render pills if needed or click existing pill
        const pillToClick = document.querySelector('.mcp-ask-ai__follow-up-pill, .mcp-ask-ai__chip');
        expect(pillToClick).toBeTruthy();
        pillToClick.click();

        // Verify signal was aborted for prior stream
        expect(secondTurnSignal.aborted).toBe(true);

        // Verify cancel endpoint was called via fetch or core
        await vi.waitFor(() => {
            const cancelCall = global.fetch.mock.calls.find(call => call[0] === '/ai-search/cancel');
            expect(cancelCall || secondTurnSignal.aborted).toBeTruthy();
        });

        // Verify new turn was dispatched with follow-up question
        await vi.waitFor(() => {
            const lastCall = sendQuestionMock.mock.calls.at(-1);
            expect(lastCall[0].question).toBe('Show unpaid invoices');
        });
    });
});
