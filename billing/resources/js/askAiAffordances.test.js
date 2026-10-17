import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { isSameOriginAppPath, escapeHtml } from '../../public/js/mcp-ai-core.js';

function mountRealBladeShape() {
    document.body.className = 'mcp-shifted';
    document.body.innerHTML = `
        <meta name="csrf-token" content="test-csrf">
        <meta name="user-id" content="test-user">
        <div class="mcp-ask-ai-backdrop" hidden></div>
        <aside class="mcp-ask-ai-sidebar" id="mcpAskAiSidebar" hidden aria-hidden="true">
            <header class="mcp-ask-ai-sidebar__header">
                <button type="button" data-mcp-ask-new data-mcp-tip="New conversation">New</button>
                <button type="button" data-mcp-ask-ai-close data-mcp-tip="Close panel">Close</button>
            </header>
            <div class="mcp-ask-ai-sidebar__body">
                <div id="mcp-ask-ai-root"
                     data-stream-url="/ai-search/stream"
                     data-feedback-url="/ai-search/feedback"
                     data-ask-ai-available="1"
                     data-rag-enabled="1"
                     data-starters='["What are your business hours?"]'>
                    <div class="mcp-ask-ai__thread"></div>
                    <div class="mcp-ask-ai__feedback-layer" data-mcp-ask-ai-feedback-layer hidden></div>
                    <textarea class="mcp-ask-ai__input" maxlength="2000" data-mcp-ask-ai-focus></textarea>
                    <span class="mcp-ask-ai__counter">0/2000</span>
                    <button type="button" class="mcp-ask-ai__send">Send</button>
                    <span class="mcp-ask-ai__mode-label"></span>
                    <div id="mcp-ask-ai-sr"></div>
                </div>
            </div>
        </aside>
    `;
}

