import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

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
                    data-starters='["What are your business hours?","How do I create a quotation?"]'>
                    <div class="mcp-ask-ai__thread"></div>
                    <div class="mcp-ask-ai__feedback-layer" data-mcp-ask-ai-feedback-layer hidden></div>
                    <div class="mcp-ask-ai__composer">
                        <div class="mcp-ask-ai__input-wrap">
                            <textarea class="mcp-ask-ai__input" maxlength="2000" data-mcp-ask-ai-focus></textarea>
                            <span class="mcp-ask-ai__counter">0/2000</span>
                            <button type="button" class="mcp-ask-ai__send">Send</button>
                        </div>
                    </div>
                    <div class="mcp-ask-ai__meta">
                        <div class="mcp-ask-ai__scope" data-mcp-dropdown data-mcp-ask-scope>
                            <button type="button" class="mcp-btn mcp-btn--secondary mcp-btn--xs"
                                    data-mcp-dropdown-trigger data-mcp-ask-scope-trigger aria-haspopup="menu" aria-expanded="false" aria-controls="mcp-ask-ai-scope-menu" aria-label="Select context">
                                <span class="mcp-dropdown__label" data-mcp-ask-scope-label>All Records</span>
                            </button>
                            <div id="mcp-ask-ai-scope-menu" role="menu" aria-label="Select context" hidden data-mcp-dropdown-menu data-mcp-ask-scope-menu></div>
                        </div>
                        <div class="mcp-ask-ai__mode" data-mcp-dropdown data-mcp-ask-mode>
                            <button type="button" data-mcp-dropdown-trigger data-mcp-ask-mode-trigger aria-haspopup="menu" aria-expanded="false" aria-controls="mcp-ask-ai-mode-menu">Ask</button>
                            <div id="mcp-ask-ai-mode-menu" role="menu" aria-label="Mode" hidden data-mcp-dropdown-menu>
                                <button type="button" role="menuitemradio" aria-checked="true" data-mcp-ask-mode-option="ask">Ask</button>
                                <button type="button" role="menuitemradio" aria-checked="false" disabled aria-disabled="true" data-mcp-ask-mode-option="act">Act — Coming soon</button>
                            </div>
                        </div>
                        <button type="button" data-mcp-ask-new aria-label="New chat">New</button>
                        <span class="mcp-ask-ai__mode-label"></span>
                    </div>
                    <div id="mcp-ask-ai-sr"></div>
                </div>
            </div>
        </aside>
    `;
}

describe('AskAi', () => {
    let sendQuestionMock;

    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();
        sendQuestionMock = vi.fn().mockImplementation(async ({ onToken, onDone }) => {
            onToken('Answer');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: 'abc12345678901234567890123456789012',
                exchange_id: 'b'.repeat(32),
                trace_id: 'trace-1',
                feedback_token: 'feedback-token',
            });
        });

        window.McpAiCore = {
            sendQuestion: sendQuestionMock,
            renderMarkdown: (t) => '<p>' + t + '</p>',
            escapeHtml: (t) => String(t)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;'),
            sha256Hex: vi.fn().mockResolvedValue('deadbeef'),
            isSameOriginAppPath: (url) => typeof url === 'string' && url.startsWith('/') && !url.startsWith('//'),
            scheduleSrBatch: () => ({ start: () => {}, flush: () => {}, stop: () => {} }),
        };
        window.McpPageContext = {
            PageContextRegistry: {
                captureForSubmit: vi.fn().mockReturnValue({
                    version: 1,
                    context_id: 'jobs:index',
                    route: 'work-order.index',
                    kind: 'list',
                    resource_type: 'work_order',
                    record_ids: ['2', '1'],
                }),
                subscribe: vi.fn(),
                suppress: vi.fn(),
            },
        };

        window.matchMedia = vi.fn((query) => ({
            matches: query.includes('max-width') ? false : query.includes('min-width'),
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
        await import('../../public/js/mcp-dropdown.js');
        await import('../../public/js/mcp-ask-ai-composer.js');
        await import('../../public/js/mcp-ask-ai.js');
    });

    afterEach(() => {
        document.body.innerHTML = '';
        document.body.className = '';
        sessionStorage.clear();
        vi.restoreAllMocks();
    });

    it('restores open layout after setHooks when mcp.askAi.open is set', async () => {
        sessionStorage.setItem('mcp.askAi.open', '1');
        vi.resetModules();
        mountAskAiPanel();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
        await import('../../public/js/mcp-ask-ai-persist.js');
        await import('../../public/js/mcp-ask-ai-stream-ui.js');
        await import('../../public/js/mcp-ask-stream-client.js');
        await import('../../public/js/mcp-ask-ai-feedback.js');
        await import('../../public/js/mcp-ask-ai.js');
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(true);
        expect(document.querySelector('.mcp-ask-ai__empty')).toBeTruthy();
    });

    it('appends user then assistant messages when sending a turn', async () => {
        await window.AskAi.sendTurn('What are your hours?');

        expect(window.AskAi.messages).toHaveLength(2);
        expect(window.AskAi.messages[0].role).toBe('user');
        expect(window.AskAi.messages[1].role).toBe('assistant');
    });

    it('Task B4 / R3: passes a record_context through sendTurn(question, { recordContext }) to sendQuestion', async () => {
        const recordContext = {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        };

        await window.AskAi.sendTurn('Tell me about this quotation', { recordContext });

        expect(sendQuestionMock).toHaveBeenCalledOnce();
        expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(recordContext);
    });

    it('Task B4 / R3: omits recordContext from sendQuestion when none is provided', async () => {
        await window.AskAi.sendTurn('What are your hours?');

        expect(sendQuestionMock.mock.calls[0][0].recordContext).toBeNull();
    });

    it('Task B7 / R1 (supersedes B4/R3): AskAi.open(seed, recordContext) stages it — the triggering turn only carries it once the user sends', async () => {
        const recordContext = {
            version: 1,
            origin: 'global_search',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        };

        window.AskAi.open('Ask about E8520', recordContext);
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
        await new Promise((r) => setTimeout(r, 0));

        // Staged, not auto-sent (deliberate R1 behavior change — a record
        // context is shown as a removable chip before submit, see the
        // "record-context chip" describe block below for full coverage).
        expect(sendQuestionMock).not.toHaveBeenCalled();

        await window.AskAi.sendTurn('Ask about E8520');
        expect(sendQuestionMock).toHaveBeenCalledOnce();
        expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(recordContext);
    });

    it('renders the loader before starting the network request', async () => {
        const turnPromise = window.AskAi.sendTurn('What are your hours?');

        expect(document.querySelector('.mcp-ask-ai__loader')).toBeTruthy();
        expect(document.querySelector('.mcp-ask-ai__loading-label')?.textContent)
            .toBe('Understanding your question');
        expect(sendQuestionMock).not.toHaveBeenCalled();

        await turnPromise;
        expect(sendQuestionMock).toHaveBeenCalledOnce();
    });

    it('stop control is present and enabled during streaming, dispatches turn-stopped on click and reverts to idle mode', async () => {
        let abortPromise;
        const abortHandlerPromise = new Promise((resolve) => {
            abortPromise = resolve;
        });
        sendQuestionMock = vi.fn().mockImplementation(({ signal }) => {
            return new Promise((resolve, reject) => {
                const triggerAbort = () => {
                    const err = new Error('The user aborted a request.');
                    err.name = 'AbortError';
                    reject(err);
                    abortPromise();
                };
                if (signal?.aborted) {
                    triggerAbort();
                    return;
                }
                signal?.addEventListener('abort', triggerAbort);
            });
        });
        window.McpAiCore.sendQuestion = sendQuestionMock;

        const stoppedEvents = [];
        const onStopped = (e) => stoppedEvents.push(e.detail);
        window.addEventListener('mcp:askai:turn-stopped', onStopped);

        const sendBtn = document.querySelector('.mcp-ask-ai__send');

        const sendTurnPromise = window.AskAi.sendTurn('What are your hours?');
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

        expect(sendBtn.classList.contains('mcp-ask-ai__send--stop')).toBe(true);
        expect(sendBtn.getAttribute('aria-label')).toBe('Stop generating');
        expect(sendBtn.disabled).toBe(false);

        // A deliberate Stop comes a second after Send, not within the Send gesture.
        const stopAt = performance.now() + 1000;
        vi.spyOn(performance, 'now').mockReturnValue(stopAt);
        sendBtn.click();

        await abortHandlerPromise;
        await sendTurnPromise;

        expect(stoppedEvents).toHaveLength(1);
        expect(stoppedEvents[0]).toMatchObject({
            messageId: expect.any(String),
            runId: expect.any(String),
        });

        expect(sendBtn.classList.contains('mcp-ask-ai__send--stop')).toBe(false);
        expect(sendBtn.getAttribute('aria-label')).toBe('Send question');

        window.removeEventListener('mcp:askai:turn-stopped', onStopped);
    });

    it('sets currentThreadId from a truthy done thread_id', async () => {
        await window.AskAi.sendTurn('What are your hours?');
        expect(window.AskAi.currentThreadId).toBe('abc12345678901234567890123456789012');
    });

    it('passes threadId on the next sendQuestion call', async () => {
        await window.AskAi.sendTurn('first question');
        sendQuestionMock.mockClear();

        sendQuestionMock.mockImplementation(async ({ onToken, onDone }) => {
            onToken('Follow up');
            onDone({
                model: 'gpt-4o-mini',
                thread_id: 'abc12345678901234567890123456789012',
                exchange_id: 'b'.repeat(32),
                trace_id: 'trace-1',
                feedback_token: 'feedback-token',
            });
        });

        await window.AskAi.sendTurn('follow up question');

        expect(sendQuestionMock).toHaveBeenCalledWith(
            expect.objectContaining({ threadId: 'abc12345678901234567890123456789012' })
        );
    });

    it('new chat resets currentThreadId and clears messages', async () => {
        await window.AskAi.sendTurn('first question');
        window.AskAi.newChat();

        expect(window.AskAi.currentThreadId).toBeNull();
        expect(window.AskAi.messages).toHaveLength(0);
    });

    it('shows live character counter', () => {
        const textarea = document.querySelector('.mcp-ask-ai__input');
        const counter = document.querySelector('.mcp-ask-ai__counter');
        textarea.value = 'hello';
        textarea.dispatchEvent(new Event('input'));
        expect(counter.textContent).toBe('5/2000');
        expect(counter.hidden).toBe(true);
        textarea.value = 'x'.repeat(1800);
        textarea.dispatchEvent(new Event('input'));
        expect(counter.hidden).toBe(false);
        expect(counter.textContent).toBe('1800/2000');
    });

    it('does not send on Enter when isComposing is true (IME guard)', () => {
        const textarea = document.querySelector('.mcp-ask-ai__input');
        textarea.value = 'test question here';

        const spy = vi.spyOn(window.AskAi, 'sendTurn');
        textarea.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            isComposing: true,
        }));

        expect(spy).not.toHaveBeenCalled();
    });

    it('does not send on Enter when keyCode is 229 (IME guard)', () => {
        const textarea = document.querySelector('.mcp-ask-ai__input');
        textarea.value = 'test question here';

        const spy = vi.spyOn(window.AskAi, 'sendTurn');
        const evt = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
        Object.defineProperty(evt, 'keyCode', { value: 229 });
        textarea.dispatchEvent(evt);

        expect(spy).not.toHaveBeenCalled();
    });

    it('Stop aborts the in-flight stream', async () => {
        sendQuestionMock.mockImplementation(({ signal, onToken }) => {
            onToken('partial');
            return new Promise((_, reject) => {
                if (signal?.aborted) {
                    reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
                    return;
                }
                signal?.addEventListener(
                    'abort',
                    () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })),
                    { once: true }
                );
            });
        });

        const promise = window.AskAi.sendTurn('long question');
        await vi.waitFor(() => expect(sendQuestionMock).toHaveBeenCalled());
        window.AskAi.stopStream?.();
        await promise;

        const assistant = window.AskAi.messages.find(m => m.role === 'assistant');
        expect(assistant?.stopped).toBe(true);
    });

    it('replaces a stopped turn when trying again instead of appending a duplicate', async () => {
        sendQuestionMock.mockImplementation(({ signal, onToken }) => {
            onToken('partial');
            return new Promise((_, reject) => {
                signal?.addEventListener(
                    'abort',
                    () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })),
                    { once: true }
                );
            });
        });

        const stoppedTurn = window.AskAi.sendTurn('Compare the printing workflows');
        await vi.waitFor(() => expect(sendQuestionMock).toHaveBeenCalledOnce());
        window.AskAi.stopStream();
        await stoppedTurn;
        const stoppedAssistant = window.AskAi.messages.find((message) => message.role === 'assistant');

        sendQuestionMock.mockReset();
        sendQuestionMock.mockImplementation(async ({ onToken, onDone }) => {
            onToken('Replacement answer');
            onDone({
                thread_id: 'abc12345678901234567890123456789012',
                exchange_id: 'c'.repeat(32),
            });
        });

        await window.AskAi.tryAgain(stoppedAssistant.id);

        expect(window.AskAi.messages).toHaveLength(2);
        expect(window.AskAi.messages.filter((message) => message.role === 'user')).toHaveLength(1);
        expect(window.AskAi.messages[0].content).toBe('Compare the printing workflows');
        expect(window.AskAi.messages[1].content).toBe('Replacement answer');
        expect(document.querySelectorAll('.mcp-ask-ai__msg--user')).toHaveLength(1);
        expect(document.querySelector('[data-try-again]')).toBeNull();
    });

    it('submits trace-bound feedback', async () => {
        global.fetch = vi.fn().mockResolvedValue({ ok: true });

        await window.AskAi.sendTurn('What are your hours?');
        const msg = window.AskAi.messages.find(m => m.role === 'assistant');
        document.querySelector('[data-feedback="up"][data-msg-id="' + msg.id + '"]').click();
        await vi.waitFor(() => expect(global.fetch).toHaveBeenCalled());
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            trace_id: 'trace-1',
            feedback_token: 'feedback-token',
            verdict: 'up',
        });
    });

    it('collects reason and optional detail in a floating card without rerendering the answer', async () => {
        global.fetch = vi.fn().mockResolvedValue({ ok: true });
        await window.AskAi.sendTurn('What are your hours?');
        const msg = window.AskAi.messages.find(m => m.role === 'assistant');
        const originalArticle = document.querySelector('[data-msg-id="' + msg.id + '"]');
        document.querySelector('[data-feedback="down"][data-msg-id="' + msg.id + '"]').click();
        expect(global.fetch).not.toHaveBeenCalled();
        expect(document.querySelector('.mcp-ask-ai__thread .mcp-ask-ai__feedback-card')).toBeNull();
        expect(document.querySelector('[data-mcp-ask-ai-feedback-layer]')?.hidden).toBe(false);
        expect(document.querySelector('[data-msg-id="' + msg.id + '"]')).toBe(originalArticle);
        const reason = document.querySelector(
            '[data-feedback-reason="' + msg.id + '"][value="incorrect"]'
        );
        expect(reason).toBeTruthy();
        reason.click();
        const comment = document.querySelector('[data-feedback-comment="' + msg.id + '"]');
        comment.value = 'The listed hours do not mention Friday.';
        comment.dispatchEvent(new Event('input', { bubbles: true }));
        const submit = document.querySelector('[data-feedback-submit="' + msg.id + '"]');
        expect(submit.disabled).toBe(false);
        submit.click();
        await vi.waitFor(() => expect(global.fetch).toHaveBeenCalledOnce());
        expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
            trace_id: 'trace-1',
            feedback_token: 'feedback-token',
            verdict: 'down',
            reason: 'incorrect',
            comment: 'The listed hours do not mention Friday.',
        });
        await vi.waitFor(() => {
            expect(document.querySelector('[data-mcp-ask-ai-feedback-layer]')?.hidden).toBe(true);
        });
        expect(document.querySelector('[data-msg-id="' + msg.id + '"]')).toBe(originalArticle);
    });

    it('switches feedback direction without rerendering the conversation', async () => {
        global.fetch = vi.fn().mockResolvedValue({ ok: true });
        await window.AskAi.sendTurn('What are your hours?');
        const msg = window.AskAi.messages.find(m => m.role === 'assistant');
        const article = document.querySelector('[data-msg-id="' + msg.id + '"]');
        const up = article.querySelector('[data-feedback="up"]');
        const down = article.querySelector('[data-feedback="down"]');

        up.click();
        await vi.waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
        down.click();
        expect(document.querySelector('[data-mcp-ask-ai-feedback-layer]')?.hidden).toBe(false);
        expect(document.querySelector('[data-msg-id="' + msg.id + '"]')).toBe(article);

        up.click();
        await vi.waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
        expect(document.querySelector('[data-mcp-ask-ai-feedback-layer]')?.hidden).toBe(true);
        expect(document.querySelector('[data-msg-id="' + msg.id + '"]')).toBe(article);
        expect(up.getAttribute('aria-pressed')).toBe('true');
        expect(down.getAttribute('aria-pressed')).toBe('false');
    });

    it('Task B7 / R4: null thread_id sets threadState to independent', async () => {
        sendQuestionMock.mockImplementation(async ({ onDone }) => {
            onDone({ model: 'm', thread_id: null });
        });

        await window.AskAi.sendTurn('single shot question');
        expect(window.AskAi.threadState).toBe('independent');
    });

    it('Task B7 / R4: truthy thread_id sets threadState to ready', async () => {
        await window.AskAi.sendTurn('What are your hours?');
        expect(window.AskAi.threadState).toBe('ready');
    });

    it('Task B7 / R4: threadState is new before any turn is sent', () => {
        window.AskAi.newChat();
        expect(window.AskAi.threadState).toBe('new');
    });

    it('Task B7 / R4: threadState re-evaluates on every done, not just the first', async () => {
        await window.AskAi.sendTurn('first question');
        expect(window.AskAi.threadState).toBe('ready');

        sendQuestionMock.mockImplementationOnce(async ({ onDone }) => {
            onDone({ model: 'm', thread_id: null });
        });
        await window.AskAi.sendTurn('second question');
        expect(window.AskAi.threadState).toBe('independent');
    });

    it('Task R7: an independent answer cannot expose unrelated chips or continue a prior thread', async () => {
        await window.AskAi.sendTurn('What jobs need attention?');
        expect(window.AskAi.currentThreadId).toBe('abc12345678901234567890123456789012');

        sendQuestionMock.mockImplementationOnce(async ({ onDone }) => {
            onDone({
                model: 'm',
                thread_id: null,
                follow_up_suggestions: [
                    'Compare labor-market wages',
                    'Open an unrelated compensation report',
                ],
            });
        });
        await window.AskAi.sendTurn('What is the current work order status?');

        expect(window.AskAi.threadState).toBe('independent');
        expect(document.querySelectorAll('.mcp-ask-ai__chip')).toHaveLength(0);
        expect(document.body.textContent).not.toContain('labor-market wages');
        expect(document.body.textContent).not.toContain('unrelated compensation report');

        sendQuestionMock.mockClear();
        await window.AskAi.sendTurn('Start a separate question');
        expect(sendQuestionMock.mock.calls[0][0].threadId).toBeNull();
    });

    it('Task B7 / R4: a stream error sets threadState to error without touching a stopped stream', async () => {
        sendQuestionMock.mockImplementation(async () => {
            throw new Error('boom');
        });
        await window.AskAi.sendTurn('will fail');
        expect(window.AskAi.threadState).toBe('error');

        sendQuestionMock.mockImplementation(async ({ onDone }) => {
            onDone({ model: 'm', thread_id: 'abc12345678901234567890123456789012' });
        });
        await window.AskAi.sendTurn('recovers');
        expect(window.AskAi.threadState).toBe('ready');
    });

    it('renders empty-state example prompts', () => {
        window.AskAi.newChat();
        const prompts = document.querySelectorAll('.mcp-ask-ai__prompt');
        expect(prompts.length).toBeGreaterThan(0);
    });

    it('refocuses the composer after a completed stream when the panel is open', async () => {
        window.McpAskAiSidebar.open();
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
        document.body.focus();
        await window.AskAi.sendTurn('What are your hours?');
        expect(document.activeElement).toBe(document.querySelector('.mcp-ask-ai__input'));
    });

    it('persists thread to sessionStorage after a completed turn', async () => {
        await window.AskAi.sendTurn('What are your hours?');
        const assistant = window.AskAi.messages.find((message) => message.role === 'assistant');
        assistant.followupSuggestions = ['Compare the schedules'];
        window.McpAskAiPersist.persistThread({
            threadId: window.AskAi.currentThreadId,
            threadState: window.AskAi.threadState,
            messages: window.AskAi.messages,
        });

        const raw = sessionStorage.getItem('mcp.askAi.thread.v1.test-user');
        expect(raw).toBeTruthy();
        const saved = JSON.parse(raw);
        expect(saved.threadId).toBe('abc12345678901234567890123456789012');
        expect(saved.messages.length).toBe(2);
        expect(saved.messages[1].followupSuggestions).toEqual(['Compare the schedules']);
    });

    it('caps persisted followupSuggestions at 3 via normalizeFollowupSuggestions', () => {
        const normalized = window.McpAskAiPersist.normalizeFollowupSuggestions([
            'One',
            'Two',
            'Three',
            'Four',
            '  One  ',
            '',
            12,
        ]);
        expect(normalized).toEqual(['One', 'Two', 'Three']);

        window.McpAskAiPersist.persistThread({
            threadId: 'thread-cap',
            threadState: 'ready',
            messages: [{
                id: 'msg-1',
                role: 'assistant',
                content: 'hi',
                followupSuggestions: ['One', 'Two', 'Three', 'Four', 'Five'],
            }],
        });
        const saved = JSON.parse(sessionStorage.getItem('mcp.askAi.thread.v1.test-user'));
        expect(saved.messages[0].followupSuggestions).toEqual(['One', 'Two', 'Three']);
    });

    it('feedback module owns popover HTML without StreamUi injection', () => {
        const html = window.McpAskAiFeedback.renderFeedbackPopoverHtml({
            id: 'msg-9',
            feedback: 'down',
            feedbackFormOpen: true,
            feedbackReason: 'incorrect',
            feedbackComment: 'Needs more detail',
            feedbackState: 'draft',
        }, window.McpAiCore.escapeHtml);

        expect(window.McpAskAiFeedback.FEEDBACK_REASONS.map(([value]) => value))
            .toEqual(['incorrect', 'incomplete', 'irrelevant', 'unsafe', 'other']);
        expect(html).toContain('data-feedback-popover="msg-9"');
        expect(html).toContain('Needs more detail');
        expect(html).toContain('value="incorrect"');
        expect(html).toContain('checked');
        expect(window.McpAskAiStreamUi.FEEDBACK_REASONS).toBeUndefined();
        expect(window.McpAskAiStreamUi.createUi({
            escapeHtml: window.McpAiCore.escapeHtml,
            getThreadEl: () => null,
            scrollToBottom: () => {},
            renderMarkdown: (t) => t,
            isSameOriginAppPath: () => true,
        }).renderFeedbackPopoverHtml).toBeUndefined();
    });

    describe('Task A9 / R2 — mapStreamError: honest capability_unavailable copy + unknown-code fallback', () => {
        it('maps capability_unavailable to the honest, jargon-free copy', () => {
            const message = window.McpAskAiStreamUi.mapStreamError({
                error: 'capability_unavailable',
                detail: "I can't answer questions that need live business data yet — I can help "
                    + 'with documents, policies, and records you open from Global Search.',
            });
            expect(message).toContain("I can't answer questions that need live business data yet");
            expect(message.toLowerCase()).not.toContain('sql');
            expect(message.toLowerCase()).not.toContain('containment');
            expect(message.toLowerCase()).not.toContain('policy mode');
        });

        it('falls back to an honest default (not the generic "AI search failed.") when capability_unavailable has no detail', () => {
            // Discriminating case: with no `detail`, an UNMAPPED code falls through
            // to the bare generic fallback ('AI search failed.') — proving this
            // code has its OWN entry in the known map, not just a lucky detail
            // passthrough (see the "with detail" case above, which can't tell the
            // two apart since RAG's detail already IS the honest copy).
            const message = window.McpAskAiStreamUi.mapStreamError({ error: 'capability_unavailable' });
            expect(message).not.toBe('AI search failed.');
            expect(message.toLowerCase()).not.toContain('sql');
        });

        it('does not map capability_unavailable to the generic busy message', () => {
            const message = window.McpAskAiStreamUi.mapStreamError({
                error: 'capability_unavailable',
                detail: 'anything',
            });
            expect(message).not.toBe('Search is busy right now. Wait a moment and try again.');
        });

        it('forward-compat pin: an unrecognized error code still falls back to detail/generic text (old JS x new RAG must degrade, not break)', () => {
            const withDetail = window.McpAskAiStreamUi.mapStreamError({
                error: 'some_brand_new_code_v99',
                detail: 'A future RAG version added this.',
            });
            expect(withDetail).toBe('A future RAG version added this.');

            const withoutDetail = window.McpAskAiStreamUi.mapStreamError({ error: 'some_brand_new_code_v99' });
            expect(withoutDetail).toBe('AI search failed.');
        });
    });

    it('Task B7 / R5: restoring a persisted thread is a reconnect (resuming), not a switch', async () => {
        sessionStorage.setItem('mcp.askAi.thread.v1.test-user', JSON.stringify({
            threadId: 'restored-thread-id',
            threadState: 'ready',
            messages: [
                { id: 'msg-1', role: 'user', content: 'hello', streaming: false },
                {
                    id: 'msg-2',
                    role: 'assistant',
                    content: 'hi',
                    streaming: false,
                    question: 'hello',
                    followupSuggestions: ['Compare the schedules'],
                },
            ],
        }));

        vi.resetModules();
        mountAskAiPanel();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
        await import('../../public/js/mcp-ask-ai-persist.js');
        await import('../../public/js/mcp-ask-ai-stream-ui.js');
        await import('../../public/js/mcp-ask-stream-client.js');
        await import('../../public/js/mcp-ask-ai-feedback.js');
        await import('../../public/js/mcp-ask-ai.js');
        await new Promise((r) => setTimeout(r, 0));

        // Reconnect: the SAME thread_id carries over, and the last-known
        // capability is not trusted as "ready" again until a fresh done
        // reconfirms it this session — the persisted follow-up chip is not
        // resurrected on stale say-so (R6-style gating extended to R4 state).
        // A cached assistant body without a restore_ref never paints: the turn
        // keeps its place with the fixed notice instead.
        expect(window.AskAi.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
        expect(window.AskAi.messages[1].error).toBe(window.McpAskAiPersist.RESTORE_NOTICE);
        expect(window.AskAi.messages[1].content).toBe('');
        expect(window.AskAi.currentThreadId).toBe('restored-thread-id');
        expect(window.AskAi.threadState).toBe('resuming');
        expect(document.querySelector('.mcp-ask-ai__chip')).toBeNull();

        // The next turn re-sends the SAME thread_id (reconnect, not a fresh thread)...
        await window.AskAi.sendTurn('a follow-up question');
        expect(sendQuestionMock.mock.calls[0][0].threadId).toBe('restored-thread-id');
        // ...and once the server reconfirms it, state moves out of "resuming".
        expect(window.AskAi.threadState).toBe('ready');
    });

    it('Task 21: after restore apply or clear, mode label leaves resuming and threadState settles', async () => {
        // Oracle: spec §4.6 reader rules, plan Task 21 Step 1(b), updateModeLabel public/js/mcp-ask-ai.js:290-305
        sessionStorage.setItem('mcp.askAi.thread.v1.test-user', JSON.stringify({
            threadId: 'restored-thread-t21',
            threadState: 'ready',
            messages: [
                { id: 'msg-1', role: 'user', content: 'hello', streaming: false },
                {
                    id: 'msg-2',
                    role: 'assistant',
                    content: '',
                    streaming: false,
                    question: 'hello',
                    restore_ref: 'ref-t21',
                },
            ],
        }));

        window.McpAskAiRestoreGate = {
            invalidate: vi.fn(),
            restore: vi.fn(),
        };

        vi.resetModules();
        mountAskAiPanel();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
        await import('../../public/js/mcp-ask-ai-persist.js');
        await import('../../public/js/mcp-ask-ai-stream-ui.js');
        await import('../../public/js/mcp-ask-stream-client.js');
        await import('../../public/js/mcp-ask-ai-feedback.js');
        await import('../../public/js/mcp-ask-ai.js');
        await new Promise((r) => setTimeout(r, 0));

        const label = document.querySelector('.mcp-ask-ai__mode-label');
        expect(window.AskAi.threadState).toBe('resuming');
        expect(label.textContent).toBe('');

        window.McpAskAiRestoreApply({
            results: [{
                restore_ref: 'ref-t21',
                status: 'authorized',
                payload: {
                    run_id: 'r-t21',
                    run_status: 'done',
                    duration_ms: 7644,
                    business_query: { outcome: 'answered', envelope: { columns: [], rows: [] } },
                },
            }],
        });

        expect(window.AskAi.threadState).toBe('ready');
        expect(label.textContent).toBe('');
    });

    it('Task 21: a restore cut short leaves the notice and the thread stays ready', async () => {
        // Oracle: spec §4.6 reader rules, plan Task 21 Step 1(b), updateModeLabel public/js/mcp-ask-ai.js:290-305
        sessionStorage.setItem('mcp.askAi.thread.v1.test-user', JSON.stringify({
            threadId: 'restored-thread-t21-clear',
            threadState: 'ready',
            messages: [
                {
                    id: 'msg-1',
                    role: 'assistant',
                    content: '',
                    streaming: false,
                    question: 'orphan assistant',
                    restore_ref: 'ref-t21-clear',
                },
            ],
        }));

        window.McpAskAiRestoreGate = {
            invalidate: vi.fn(),
            restore: vi.fn(),
        };

        vi.resetModules();
        mountAskAiPanel();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
        await import('../../public/js/mcp-ask-ai-persist.js');
        await import('../../public/js/mcp-ask-ai-stream-ui.js');
        await import('../../public/js/mcp-ask-stream-client.js');
        await import('../../public/js/mcp-ask-ai-feedback.js');
        await import('../../public/js/mcp-ask-ai.js');
        await new Promise((r) => setTimeout(r, 0));

        const label = document.querySelector('.mcp-ask-ai__mode-label');
        expect(window.AskAi.threadState).toBe('resuming');
        expect(label.textContent).toBe('');

        window.McpAskAiRestoreClear();

        expect(window.AskAi.messages.map((m) => m.error)).toEqual([window.McpAskAiPersist.RESTORE_NOTICE]);
        expect(window.AskAi.threadState).toBe('ready');
        expect(label.textContent).toBe('');
    });

    it('Task A9 / Bug 2 (R4): a resumed thread the server no longer recognizes shows an honest expiry notice and continues with the fresh thread', async () => {

        sessionStorage.setItem('mcp.askAi.thread.v1.test-user', JSON.stringify({
            threadId: 'expired-thread-id',
            threadState: 'ready',
            messages: [
                { id: 'msg-1', role: 'user', content: 'hello', streaming: false },
                {
                    id: 'msg-2',
                    role: 'assistant',
                    content: 'hi',
                    streaming: false,
                    question: 'hello',
                    followupSuggestions: ['Compare the schedules'],
                },
            ],
        }));

        vi.resetModules();
        mountAskAiPanel();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
        await import('../../public/js/mcp-ask-ai-persist.js');
        await import('../../public/js/mcp-ask-ai-stream-ui.js');
        await import('../../public/js/mcp-ask-stream-client.js');
        await import('../../public/js/mcp-ask-ai-feedback.js');
        await import('../../public/js/mcp-ask-ai.js');
        await new Promise((r) => setTimeout(r, 0));

        expect(window.AskAi.threadState).toBe('resuming');

        // The server doesn't recognize 'expired-thread-id' (expired, never
        // existed, or not this user's — genuinely indistinguishable) and
        // mints a fresh one instead, per RAG's existing thread_id channel.
        sendQuestionMock.mockImplementation(async ({ onDone }) => {
            onDone({ model: 'm', thread_id: 'fresh-thread-id' });
            return { terminal: 'done' };
        });

        await window.AskAi.sendTurn('a follow-up question');

        expect(sendQuestionMock.mock.calls[0][0].threadId).toBe('expired-thread-id');
        // The turn itself still succeeds — normal ready/independent transition,
        // no new state value, no parallel mechanism (B7's machine, extended).
        expect(window.AskAi.currentThreadId).toBe('fresh-thread-id');
        expect(window.AskAi.threadState).toBe('ready');

        const lastAssistant = window.AskAi.messages.filter((m) => m.role === 'assistant').at(-1);
        expect(lastAssistant.threadExpiredNotice).toBeTruthy();
        expect(document.querySelector('.mcp-ask-ai__thread-notice')?.textContent)
            .toContain('expired');
        // Not an error — feedback/regenerate affordances must still be offered.
        expect(lastAssistant.error).toBeFalsy();
    });

    it('Task A9 / Bug 2 (R4): a brand-new thread (no thread_id ever sent) never shows the expiry notice', async () => {
        await window.AskAi.sendTurn('What are your hours?');

        const assistant = window.AskAi.messages.filter((m) => m.role === 'assistant').at(-1);
        expect(assistant.threadExpiredNotice).toBeFalsy();
        expect(document.querySelector('.mcp-ask-ai__thread-notice')).toBeNull();
    });

    it('Task A9 / Bug 2 (R4): a thread that IS still recognized (same id echoed back) never shows the expiry notice', async () => {
        await window.AskAi.sendTurn('first question');
        expect(window.AskAi.currentThreadId).toBe('abc12345678901234567890123456789012');

        // Same thread_id echoed back on the next turn — genuinely still valid.
        await window.AskAi.sendTurn('a follow-up question');

        const assistant = window.AskAi.messages.filter((m) => m.role === 'assistant').at(-1);
        expect(assistant.threadExpiredNotice).toBeFalsy();
        expect(document.querySelector('.mcp-ask-ai__thread-notice')).toBeNull();
    });

    it('Task B7 / R5: newChat is an explicit thread switch — clears thread_id and resets state to new', async () => {
        await window.AskAi.sendTurn('What are your hours?');
        expect(window.AskAi.threadState).toBe('ready');

        window.AskAi.newChat();
        expect(window.AskAi.currentThreadId).toBeNull();
        expect(window.AskAi.threadState).toBe('new');

        await window.AskAi.sendTurn('a brand new question');
        expect(sendQuestionMock.mock.calls[0][0].threadId).toBeNull();
    });

    it('Task B7 / R5: a stale done from a superseded turn cannot clobber the newer turn\'s thread/state', async () => {
        let releaseFirst;
        const firstDone = new Promise((resolve) => { releaseFirst = resolve; });
        let callIndex = 0;
        sendQuestionMock.mockImplementation(async ({ onDone }) => {
            callIndex += 1;
            if (callIndex === 1) {
                await firstDone;
                onDone({ model: 'm', thread_id: 'stale-thread' });
                return { terminal: 'done' };
            }
            onDone({ model: 'm', thread_id: 'fresh-thread' });
            return { terminal: 'done' };
        });

        const firstTurn = window.AskAi.sendTurn('first question');
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
        // Supersede the in-flight first turn with a second one (abort + new turnId).
        const secondTurn = window.AskAi.sendTurn('second question');
        await secondTurn;
        expect(window.AskAi.currentThreadId).toBe('fresh-thread');
        expect(window.AskAi.threadState).toBe('ready');

        // Now let the stale first turn's done fire late.
        releaseFirst();
        await firstTurn;

        expect(window.AskAi.currentThreadId).toBe('fresh-thread');
        expect(window.AskAi.threadState).toBe('ready');
    });

    it('Task B7 / R5: a disconnect (non-abort stream error) is not a cancel and does not clear the thread', async () => {
        await window.AskAi.sendTurn('What are your hours?');
        expect(window.AskAi.currentThreadId).toBe('abc12345678901234567890123456789012');

        sendQuestionMock.mockImplementation(async () => {
            const err = new TypeError('network error');
            throw err;
        });
        await window.AskAi.sendTurn('a follow-up during a network blip');

        const assistant = window.AskAi.messages.filter((m) => m.role === 'assistant').at(-1);
        expect(assistant.stopped).toBeFalsy();
        expect(assistant.error).toBeTruthy();
        expect(window.AskAi.currentThreadId).toBe('abc12345678901234567890123456789012');
        expect(window.AskAi.threadState).toBe('error');
    });

    it('Task R7: a failed response never renders labor-market or unrelated follow-up chips', async () => {
        sendQuestionMock.mockImplementation(async ({ onDone }) => {
            onDone({
                model: 'm',
                thread_id: 'abc12345678901234567890123456789012',
                follow_up_suggestions: [
                    'Compare labor-market wages',
                    'Open an unrelated compensation report',
                ],
            });
            throw new TypeError('network error after completed payload');
        });

        await window.AskAi.sendTurn('What jobs need attention?');

        const failedAssistant = window.AskAi.messages.filter((message) => message.role === 'assistant').at(-1);
        expect(failedAssistant.error).toBeTruthy();
        expect(document.querySelectorAll('.mcp-ask-ai__chip')).toHaveLength(0);
        expect(document.body.textContent).not.toContain('labor-market wages');
        expect(document.body.textContent).not.toContain('unrelated compensation report');
    });

    describe('the 24 hour panel window', () => {
        function seedThread(startedAt) {
            sessionStorage.setItem('mcp.askAi.thread.v1.test-user', JSON.stringify({
                threadId: 'th-old',
                threadState: 'ready',
                startedAt,
                messages: [
                    { id: 'msg-1', role: 'user', content: 'hello' },
                    { id: 'msg-2', role: 'assistant', content: '', restore_ref: 'a'.repeat(43) },
                ],
            }));
        }

        async function reloadPanel() {
            vi.resetModules();
            mountAskAiPanel();
            await import('../../public/js/mcp-ask-ai-sidebar.js');
            await import('../../public/js/mcp-ask-ai-persist.js');
            await import('../../public/js/mcp-ask-ai-stream-ui.js');
            await import('../../public/js/mcp-ask-stream-client.js');
            await import('../../public/js/mcp-ask-ai-feedback.js');
            await import('../../public/js/mcp-ask-ai.js');
            await new Promise((r) => setTimeout(r, 0));
        }

        beforeEach(() => {
            window.McpAskAiRestoreGate = { invalidate: vi.fn(), restore: vi.fn() };
        });

        it('a thread inside the window still asks the server to restore it', async () => {
            seedThread(Date.now() - 23 * 60 * 60 * 1000);
            await reloadPanel();
            expect(window.McpAskAiRestoreGate.restore).toHaveBeenCalledTimes(1);
        });

        it('a thread past the window opens as a new, empty thread and asks the server for nothing', async () => {
            // Oracle: mcp-rag-qa ADR 0085 — after 24 h the panel clears
            // its window; the old thread's records stay on the server.
            seedThread(Date.now() - 25 * 60 * 60 * 1000);
            await reloadPanel();
            const label = document.querySelector('.mcp-ask-ai__mode-label');
            expect(window.McpAskAiRestoreGate.restore).not.toHaveBeenCalled();
            expect(window.AskAi.messages).toEqual([]);
            expect(window.AskAi.currentThreadId).toBeNull();
            expect(sessionStorage.getItem('mcp.askAi.thread.v1.test-user')).toBeNull();
            expect(label.textContent).toBe(window.McpAskAiPersist.THREAD_CLEARED_NOTICE);
            window.AskAi.newChat();
            expect(label.textContent).toBe('');
        });
    });

    it('newChat clears persisted sessionStorage', async () => {
        await window.AskAi.sendTurn('What are your hours?');
        expect(sessionStorage.getItem('mcp.askAi.thread.v1.test-user')).toBeTruthy();
        window.AskAi.newChat();
        expect(sessionStorage.getItem('mcp.askAi.thread.v1.test-user')).toBeNull();
    });

    it('discards corrupt sessionStorage without throwing', async () => {
        sessionStorage.setItem('mcp.askAi.thread.v1.test-user', '{not json');
        vi.resetModules();
        mountAskAiPanel();
        await expect(import('../../public/js/mcp-ask-ai-sidebar.js')).resolves.toBeDefined();
        await expect(import('../../public/js/mcp-ask-ai-persist.js')).resolves.toBeDefined();
        await expect(import('../../public/js/mcp-ask-ai-stream-ui.js')).resolves.toBeDefined();
        await expect(import('../../public/js/mcp-ask-stream-client.js')).resolves.toBeDefined();
        await expect(import('../../public/js/mcp-ask-ai-feedback.js')).resolves.toBeDefined();
        await expect(import('../../public/js/mcp-ask-ai.js')).resolves.toBeDefined();
        await new Promise((r) => setTimeout(r, 0));
        expect(sessionStorage.getItem('mcp.askAi.thread.v1.test-user')).toBeNull();
    });

    it('shows selected state after feedback vote', async () => {
        global.fetch = vi.fn().mockResolvedValue({ ok: true });
        await window.AskAi.sendTurn('What are your hours?');
        const msg = window.AskAi.messages.find(m => m.role === 'assistant');
        const feedbackBtn = document.querySelector('[data-feedback="up"][data-msg-id="' + msg.id + '"]');
        expect(feedbackBtn).toBeTruthy();
        feedbackBtn.click();
        await vi.waitFor(() => {
            const thumb = document.querySelector('[data-feedback="up"][data-msg-id="' + msg.id + '"]');
            expect(thumb.getAttribute('aria-pressed')).toBe('true');
            expect(thumb.classList.contains('is-selected')).toBe(true);
        });
    });

    it('renders independent-mode note when thread_id is null', async () => {
        sendQuestionMock.mockImplementation(async ({ onDone }) => {
            onDone({ model: 'm', thread_id: null });
        });

        await window.AskAi.sendTurn('single shot question');
        expect(window.AskAi.threadState).toBe('independent');
        const label = document.querySelector('.mcp-ask-ai__mode-label');
        expect(label.textContent).toBe('Each question is answered independently');
    });

    it('disables send when input is too short', () => {
        const textarea = document.querySelector('.mcp-ask-ai__input');
        const sendBtn = document.querySelector('.mcp-ask-ai__send');
        textarea.value = 'a';
        textarea.dispatchEvent(new Event('input'));
        expect(sendBtn.disabled).toBe(true);
        textarea.value = 'valid question';
        textarea.dispatchEvent(new Event('input'));
        expect(sendBtn.disabled).toBe(false);
    });

    it('retries once on pre-stream 401 without tearing down the retry turn', async () => {
        let calls = 0;
        sendQuestionMock.mockImplementation(async () => {
            calls += 1;
            if (calls === 1) {
                const err = new Error('Session expired');
                err.name = 'PreStreamError';
                err.status = 401;
                throw err;
            }
            return {};
        });

        await window.AskAi.sendTurn('What are your hours?');

        expect(calls).toBe(2);
        expect(window.AskAi.messages).toHaveLength(2);
        expect(document.querySelector('.mcp-ask-ai__send').textContent).toBe('Send');
    });

    it('deep-freezes the exact ordered retry descriptor', async () => {
        document.querySelector('[data-mcp-ask-scope-trigger]').click();
        const options = [...document.querySelectorAll('[data-mcp-ask-scope-menu] [role="menuitemradio"]')];
        options.find((b) => b.textContent.includes('Current page'))?.click();
        await window.AskAi.sendTurn('Summarize these jobs');
        const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
        expect(Object.isFrozen(assistant.retryDescriptor)).toBe(true);
        expect(Object.isFrozen(assistant.retryDescriptor.page_context)).toBe(true);
        expect(Object.isFrozen(assistant.retryDescriptor.page_context.record_ids)).toBe(true);
        expect(assistant.retryDescriptor.page_context.record_ids).toEqual(['2', '1']);
        expect(assistant.contextLabel).toBe('Jobs on this page');
    });

    it('keeps concise page context attached to the composer throughout a conversation', async () => {
        document.querySelector('[data-mcp-ask-scope-trigger]').click();
        const options = [...document.querySelectorAll('[data-mcp-ask-scope-menu] [role="menuitemradio"]')];
        options.find((b) => b.textContent.includes('Current page'))?.click();
        await window.AskAi.sendTurn('Summarize these jobs');
        expect(document.querySelector('[data-mcp-ask-scope-label]').textContent).toContain('Current page:');

        await window.AskAi.sendTurn('Tell me more');
        expect(document.querySelector('[data-mcp-ask-scope-label]').textContent).toContain('Current page:');
    });

    it.each([
        ['work-order.index', 'list', 'Jobs on this page'],
        ['work-order.show', 'detail', 'Job on this page'],
        ['report.work-order', 'report', 'Jobs report'],
        ['customer.show', 'embedded_list', 'Jobs for customer'],
        ['bill.show', 'embedded_list', 'Jobs on bill'],
        ['customer-orders.show', 'embedded_list', 'Jobs on customer order'],
    ])('labels %s context accurately', (route, kind, expected) => {
        expect(window.AskAi._test.contextLabel({
            route,
            kind,
            record_ids: ['1'],
        })).toBe(expected);
    });

    it('changes a silent initial status to a neutral label after four seconds', () => {
        vi.useFakeTimers();
        const assistant = {
            id: 'silent-status',
            content: '',
            streaming: true,
            phases: [{ phase: 'understanding', label: 'Understanding your question' }],
        };

        window.AskAi._test.startNeutralStatusTimer(assistant);
        vi.advanceTimersByTime(window.AskAi._test.NEUTRAL_STATUS_DELAY_MS - 1);
        expect(assistant.phases).toHaveLength(1);
        vi.advanceTimersByTime(1);
        expect(assistant.phases.at(-1)).toEqual({
            phase: 'working',
            label: 'Working on your request',
        });
        vi.useRealTimers();
    });

    it('keeps the neutral status quiet when another surface owns the turn', () => {
        vi.useFakeTimers();
        const owned = document.createElement('div');
        owned.className = 'mcp-ask-ai__msg mcp-ask-ai__msg--assistant';
        owned.dataset.msgId = 'owned-status';
        owned.setAttribute('data-progress-owner', 'module');
        document.body.appendChild(owned);
        const assistant = {
            id: 'owned-status',
            content: '',
            streaming: true,
            phases: [{ phase: 'understanding', label: 'Understanding your question' }],
        };

        window.AskAi._test.startNeutralStatusTimer(assistant);
        vi.advanceTimersByTime(window.AskAi._test.NEUTRAL_STATUS_DELAY_MS + 1);
        expect(assistant.phases).toHaveLength(1);
        vi.useRealTimers();
    });

    it('regenerates only the latest answer using its immutable snapshot', async () => {
        document.querySelector('[data-mcp-ask-scope-trigger]').click();
        const options = [...document.querySelectorAll('[data-mcp-ask-scope-menu] [role="menuitemradio"]')];
        options.find((b) => b.textContent.includes('Current page'))?.click();
        await window.AskAi.sendTurn('first question');
        await window.AskAi.sendTurn('second question');
        const assistants = window.AskAi.messages.filter((m) => m.role === 'assistant');
        sendQuestionMock.mockClear();
        sendQuestionMock.mockImplementation(async ({ operation, targetExchangeId, pageContext, onDone, onToken }) => {
            expect(operation).toBe('regenerate');
            expect(targetExchangeId).toBe('b'.repeat(32));
            expect(pageContext.record_ids).toEqual(['2', '1']);
            onToken('Replacement');
            onDone({
                thread_id: 'abc12345678901234567890123456789012',
                exchange_id: 'c'.repeat(32),
                trace_id: 'trace-2',
                feedback_token: 'feedback-2',
                context_mode: 'jobs',
            });
            return { terminal: 'done' };
        });

        await window.AskAi.regenerate(assistants[0].id);
        expect(sendQuestionMock).not.toHaveBeenCalled();
        await window.AskAi.regenerate(assistants[1].id);
        expect(sendQuestionMock).toHaveBeenCalledOnce();
        expect(assistants[1].content).toBe('Replacement');
        expect(assistants[1].exchangeId).toBe('c'.repeat(32));
    });

    it('rolls back staged regeneration on failure', async () => {
        await window.AskAi.sendTurn('question');
        const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
        const original = assistant.content;
        sendQuestionMock.mockImplementation(async () => {
            throw new Error('network');
        });
        await window.AskAi.regenerate(assistant.id);
        expect(assistant.content).toBe(original);
        expect(assistant.error).toBeNull();
        expect(assistant.streaming).toBe(false);
    });

    it('keeps document citation indexes marker-bound without inventing a database index', () => {
        window.McpAiCore.renderMarkdown = () => '<p>See [Source 2] for the policy.</p>';
        const html = window.AskAi._test.renderAnswerContent({
            id: 'citation-test',
            content: 'unused',
            sources: [{ id: 'policy', title: 'Company policy', snippet: 'Verified policy' }],
            citations: { parsed: true, cited: [{ marker: 2, id: 'policy' }] },
            databaseEvidence: true,
            recordLinks: [{ label: 'WO-10', url: '/jobs/show/10' }],
            sqlQueries: ['select authorized records'],
        });

        const host = document.createElement('div');
        host.innerHTML = html;
        expect(host.querySelectorAll('.mcp-ask-ai__citation-marker')).toHaveLength(1);
        expect(host.querySelector('[data-citation-marker="2"]')).toBeTruthy();
        expect(host.querySelector('[data-citation-marker="3"]')).toBeNull();
    });

    it('removes unbound citation markers from an old RAG response', () => {
        window.McpAiCore.renderMarkdown = () => '<p>See [Source 2] and [2] before acting.</p>';
        const html = window.AskAi._test.renderAnswerContent({
            id: 'unbound-citation-test',
            content: 'unused',
            sources: [{ id: 'policy:0', title: 'Company policy', snippet: 'Verified policy' }],
            citations: { parsed: false, cited: [] },
            recordLinks: [],
            sqlQueries: [],
        });

        const host = document.createElement('div');
        host.innerHTML = html;
        expect(host.textContent).not.toContain('[Source 2]');
        expect(host.textContent).not.toContain('[2]');
        expect(host.querySelectorAll('.mcp-ask-ai__citation-marker')).toHaveLength(0);
    });

    it('persists a missing citation payload as unparsed', () => {
        window.McpAskAiPersist.persistThread({
            threadId: null,
            threadState: 'independent',
            messages: [{
                id: 'asst-1',
                role: 'assistant',
                content: 'An older RAG answer.',
                sources: [],
                citations: {},
            }],
        });

        const restored = window.McpAskAiPersist.loadPersistedThread((value) => value);

        expect(restored.messages[0].citations).toEqual({ parsed: false, cited: [] });
    });

    it('normalizes an empty live citations event as unparsed', async () => {
        sendQuestionMock.mockImplementation(async ({ onToken, onCitations, onDone }) => {
            onToken('Older RAG answer');
            onCitations({});
            onDone({ model: 'gpt-4o-mini' });
        });

        await window.AskAi.sendTurn('question');

        const assistant = window.AskAi.messages.find((message) => message.role === 'assistant');
        expect(assistant.citations).toEqual({ parsed: false, cited: [] });
    });

    it('styles Markdown tables without guessing status or record-link semantics', () => {
        window.McpAiCore.renderMarkdown = () => `
            <table>
                <thead><tr><th>Job</th><th>Status</th><th>Priority</th></tr></thead>
                <tbody><tr><td>WO-10</td><td>Finished</td><td>High</td></tr></tbody>
            </table>
            <p>Compare WO-11 separately.</p>
        `;
        const html = window.AskAi._test.renderAnswerContent({
            id: 'rich-output',
            content: 'unused',
            sources: [],
            citations: { parsed: true, cited: [] },
            recordLinks: [
                { label: 'WO-10', url: '/jobs/show/10' },
                { label: 'WO-11', url: 'javascript:alert(1)' },
            ],
        });

        const host = document.createElement('div');
        host.innerHTML = html;
        expect(host.querySelector('.mcp-ask-ai__table-wrap .mcp-table')).toBeTruthy();
        expect(host.querySelector('.mcp-status')).toBeNull();
        expect(host.querySelector('.mcp-ask-ai__inline-record-link')).toBeNull();
        expect(host.querySelector('a[href^="javascript"]')).toBeNull();
    });

    it('renders suggested follow-ups as full-width prompt cards', async () => {
        await window.AskAi.sendTurn('List the jobs');
        const assistant = window.AskAi.messages.find((message) => message.role === 'assistant');
        assistant.followupSuggestions = ['Compare their statuses', 'Group them by customer'];
        window.AskAi._test.renderThread();

        expect(document.querySelectorAll('.mcp-ask-ai__chip')).toHaveLength(2);
        expect(document.querySelector('.mcp-ask-ai__chip-icon')).toBeTruthy();
    });

    it('normalizes and bounds suggestions received with the completed answer', async () => {
        sendQuestionMock.mockImplementation(async ({ onToken, onDone }) => {
            onToken('Answer');
            onDone({
                thread_id: 'abc12345678901234567890123456789012',
                follow_up_suggestions: [
                    '  Compare their statuses  ',
                    'Compare their statuses',
                    'Group these jobs by customer',
                    'Show delivery dates',
                    'Ignored fourth suggestion',
                ],
            });
        });

        await window.AskAi.sendTurn('List the jobs');

        const assistant = window.AskAi.messages.find((message) => message.role === 'assistant');
        expect(assistant.followupSuggestions).toEqual([
            'Compare their statuses',
            'Group these jobs by customer',
            'Show delivery dates',
        ]);
        expect(document.querySelectorAll('.mcp-ask-ai__chip')).toHaveLength(3);
    });

    it('draws no legacy loader or status card while a module owns the progress display', () => {
        const { renderThinkingContent } = window.AskAi._test;
        window.McpAskAiProgressOwner = 'module';
        try {
            const html = renderThinkingContent({ phases: [] });
            expect(html).not.toContain('mcp-ask-ai__loader');
            expect(html).not.toContain('mcp-ask-ai__status-badge');
            expect(html).not.toContain('mcp-ask-ai__elapsed');
        } finally {
            delete window.McpAskAiProgressOwner;
        }
    });

    it('uses the full AI loader for every response phase and hides it once tokens arrive', async () => {
        const {
            loaderStageForMsg,
            loaderSrcForMsg,
            renderThinkingContent,
            updateAssistantBubble,
            setThreadEl,
            getMessages,
            setMessages,
        } = window.AskAi._test;

        expect(loaderStageForMsg({ phases: [] })).toBe('full');
        expect(loaderStageForMsg({ phases: [{ phase: 'understanding', label: 'Understanding' }] }))
            .toBe('full');
        expect(loaderStageForMsg({ phases: [{ phase: 'inspecting_schema', label: 'Inspecting schema' }] }))
            .toBe('full');
        expect(loaderStageForMsg({ phases: [{ phase: 'searching_documents', label: 'Finding information' }] }))
            .toBe('full');
        expect(loaderStageForMsg({ phases: [{ phase: 'writing_answer', label: 'Writing answer' }] }))
            .toBe('full');
        expect(loaderSrcForMsg({ phases: [{ phase: 'writing_answer', label: 'Writing answer' }] }))
            .toBe('/img/loaders/ai-chat-loader.svg');
        expect(loaderSrcForMsg({ phases: [] }))
            .toBe('/img/loaders/ai-chat-loader.svg');

        const html = renderThinkingContent({ phases: [{ phase: 'searching_database', label: 'Finding information' }] });
        expect(html).toContain('mcp-ask-ai__loader');
        expect(html).toContain('<img class="mcp-ask-ai__loader mcp-ask-ai__loader--full"');
        expect(html).toContain('data-loader-stage="full"');
        expect(html).toContain('src="/img/loaders/ai-chat-loader.svg"');
        expect(html).toContain('mcp-ask-ai__loading-label');
        expect(html).not.toContain('mcp-ask-ai__skeleton');
        expect(html).not.toContain('ai-chat-loader-intro.svg');

        const writingHtml = renderThinkingContent({
            phases: [{ phase: 'writing_answer', label: 'Writing answer' }],
        });
        expect(writingHtml).toContain('mcp-ask-ai__loader--full');
        expect(writingHtml).toContain('/img/loaders/ai-chat-loader.svg');

        // Integration: once tokens arrive, the thinking loader is replaced by plain text.
        const thread = document.createElement('div');
        document.body.appendChild(thread);
        setThreadEl(thread);
        const assistant = {
            id: 'asst-loader',
            role: 'assistant',
            content: '',
            streaming: true,
            phases: [{ phase: 'searching_documents', label: 'Searching' }],
            sources: [],
            citations: { parsed: true, cited: [] },
        };
        setMessages([assistant]);
        thread.innerHTML = '<article data-msg-id="asst-loader"><div class="mcp-ask-ai__msg-body"></div></article>';
        assistant._bodyEl = thread.querySelector('.mcp-ask-ai__msg-body');
        updateAssistantBubble(assistant);
        expect(assistant._bodyEl.innerHTML).toContain('mcp-ask-ai__loading');

        assistant.content = ' \n';
        updateAssistantBubble(assistant);
        expect(assistant._bodyEl.innerHTML).toContain('mcp-ask-ai__loading');

        assistant.content = 'Hello from the model';
        updateAssistantBubble(assistant);
        // The streaming body paints on the next animation frame.
        await new Promise((resolve) => requestAnimationFrame(resolve));
        expect(assistant._bodyEl.textContent).toBe('Hello from the model');
        expect(assistant._bodyEl.innerHTML).not.toContain('mcp-ask-ai__loading');

        setMessages([]);
        setThreadEl(null);
        thread.remove();
    });

    it('renders the full loader at 96 by 96', () => {
        const { renderThinkingContent } = window.AskAi._test;
        const html = renderThinkingContent({
            phases: [{ phase: 'searching_documents', label: 'Finding information' }],
        });

        expect(html).toContain('<img class="mcp-ask-ai__loader mcp-ask-ai__loader--full"');
        expect(html).toContain('width="96" height="96"');
    });

    describe('Task B7 / R1 — record-context chip (staged, before submit)', () => {
        const recordContext = {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        };

        it('open(seed, recordContext, label) stages a removable chip instead of auto-sending', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            expect(sendQuestionMock).not.toHaveBeenCalled();
            expect(document.querySelector('.mcp-ask-ai__input').value).toBe('Ask about E8520');
            expect(document.querySelector('[data-mcp-ask-scope-label]').textContent)
                .toBe('Selected record: E8520 (Quotation)');
        });

        it('sending consumes the staged record context exactly once, then clears the chip', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            await window.AskAi.sendTurn(document.querySelector('.mcp-ask-ai__input').value);
            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(recordContext);
            expect(document.querySelector('[data-mcp-ask-scope-label]').textContent)
                .toBe('All records');

            sendQuestionMock.mockClear();
            await window.AskAi.sendTurn('a follow-up question');
            expect(sendQuestionMock.mock.calls[0][0].recordContext).toBeNull();
        });

        it('removing the staged chip detaches the record context from the outgoing request', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            window.McpAskAiComposer.reset();
            expect(document.querySelector('[data-mcp-ask-scope-label]').textContent)
                .toBe('All records');

            await window.AskAi.sendTurn('Ask about E8520');
            expect(sendQuestionMock.mock.calls[0][0].recordContext).toBeNull();
        });

        it('falls back to a generic resource label when no Billing-rendered label is supplied', async () => {
            window.AskAi.open('Ask about E8520', recordContext);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            expect(document.querySelector('[data-mcp-ask-scope-label]').textContent)
                .toBe('Selected record: quotation #1234');
        });

        it('never persists the staged label or record context to sessionStorage', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            await window.AskAi.sendTurn(document.querySelector('.mcp-ask-ai__input').value);

            const raw = sessionStorage.getItem('mcp.askAi.thread.v1.test-user');
            expect(raw).not.toContain('recordContext');
            expect(raw).not.toContain('E8520 (Quotation)');
        });

        it('a plain seed with no record_context still auto-sends (existing Ctrl+K behavior unchanged)', async () => {
            window.AskAi.open('What are your hours?');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            await new Promise((r) => setTimeout(r, 0));

            expect(sendQuestionMock).toHaveBeenCalledOnce();
        });
    });

    describe('Task B7.1 / R2 fix — retry descriptor carries record refs (identifiers only); retry/regenerate re-send them', () => {
        const recordContext = {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        };

        it('the frozen retryDescriptor carries the type/ID refs but never a label/display field', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            await window.AskAi.sendTurn(document.querySelector('.mcp-ask-ai__input').value);

            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            expect(assistant.retryDescriptor.record_context).toEqual(recordContext);
            expect(Object.isFrozen(assistant.retryDescriptor)).toBe(true);
            expect(Object.isFrozen(assistant.retryDescriptor.record_context)).toBe(true);
            expect(Object.keys(assistant.retryDescriptor)).not.toContain('label');
            expect(JSON.stringify(assistant.retryDescriptor)).not.toContain('E8520 (Quotation)');
        });

        it('tryAgain (retry after error) re-sends the record refs instead of dropping grounding', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            await window.AskAi.sendTurn(document.querySelector('.mcp-ask-ai__input').value);

            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            sendQuestionMock.mockClear();
            await window.AskAi.tryAgain(assistant.id);
            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(recordContext);
        });

        it('regenerate re-sends the record refs instead of hard-nulling them', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            await window.AskAi.sendTurn(document.querySelector('.mcp-ask-ai__input').value);

            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            sendQuestionMock.mockClear();
            sendQuestionMock.mockImplementation(async ({ onDone, onToken }) => {
                onToken('Replacement');
                onDone({
                    thread_id: 'abc12345678901234567890123456789012',
                    exchange_id: 'c'.repeat(32),
                    trace_id: 'trace-2',
                    feedback_token: 'feedback-2',
                });
                return { terminal: 'done' };
            });
            await window.AskAi.regenerate(assistant.id);
            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(recordContext);
        });
    });

    describe('Task 1B — explicit record context suppresses the ambient page snapshot', () => {
        const recordContext = {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        };

        it('sendTurn: an explicit record context suppresses the ambient page snapshot even though captureForSubmit reports one', async () => {
            await window.AskAi.sendTurn('Tell me about this quotation', { recordContext });

            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(recordContext);
            expect(sendQuestionMock.mock.calls[0][0].pageContext).toBeNull();

            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            expect(assistant.retryDescriptor.page_context).toBeNull();
            expect(assistant.retryDescriptor.context_mode).toBe('none');
        });

        it('sendTurn: an ordinary Jobs turn (no record context) sends the ambient page snapshot when Current Page is selected', async () => {
            document.querySelector('[data-mcp-ask-scope-trigger]').click();
            const options = [...document.querySelectorAll('[data-mcp-ask-scope-menu] [role="menuitemradio"]')];
            options.find((b) => b.textContent.includes('Current page'))?.click();
            await window.AskAi.sendTurn('Summarize these jobs');

            expect(sendQuestionMock.mock.calls[0][0].recordContext).toBeNull();
            expect(sendQuestionMock.mock.calls[0][0].pageContext).toEqual(
                expect.objectContaining({ record_ids: ['2', '1'] })
            );
        });

        it('tryAgain: retries with the record context but never resends the page snapshot', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            await window.AskAi.sendTurn(document.querySelector('.mcp-ask-ai__input').value);

            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            sendQuestionMock.mockClear();
            await window.AskAi.tryAgain(assistant.id);

            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(recordContext);
            expect(sendQuestionMock.mock.calls[0][0].pageContext).toBeNull();
        });

        it('regenerate: regenerates with the record context but never resends the page snapshot', async () => {
            window.AskAi.open('Ask about E8520', recordContext, 'E8520 (Quotation)');
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            await window.AskAi.sendTurn(document.querySelector('.mcp-ask-ai__input').value);

            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            sendQuestionMock.mockClear();
            sendQuestionMock.mockImplementation(async ({ onDone, onToken }) => {
                onToken('Replacement');
                onDone({
                    thread_id: 'abc12345678901234567890123456789012',
                    exchange_id: 'c'.repeat(32),
                    trace_id: 'trace-2',
                    feedback_token: 'feedback-2',
                });
                return { terminal: 'done' };
            });
            await window.AskAi.regenerate(assistant.id);

            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(recordContext);
            expect(sendQuestionMock.mock.calls[0][0].pageContext).toBeNull();
        });
    });

    describe('Task B7 / R6 — done gating for follow-up feedback identity', () => {
        it('feedback identity (trace/exchange/feedback token) is absent until the server done event', async () => {
            let releaseDone;
            const gate = new Promise((resolve) => { releaseDone = resolve; });
            sendQuestionMock.mockImplementation(async ({ onToken, onDone }) => {
                onToken('Partial answer');
                await gate;
                onDone({
                    model: 'm',
                    thread_id: 'abc12345678901234567890123456789012',
                    exchange_id: 'd'.repeat(32),
                    trace_id: 'trace-gated',
                    feedback_token: 'feedback-gated',
                });
                return { terminal: 'done' };
            });

            const turn = window.AskAi.sendTurn('gated question');
            await vi.waitFor(() => {
                const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
                expect(assistant.content).toBe('Partial answer');
            });

            const midStreamAssistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            expect(midStreamAssistant.traceId).toBeNull();
            expect(midStreamAssistant.feedbackToken).toBeNull();
            expect(midStreamAssistant.exchangeId).toBeNull();
            expect(document.querySelector('[data-feedback="up"]')).toBeNull();

            releaseDone();
            await turn;

            const finished = window.AskAi.messages.find((m) => m.role === 'assistant');
            expect(finished.traceId).toBe('trace-gated');
            expect(finished.feedbackToken).toBe('feedback-gated');
            expect(document.querySelector('[data-feedback="up"][data-msg-id="' + finished.id + '"]')).toBeTruthy();
        });
    });

    describe('Task B7 / R7 — degraded record context surfaces as a normal answer', () => {
        it('a "records no longer available" answer renders as a plain completed message, no crash', async () => {
            sendQuestionMock.mockImplementation(async ({ onToken, onDone }) => {
                onToken('The earlier records are no longer available.');
                onDone({ model: 'm', thread_id: 'abc12345678901234567890123456789012' });
                return { terminal: 'done' };
            });

            await window.AskAi.sendTurn('what were those jobs again?');

            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            expect(assistant.error).toBeFalsy();
            expect(assistant.content).toBe('The earlier records are no longer available.');
            expect(document.querySelector('.mcp-ask-ai__error')).toBeNull();
        });
    });

    describe('Task P7 — "Ask about this bill" delegated record trigger (bill.show)', () => {
        function mountAskBillButton({
            resourceType = 'quotation',
            recordId = '802',
            label = 'QUO-CONTRACT-802 (Quotation)',
        } = {}) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.textContent = 'Ask about this bill';
            btn.setAttribute('data-mcp-ask-ai-record-trigger', '');
            btn.setAttribute('data-record-resource-type', resourceType);
            btn.setAttribute('data-record-id', recordId);
            btn.setAttribute('data-record-label', label);
            document.body.appendChild(btn);
            return btn;
        }

        function click(el) {
            el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        }

        afterEach(() => {
            document.querySelectorAll('[data-mcp-ask-ai-record-trigger]').forEach((el) => el.remove());
        });

        it('click stages a removable one-shot record-context chip without auto-sending', async () => {
            const btn = mountAskBillButton();
            click(btn);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            expect(sendQuestionMock).not.toHaveBeenCalled();
            expect(document.querySelector('[data-mcp-ask-scope-label]').textContent)
                .toBe('Selected record: QUO-CONTRACT-802 (Quotation)');
        });

        it('only identifiers (resource_type/record_id) enter the untrusted record_context payload — never the label', async () => {
            const btn = mountAskBillButton({
                resourceType: 'invoice',
                recordId: '801',
                label: 'INV-CONTRACT-801 (Invoice)',
            });
            click(btn);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            await window.AskAi.sendTurn('What is the reference number for this bill?');
            const sent = sendQuestionMock.mock.calls[0][0].recordContext;

            expect(sent).toEqual({
                version: 1,
                origin: 'explicit_selection',
                query: 'invoice #801',
                references: [{ resource_type: 'invoice', record_id: '801' }],
            });
            expect(Object.keys(sent.references[0])).toEqual(['resource_type', 'record_id']);
            expect(JSON.stringify(sent)).not.toContain('INV-CONTRACT-801');
            expect(JSON.stringify(sent)).not.toContain('label');
        });

        it('removing the staged chip detaches the record context — a click never confers authority on its own', async () => {
            const btn = mountAskBillButton();
            click(btn);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            window.McpAskAiComposer.reset();
            await window.AskAi.sendTurn('Tell me about this bill');
            expect(sendQuestionMock.mock.calls[0][0].recordContext).toBeNull();
        });

        it('retry/regenerate preserve the selected Bill across turns', async () => {
            const btn = mountAskBillButton({
                resourceType: 'quotation',
                recordId: '802',
                label: 'QUO-CONTRACT-802 (Quotation)',
            });
            click(btn);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            await window.AskAi.sendTurn("What is this bill's reference?");

            const expectedContext = {
                version: 1,
                origin: 'explicit_selection',
                query: 'quotation #802',
                references: [{ resource_type: 'quotation', record_id: '802' }],
            };
            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            expect(assistant.retryDescriptor.record_context).toEqual(expectedContext);

            sendQuestionMock.mockClear();
            await window.AskAi.tryAgain(assistant.id);
            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(expectedContext);

            sendQuestionMock.mockClear();
            sendQuestionMock.mockImplementation(async ({ onDone, onToken }) => {
                onToken('Replacement');
                onDone({ thread_id: 'abc12345678901234567890123456789012', exchange_id: 'c'.repeat(32) });
                return { terminal: 'done' };
            });
            const latest = window.AskAi.messages.find((m) => m.role === 'assistant');
            await window.AskAi.regenerate(latest.id);
            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual(expectedContext);
        });

        it('explicit record suppresses the ambient "Jobs on bill" page context end-to-end (Phase 1B precedence on this new surface)', async () => {
            // Simulates the real bill.show DOM: the ambient jobs-list owner
            // (show-bill-component.blade.php's data-mcp-page-context-owner="jobs",
            // route bill.show, kind embedded_list) is present AND the new button
            // is clicked — the explicit selection must win, never both.
            window.McpPageContext.PageContextRegistry.captureForSubmit.mockReturnValue({
                version: 1,
                context_id: 'jobs:bill:42',
                route: 'bill.show',
                kind: 'embedded_list',
                resource_type: 'work_order',
                record_ids: ['10'],
                scope_id: '42',
            });

            const btn = mountAskBillButton({
                resourceType: 'invoice',
                recordId: '42',
                label: 'INV-42 (Invoice)',
            });
            click(btn);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            await window.AskAi.sendTurn("What is this invoice's customer name?");

            expect(sendQuestionMock.mock.calls[0][0].recordContext).toEqual({
                version: 1,
                origin: 'explicit_selection',
                query: 'invoice #42',
                references: [{ resource_type: 'invoice', record_id: '42' }],
            });
            // Task 1B mechanism (sendTurn: recordContext ? null : currentSnapshot)
            // doing its job on this new dual-context surface: the ambient "Jobs
            // on bill" snapshot is suppressed entirely, never sent alongside it.
            expect(sendQuestionMock.mock.calls[0][0].pageContext).toBeNull();
            const assistant = window.AskAi.messages.find((m) => m.role === 'assistant');
            expect(assistant.retryDescriptor.page_context).toBeNull();
            expect(assistant.retryDescriptor.context_mode).toBe('none');
        });

        it('a click with no rag availability is a no-op (no chip staged, sidebar not force-opened)', async () => {
            document.getElementById('mcp-ask-ai-root').dataset.askAiAvailable = '0';
            const btn = mountAskBillButton();
            click(btn);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

            expect(sendQuestionMock).not.toHaveBeenCalled();
            expect(document.querySelector('[data-record-context-remove]')).toBeNull();
        });
    });

    describe('waitForLoaderPaint', () => {
        afterEach(() => {
            vi.unstubAllGlobals();
            vi.restoreAllMocks();
        });

        it('resolves when the tab goes hidden while the frame it waits for is still pending', async () => {
            // A hidden tab never paints, so the two frames requested while visible never fire.
            vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
            vi.stubGlobal('requestAnimationFrame', () => 1);
            let settled = false;
            const wait = window.AskAi._test.waitForLoaderPaint().then(() => { settled = true; });
            await Promise.resolve();
            expect(settled).toBe(false);

            vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
            document.dispatchEvent(new Event('visibilitychange'));
            await wait;

            expect(settled).toBe(true);
        });
    });

    // T-0.1 — the correlation-ID generator must fail loudly, not silently
    // return zeros, when crypto.getRandomValues is unavailable (F27).
    describe('randomRunId', () => {
        afterEach(() => {
            vi.unstubAllGlobals();
        });

        it('throws instead of silently minting an all-zero id when crypto.getRandomValues is unavailable', () => {
            vi.stubGlobal('crypto', undefined);

            expect(() => window.AskAi._test.randomRunId()).toThrow();
        });

        it('throws instead of silently minting an all-zero id when crypto exists but getRandomValues does not', () => {
            vi.stubGlobal('crypto', {});

            expect(() => window.AskAi._test.randomRunId()).toThrow();
        });

        it('never returns the all-zero id that the old optional-chaining fallback produced', () => {
            vi.stubGlobal('crypto', undefined);

            let thrown = null;
            let id = null;
            try {
                id = window.AskAi._test.randomRunId();
            } catch (err) {
                thrown = err;
            }

            expect(thrown).not.toBeNull();
            expect(id).not.toBe('0'.repeat(32));
        });

        it('returns exactly the bytes crypto.getRandomValues produced, as 32 lowercase hex chars', () => {
            // Independent oracle: hand-picked bytes 0x01..0x10 encode to this
            // exact literal string, computed by hand, not read back from the
            // implementation under test.
            const fixedBytes = Uint8Array.from(
                { length: 16 },
                (_, i) => i + 1
            );
            vi.stubGlobal('crypto', {
                getRandomValues: (arr) => {
                    arr.set(fixedBytes);
                    return arr;
                },
            });

            const id = window.AskAi._test.randomRunId();

            expect(id).toBe('0102030405060708090a0b0c0d0e0f10');
            expect(id).toMatch(/^[a-f0-9]{32}$/);
        });

        it('does not fall back to a predictable Date.now()-derived id', () => {
            vi.stubGlobal('crypto', undefined);
            const dateNowSpy = vi.spyOn(Date, 'now');

            expect(() => window.AskAi._test.randomRunId()).toThrow();
            expect(dateNowSpy).not.toHaveBeenCalled();
        });

        it('surfaces a visible stream error via sendTurn instead of silently sending a broken run id', async () => {
            vi.stubGlobal('crypto', undefined);

            await window.AskAi.sendTurn('What are your hours?');

            expect(sendQuestionMock).not.toHaveBeenCalled();
            const assistant = window.AskAi.messages[1];
            expect(assistant.error).toBe(
                'Ask Assistant requires a secure browser context. Use HTTPS or a browser with Web Crypto available.'
            );
            expect(assistant.streaming).toBe(false);
        });
    });

    it('announces the turn to modules before the request goes out', async () => {
        const seen = [];
        window.addEventListener('mcp:askai:turn-started', (e) => seen.push({ ...e.detail, at: 'event' }));
        sendQuestionMock.mockImplementation(async ({ runId, onToken, onDone }) => {
            seen.push({ runId, at: 'request' });
            onToken('Answer'); onDone({ thread_id: 'abc12345678901234567890123456789012' });
        });
        await window.AskAi.sendTurn('How many jobs do we have?');
        expect(seen[0].at).toBe('event');
        expect(seen[0].messageId).toBe(window.AskAi.messages.at(-1).id);
        expect(seen[1]).toEqual({ runId: seen[0].runId, at: 'request' });
    });

    it('copies the registered text and shows Copied for a bounded time', async () => {
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
        window.McpAskAiCopyText = (id) => 'Unpaid jobs · Kilimanjaro Traders\n1 of 4 shown\nJob\tBalance\n24694\t7,940,000';
        await window.AskAi.sendTurn('Show unpaid jobs for Kilimanjaro Traders');
        const btn = document.querySelector('[data-copy]');
        btn.click();
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        expect(writeText).toHaveBeenCalledWith(window.McpAskAiCopyText('x'));
        expect(document.querySelector('.mcp-ask-ai__copied').textContent).toBe('Copied');
        expect(document.querySelector('#mcp-ask-ai-sr').textContent).toBe('Copied');
        expect(btn.getAttribute('data-mcp-tip')).toBe('Copied');
        vi.advanceTimersByTime(1500);
        expect(document.querySelector('.mcp-ask-ai__copied')).toBeNull();
        expect(btn.getAttribute('data-mcp-tip')).toBe('Copy');
        delete window.McpAskAiCopyText;
        vi.useRealTimers();
    });

    it('falls back to the message content when no copy text is registered', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
        await window.AskAi.sendTurn('How many jobs do we have?');
        document.querySelector('[data-copy]').click();
        await Promise.resolve(); await Promise.resolve();
        expect(writeText).toHaveBeenCalledWith('Answer');
    });
});
