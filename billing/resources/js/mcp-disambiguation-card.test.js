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

describe('Cycle UX-6B: Single-Click Disambiguation Selection Cards & Stream Abort', () => {
    let sendQuestionMock;

    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();

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

    it('test_disambiguation_card_renders_xss_safe_and_aborts_active_stream_on_click', async () => {
        const disambiguationPayload = {
            term: 'Acme',
            candidates: [
                {
                    id: '101',
                    resource_type: 'customer',
                    label: 'Acme Corp',
                    secondary_label: 'ACC-101',
                    suggested_query: 'Show invoices for Acme Corp (ID: 101)',
                },
                {
                    id: '102',
                    resource_type: 'customer',
                    label: 'Acme Logistics',
                    secondary_label: 'ACC-102',
                    suggested_query: 'Show invoices for Acme Logistics (ID: 102)',
                },
            ],
        };

        // Turn 1 yields disambiguation event
        sendQuestionMock.mockImplementation(async (handlers) => {
            handlers.onDisambiguation?.(disambiguationPayload);
            handlers.onToken?.('Multiple matching customers were found.');
            handlers.onDone?.({
                thread_id: 'th-disambig',
                disambiguation: disambiguationPayload,
            });
            return { terminal: 'done' };
        });

        await window.AskAi.sendTurn('Show invoices for Acme');

        const card = document.querySelector('.mcp-ask-ai__disambiguation-card');
        expect(card).toBeTruthy();
        expect(card.textContent).toContain('Acme');

        const pills = card.querySelectorAll('.mcp-ask-ai__disambiguation-pill');
        expect(pills.length).toBe(2);
        expect(pills[0].textContent).toContain('Acme Corp');
        expect(pills[0].textContent).toContain('ACC-101');
        expect(pills[1].textContent).toContain('Acme Logistics');
        expect(pills[1].textContent).toContain('ACC-102');

        // Setup streaming turn 2
        let streamSignal;
        sendQuestionMock.mockImplementation(async (handlers) => {
            streamSignal = handlers.signal;
            handlers.onToken?.('Streaming more info...');
            return new Promise((_, reject) => {
                handlers.signal?.addEventListener('abort', () => {
                    reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
                });
            });
        });

        window.AskAi.sendTurn('Another query');
        await vi.waitFor(() => expect(sendQuestionMock).toHaveBeenCalledTimes(2));

        // Click candidate pill while streaming
        sendQuestionMock.mockImplementation(async ({ onToken, onDone }) => {
            onToken('Here are invoices for Acme Corp.');
            onDone({ thread_id: 'th-disambig-2' });
        });

        pills[0].click();

        // Verify active stream was aborted
        expect(streamSignal.aborted).toBe(true);

        // Verify suggested query was submitted
        await vi.waitFor(() => {
            const lastCall = sendQuestionMock.mock.calls.at(-1);
            expect(lastCall[0].question).toBe('Show invoices for Acme Corp (ID: 101)');
        });
    });

    it('sanitizes malicious script and img tags in disambiguation candidates without XSS execution', async () => {
        const maliciousDisambiguation = {
            term: '<script>alert("term")</script>',
            candidates: [
                {
                    id: '99',
                    resource_type: 'customer',
                    label: '<script>alert("xss")</script>',
                    secondary_label: '<img src=x onerror=alert(1)>',
                    suggested_query: 'Safe query after selection',
                },
            ],
        };

        sendQuestionMock.mockImplementation(async (handlers) => {
            handlers.onDisambiguation?.(maliciousDisambiguation);
            handlers.onDone?.({ disambiguation: maliciousDisambiguation });
            return { terminal: 'done' };
        });

        await window.AskAi.sendTurn('Look up ambiguous entity');

        const card = document.querySelector('.mcp-ask-ai__disambiguation-card');
        expect(card).toBeTruthy();

        // XSS Verification: zero executable tags created in DOM
        expect(card.querySelector('script')).toBeNull();
        expect(card.querySelector('img')).toBeNull();

        // Text content is preserved safely as text
        expect(card.textContent).toContain('<script>alert("term")</script>');
        expect(card.textContent).toContain('<script>alert("xss")</script>');
        expect(card.textContent).toContain('<img src=x onerror=alert(1)>');
    });
});