describe('Ask AI chrome + affordances', () => {
    let sendQuestionMock;

    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();
        sendQuestionMock = vi.fn().mockImplementation(async ({ onToken, onDone }) => {
            onToken('Answer');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: 'abc12345678901234567890123456789012',
                exchange_id: 'a'.repeat(32),
                trace_id: 'trace-1',
                feedback_token: 'feedback-token',
            });
            return { terminal: 'done' };
        });

        window.McpAiCore = {
            sendQuestion: sendQuestionMock,
            renderMarkdown: (t) => '<p>' + t + '</p>',
            escapeHtml: (t) => String(t)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;'),
            isSameOriginAppPath: (u) => typeof u === 'string' && u.startsWith('/') && !u.startsWith('//'),
            sha256Hex: vi.fn().mockResolvedValue('deadbeef'),
            scheduleSrBatch: () => ({ start: () => {}, flush: () => {}, stop: () => {} }),
        };

        window.matchMedia = vi.fn((query) => ({
            matches: query.includes('prefers-reduced-motion') ? false : false,
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));

        mountRealBladeShape();
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

    it('new-chat clears a seeded thread when the button is in the sidebar header (real blade shape)', async () => {
        await window.AskAi.sendTurn('seed question');
        expect(window.AskAi.messages.length).toBeGreaterThan(0);
        document.querySelector('[data-mcp-ask-new]').click();
        expect(window.AskAi.messages).toEqual([]);
    });

    it('sets thumbs aria-pressed before fetch resolves (optimistic)', async () => {
        let resolveFetch;
        const fetchPromise = new Promise((r) => { resolveFetch = r; });
        global.fetch = vi.fn().mockReturnValue(fetchPromise);

        await window.AskAi.sendTurn('What are your hours?');
        const msg = window.AskAi.messages.find((m) => m.role === 'assistant');
        const feedbackBtn = document.querySelector('[data-feedback="up"][data-msg-id="' + msg.id + '"]');
        expect(feedbackBtn).toBeTruthy();
        expect(feedbackBtn.getAttribute('data-mcp-tip')).toBe('Helpful');

        feedbackBtn.click();
        await vi.waitFor(() => expect(global.fetch).toHaveBeenCalled());
        expect(feedbackBtn.getAttribute('aria-pressed')).toBe('true');
        expect(feedbackBtn.classList.contains('is-selected')).toBe(true);
        expect(feedbackBtn.classList.contains('mcp-ask-ai__thumb--pop')).toBe(true);

        resolveFetch({ ok: true });
        await fetchPromise;
    });

    it('reverts thumbs and announces SR error when feedback fetch fails', async () => {
        global.fetch = vi.fn().mockResolvedValue({ ok: false });

        await window.AskAi.sendTurn('What are your hours?');
        const msg = window.AskAi.messages.find((m) => m.role === 'assistant');
        const feedbackBtn = document.querySelector('[data-feedback="up"][data-msg-id="' + msg.id + '"]');
        feedbackBtn.click();
        await vi.waitFor(() => {
            expect(feedbackBtn.getAttribute('aria-pressed')).toBe('false');
            expect(document.querySelector('#mcp-ask-ai-sr').textContent).toContain('Could not send');
        });
    });

    it('does not render a copy button', async () => {
        await window.AskAi.sendTurn('What are your hours?');
        expect(document.querySelector('.mcp-ask-ai__copy')).toBeNull();
    });

    it('renders payload phase label and SVG loader before tokens arrive', async () => {
        let finish;
        sendQuestionMock.mockImplementation(async ({ onStatus }) => {
            onStatus({ phase: 'searching_documents', label: 'Searching the documents' });
            await new Promise((r) => { finish = r; });
            return { terminal: 'done' };
        });

        const turnPromise = window.AskAi.sendTurn('hours?');
        await vi.waitFor(() => {
            const label = document.querySelector('.mcp-ask-ai__loading-label');
            const loader = document.querySelector('.mcp-ask-ai__loader');
            expect(label?.textContent).toBe('Searching the documents');
            expect(loader?.getAttribute('data-loader-stage')).toBe('full');
            expect(loader?.tagName).toBe('IMG');
            expect(loader?.getAttribute('src')).toBe('/img/loaders/ai-chat-loader.svg');
        });
        finish();
        await turnPromise;
    });

    it('does not rebuild a status badge for a turn a v2 surface owns', async () => {
        let finish;
        sendQuestionMock.mockImplementation(async ({ onStatus }) => {
            const article = [...document.querySelectorAll('.mcp-ask-ai__msg--assistant')].pop();
            article.classList.add('mcp-ask-ai__msg--v2-work');
            article.querySelector('.mcp-ask-ai__status-badge')?.remove();
            onStatus({ phase: 'searching_database', label: 'Looking at the database' });
            await new Promise((r) => { finish = r; });
            return { terminal: 'done' };
        });

        const turnPromise = window.AskAi.sendTurn('hours?');
        await vi.waitFor(() => {
            expect(document.querySelectorAll('.mcp-ask-ai__status-badge')).toHaveLength(0);
        });
        finish();
        await turnPromise;
    });

    it('shows one progress label before tokens arrive', async () => {
        let finish;
        sendQuestionMock.mockImplementation(async ({ onStatus }) => {
            onStatus({ phase: 'understanding', label: 'Understanding your question' });
            onStatus({ phase: 'searching_documents', label: 'Searching the documents' });
            await new Promise((r) => { finish = r; });
            return { terminal: 'done' };
        });

        const turnPromise = window.AskAi.sendTurn('hours?');
        await vi.waitFor(() => {
            expect(document.querySelector('.mcp-ask-ai__loading-label')?.textContent)
                .toBe('Searching the documents');
            expect(document.querySelector('.mcp-ask-ai__phases')).toBeNull();
        });
        finish();
        await turnPromise;
    });

    it('renders follow-up chips after a structured database answer when threadState is ready', async () => {
        sendQuestionMock.mockImplementation(async ({ onStatus, onToken, onDone }) => {
            onStatus({ phase: 'searching_database', label: 'Looking at the database' });
            onToken('Answer');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: 'abc12345678901234567890123456789012',
                follow_up_suggestions: ['Compare their statuses', 'Group these jobs by customer'],
            });
            return { terminal: 'done' };
        });

        await window.AskAi.sendTurn('biggest invoice?');
        const msg = window.AskAi.messages.find((m) => m.role === 'assistant');
        expect(msg?.queryType).toBe('structured');

        const chips = document.querySelectorAll('.mcp-ask-ai__chip');
        expect(chips.length).toBe(2);
        expect(chips[0].textContent).toBe('Compare their statuses');
    });

    it('clicking a follow-up chip sends the template text', async () => {
        sendQuestionMock.mockImplementation(async ({ onStatus, onToken, onDone }) => {
            onStatus({ phase: 'searching_database', label: 'Looking at the database' });
            onToken('Answer');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: 'abc12345678901234567890123456789012',
                follow_up_suggestions: ['Compare their statuses'],
            });
            return { terminal: 'done' };
        });

        await window.AskAi.sendTurn('biggest invoice?');
        sendQuestionMock.mockClear();

        document.querySelector('.mcp-ask-ai__chip[data-chip-index="0"]').click();
        await vi.waitFor(() => {
            expect(sendQuestionMock).toHaveBeenCalledWith(
                expect.objectContaining({ question: 'Compare their statuses' })
            );
        });
    });

    it('renders answer-specific follow-up chips for semantic answers', async () => {
        sendQuestionMock.mockImplementation(async ({ onStatus, onToken, onDone }) => {
            onStatus({ phase: 'searching_documents', label: 'Searching the documents' });
            onToken('Answer');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: 'abc12345678901234567890123456789012',
                follow_up_suggestions: [
                    'Explain the pre-production checks',
                    'Compare offset and digital printing',
                ],
            });
            return { terminal: 'done' };
        });

        await window.AskAi.sendTurn('business hours?');
        expect(document.querySelectorAll('.mcp-ask-ai__chip')).toHaveLength(2);
        expect(document.querySelector('.mcp-ask-ai__chip')?.textContent)
            .toContain('Explain the pre-production checks');
    });

    it('does not render follow-up chips when threadState is independent', async () => {
        sendQuestionMock.mockImplementation(async ({ onStatus, onToken, onDone }) => {
            onStatus({ phase: 'searching_database', label: 'Looking at the database' });
            onToken('Answer');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: null,
                follow_up_suggestions: ['Compare their statuses'],
            });
            return { terminal: 'done' };
        });

        await window.AskAi.sendTurn('biggest invoice?');
        expect(document.querySelectorAll('.mcp-ask-ai__chip').length).toBe(0);
    });
});

