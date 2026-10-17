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
                     data-rag-enabled="1"
                     data-starters='["What are your business hours?"]'>
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

describe('Cycle UX-1B: Progressive Status Badge, Streaming Markdown Auto-Mender & Error Notice Card', () => {
    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();

        window.McpAiCore = {
            sendQuestion: vi.fn(),
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

    it('mendMarkdown auto-closes dangling bold, code fences, and inline code during streaming', () => {
        const mend = window.McpAskAiStreamUi.mendMarkdown;
        expect(typeof mend).toBe('function');

        // Bold auto-mending
        expect(mend('**Total Invoiced:')).toBe('**Total Invoiced:**');
        expect(mend('**Total Invoiced:**')).toBe('**Total Invoiced:**');
        expect(mend('Here is **bold and *italic')).toBe('Here is **bold and *italic***');

        // Inline code auto-mending
        expect(mend('Run `npm test')).toBe('Run `npm test`');
        expect(mend('Run `npm test` now')).toBe('Run `npm test` now');

        // Code fence auto-mending
        expect(mend('```sql\nSELECT * FROM bills')).toBe('```sql\nSELECT * FROM bills\n```');
        expect(mend('```sql\nSELECT * FROM bills\n```')).toBe('```sql\nSELECT * FROM bills\n```');
        expect(mend('```json\n{"a": 1}')).toBe('```json\n{"a": 1}\n```');
    });

    it('test_status_badge_updates_on_sse_status_and_hides_on_token', async () => {
        let statusCb, tokenCb, doneCb;
        window.McpAiCore.sendQuestion = vi.fn().mockImplementation(async (handlers) => {
            statusCb = handlers.onStatus;
            tokenCb = handlers.onToken;
            doneCb = handlers.onDone;
            return new Promise((resolve) => {
                window._resolveStream = resolve;
            });
        });

        const sendPromise = window.AskAi.sendTurn('Show unpaid invoices');
        await vi.waitFor(() => expect(window.McpAiCore.sendQuestion).toHaveBeenCalled());

        // 1. Initial / connecting status
        statusCb({ phase: 'connecting', label: 'Connecting to Ask Assistant...' });
        let badge = document.querySelector('.mcp-ask-ai__status-badge');
        expect(badge).toBeTruthy();
        expect(badge.classList.contains('is-connecting') || badge.classList.contains('mcp-ask-ai__status-badge--connecting')).toBe(true);
        expect(badge.textContent).toContain('Connecting to Ask Assistant...');

        // 2. Searching database status
        statusCb({ phase: 'searching_database', label: 'Querying authorized records in MariaDB...' });
        badge = document.querySelector('.mcp-ask-ai__status-badge');
        expect(badge).toBeTruthy();
        expect(badge.textContent).toContain('Querying authorized records in MariaDB...');

        // 3. First token arrives -> badge collapses/hides
        tokenCb('Found 3 invoices');
        await vi.waitFor(() => {
            const currentBadge = document.querySelector('.mcp-ask-ai__status-badge');
            expect(!currentBadge || currentBadge.classList.contains('is-collapsed') || currentBadge.hidden || currentBadge.style.display === 'none').toBe(true);
        });

        doneCb({ thread_id: 'th-1', exchange_id: 'ex-1' });
        window._resolveStream({ terminal: 'done' });
        await sendPromise;
    });

    it('renders structured warning notice card for transient busy errors with single-click retry action', async () => {
        window.McpAiCore.sendQuestion = vi.fn().mockImplementation(async (handlers) => {
            handlers.onError({ error: 'service_unavailable', detail: 'Search is busy right now. Wait a moment and try again.' });
            return { terminal: 'error' };
        });

        await window.AskAi.sendTurn('Show overdue bills');

        const warningCard = document.querySelector('.mcp-ask-ai__notice.mcp-ask-ai__notice--warning');
        expect(warningCard).toBeTruthy();
        expect(warningCard.textContent).toContain('Search is busy right now');
        expect(warningCard.querySelector('.fa-triangle-exclamation, .fa-circle-exclamation, .fa-clock-rotate-left, .mcp-ask-ai__notice-icon, .fa-exclamation-triangle')).toBeTruthy();

        const retryBtn = warningCard.querySelector('[data-try-again]');
        expect(retryBtn).toBeTruthy();
        expect(retryBtn.textContent).toContain('Try again');

        // Unboxed bare red error text must not be present
        expect(document.querySelector('.mcp-ask-ai__error')).toBeNull();
    });
});