describe('Task A9.1 — try-again affordance suppressed for the permanent capability_unavailable denial', () => {
    let sendQuestionMock;

    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();
        sendQuestionMock = vi.fn();

        window.McpAiCore = {
            sendQuestion: sendQuestionMock,
            renderMarkdown: (t) => '<p>' + t + '</p>',
            escapeHtml: (t) => String(t)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;'),
            isSameOriginAppPath: (u) => typeof u === 'string' && u.startsWith('/') && !u.startsWith('//'),
            sha256Hex: vi.fn().mockResolvedValue('deadbeef'),
            scheduleSrBatch: () => ({ start: () => {}, flush: () => {}, stop: () => {} }),
        };

        window.matchMedia = vi.fn((query) => ({
            matches: false,
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));

        mountRealBladeShape();
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

    it('does not render "Try again" when the stream errors with capability_unavailable', async () => {
        sendQuestionMock.mockImplementation(async ({ onError }) => {
            onError({
                error: 'capability_unavailable',
                detail: "I can't answer questions that need live business data yet.",
            });
            return { terminal: 'error' };
        });

        await window.AskAi.sendTurn('What is invoice 42 total?');
        const msg = window.AskAi.messages.find((m) => m.role === 'assistant');

        expect(msg?.error).toContain("I can't answer questions that need live business data yet");
        expect(document.querySelector('[data-try-again="' + msg.id + '"]')).toBeNull();
    });

    it('still renders "Try again" for a transient service_unavailable error', async () => {
        sendQuestionMock.mockImplementation(async ({ onError }) => {
            onError({ error: 'service_unavailable' });
            return { terminal: 'error' };
        });

        await window.AskAi.sendTurn('What are your hours?');
        const msg = window.AskAi.messages.find((m) => m.role === 'assistant');

        expect(document.querySelector('[data-try-again="' + msg.id + '"]')).toBeTruthy();
    });

    it('still renders "Try again" for an unknown/unmapped error code', () => {
        sendQuestionMock.mockImplementation(async ({ onError }) => {
            onError({ error: 'some_brand_new_code_v99' });
            return { terminal: 'error' };
        });

        return window.AskAi.sendTurn('What are your hours?').then(() => {
            const msg = window.AskAi.messages.find((m) => m.role === 'assistant');
            expect(document.querySelector('[data-try-again="' + msg.id + '"]')).toBeTruthy();
        });
    });
});

describe('Task B7 / R3 + R7 — record links reuse the shared same-origin guard (mobile sheet too)', () => {
    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();

        window.McpAiCore = {
            escapeHtml,
            isSameOriginAppPath,
            renderMarkdown: (t) => '<p>' + escapeHtml(t) + '</p>',
            sendQuestion: vi.fn(),
            sha256Hex: vi.fn(),
            scheduleSrBatch: () => ({ start: () => {}, flush: () => {}, stop: () => {} }),
        };

        // Mobile viewport: matchMedia(max-width) reports true, matching the
        // sheet-mode breakpoint used by mcp-ask-ai-sidebar.js / mcp-global-search.js.
        window.matchMedia = vi.fn((query) => ({
            matches: query.includes('max-width'),
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));

        document.body.innerHTML = `
            <aside id="mcpAskAiSidebar">
                <div id="mcp-ask-ai-root" data-rag-enabled="1" data-stream-url="/s" data-feedback-url="/f">
                    <div class="mcp-ask-ai__thread"></div>
                    <textarea class="mcp-ask-ai__input"></textarea>
                    <button class="mcp-ask-ai__send"></button>
                    <div id="mcp-ask-ai-sr"></div>
                </div>
            </aside>`;

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
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('renders safe record links and drops unsafe ones in mobile (sheet) mode', () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));
        window.AskAi._test.setMessages([{
            id: 'msg-mobile',
            role: 'assistant',
            content: 'Here is the job.',
            streaming: false,
            sources: [],
            recordLinks: [
                { table: 'work_orders', record_id: 1, label: 'WO-1', url: '/jobs/1' },
                { table: 'work_orders', record_id: 2, label: 'Evil', url: '//evil.example' },
            ],
        }]);
        window.AskAi._test.renderThread();

        const html = thread.innerHTML;
        expect(html).toContain('href="/jobs/1"');
        expect(html).not.toContain('href="//evil.example"');
    });

    it('delegates to window.McpAiCore.isSameOriginAppPath rather than forking the guard', () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));
        window.AskAi._test.setMessages([{
            id: 'msg-guard',
            role: 'assistant',
            content: 'Here is the job.',
            streaming: false,
            sources: [],
            recordLinks: [{ table: 'work_orders', record_id: 1, label: 'WO-1', url: '/jobs/1' }],
        }]);
        window.AskAi._test.renderThread();
        expect(thread.innerHTML).toContain('href="/jobs/1"');

        // Stubbing the SHARED guard to reject everything must take effect here
        // too — proving renderRecordLinks calls through it live, not a copy.
        window.McpAiCore.isSameOriginAppPath = () => false;
        window.AskAi._test.renderThread();
        expect(thread.innerHTML).not.toContain('href="/jobs/1"');
    });
});
