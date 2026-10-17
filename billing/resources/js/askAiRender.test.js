import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
    isSameOriginAppPath,
    escapeHtml,
} from '../../public/js/mcp-ai-core.js';

describe('isSameOriginAppPath', () => {
    it('accepts a single-leading-slash app path', () => {
        expect(isSameOriginAppPath('/jobs/42')).toBe(true);
        expect(isSameOriginAppPath('/jobs/show/1')).toBe(true);
        expect(isSameOriginAppPath('/work-orders/1')).toBe(true);
    });

    it('rejects protocol-relative, backslash, controls, non-strings, and encoded confusion', () => {
        expect(isSameOriginAppPath('//evil')).toBe(false);
        expect(isSameOriginAppPath('//evil.example')).toBe(false);
        expect(isSameOriginAppPath('\\evil')).toBe(false);
        expect(isSameOriginAppPath('/\\evil')).toBe(false);
        expect(isSameOriginAppPath('javascript:alert(1)')).toBe(false);
        expect(isSameOriginAppPath('/foo\u0000bar')).toBe(false);
        expect(isSameOriginAppPath('/%2f%2fevil.example')).toBe(false);
        expect(isSameOriginAppPath('/javascript%3Aalert(1)')).toBe(false);
        expect(isSameOriginAppPath('https://evil.example/x')).toBe(false);
        expect(isSameOriginAppPath('')).toBe(false);
        expect(isSameOriginAppPath(null)).toBe(false);
        expect(isSameOriginAppPath(12)).toBe(false);
    });
});

describe('paced reveal + record links', () => {
    let rafQueue;

    beforeEach(async () => {
        rafQueue = [];
        vi.stubGlobal('requestAnimationFrame', (cb) => {
            rafQueue.push(cb);
            return rafQueue.length;
        });
        vi.stubGlobal('cancelAnimationFrame', (id) => {
            rafQueue[id - 1] = null;
        });

        window.matchMedia = vi.fn(() => ({
            matches: false,
            media: '',
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));

        window.McpAiCore = {
            escapeHtml,
            isSameOriginAppPath,
            renderMarkdown: (t) => '<p>' + escapeHtml(t) + '</p>',
            sendQuestion: vi.fn(),
            sha256Hex: vi.fn(),
            scheduleSrBatch: () => ({ start: () => {}, flush: () => {}, stop: () => {} }),
        };

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

    function drainRaf(n = 50) {
        for (let i = 0; i < n && rafQueue.some(Boolean); i++) {
            const cbs = rafQueue.filter(Boolean);
            rafQueue = [];
            cbs.forEach((cb) => cb(0));
        }
    }

    it('does not paint a full 300-char chunk on the same frame; flush paints all', () => {
        const assistant = { content: '' };
        const paints = [];
        const revealer = window.AskAi._test.makeRevealer(assistant, () => paints.push(assistant.content));

        revealer.push('x'.repeat(300));
        expect(assistant.content).toBe('');
        expect(paints).toEqual([]);

        drainRaf(1);
        expect(assistant.content.length).toBeGreaterThan(0);
        expect(assistant.content.length).toBeLessThan(300);

        revealer.flush();
        expect(assistant.content).toBe('x'.repeat(300));
    });

    it('flushes pending text before error render', () => {
        const assistant = { content: '' };
        const revealer = window.AskAi._test.makeRevealer(assistant, () => {});
        revealer.push('partial answer text that should survive error');
        revealer.flush();
        expect(assistant.content).toContain('partial answer');
    });

    it('renders database evidence inline and escapes labels; rejects unsafe urls', () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));
        window.AskAi._test.setMessages([
            {
                id: 'msg-1',
                role: 'assistant',
                content: 'Here are the jobs.',
                streaming: false,
                sources: [],
                recordLinks: [
                    { table: 'work_orders', record_id: 1, label: 'Job <script>', url: '/jobs/1' },
                    { table: 'work_orders', record_id: 2, label: 'Evil', url: '//evil.example' },
                    { table: 'work_orders', record_id: 3, label: 'Proto', url: 'javascript:alert(1)' },
                ],
            },
        ]);
        window.AskAi._test.renderThread();

        const html = thread.innerHTML;
        expect(html).toContain('href="/jobs/1"');
        expect(html).toContain('Job &lt;script&gt;');
        expect(html).not.toContain('href="//evil.example"');
        expect(html).not.toContain('javascript:');
        expect(html).not.toContain('data-citation-marker="1"');
        expect(html).toContain('Referenced records (1)');
        expect(html).not.toContain('mcp-ask-ai__records');
    });

    it('never links a numeric-only label in prose, but lists it with its type', () => {
        // Oracle: scope spec §3.5 — prose candidates need a letter; a
        // numeric-only label never wraps an amount in the answer text. The
        // referenced-records list shows it because the type makes it readable.
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));
        window.AskAi._test.setMessages([{
            id: 'msg-amount',
            role: 'assistant',
            content: 'Job WO-1 totals 7940000.',
            streaming: false,
            sources: [],
            recordLinks: [
                { table: 'work_orders', record_id: 1, label: '7940000', url: '/jobs/1' },
                { table: 'work_orders', record_id: 2, label: 'WO-1', url: '/jobs/2' },
            ],
        }]);
        window.AskAi._test.renderThread();

        const body = thread.querySelector('.mcp-ask-ai__msg-body');
        expect(body.querySelector('a[href="/jobs/2"]')).not.toBeNull();
        expect(body.querySelector('a[href="/jobs/1"]')).toBeNull();

        const list = thread.querySelector('.mcp-ask-ai__record-refs-list');
        expect(list).not.toBeNull();
        expect(list.querySelector('a[href="/jobs/1"]')).not.toBeNull();
        expect(list.querySelector('a[href="/jobs/2"]')).not.toBeNull();
    });

    it('shows a record label with markup as text in the referenced-records list', () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));
        window.AskAi._test.setMessages([{
            id: 'msg-escaped-label',
            role: 'assistant',
            content: 'See the record.',
            streaming: false,
            sources: [],
            recordLinks: [{ table: 'customer', record_id: 9, label: '<b>Blackstone</b>', url: '/customer/show/9' }],
        }]);
        window.AskAi._test.renderThread();

        const link = thread.querySelector('.mcp-ask-ai__record-refs-list a[href="/customer/show/9"]');
        expect(link).not.toBeNull();
        expect(link.textContent).toBe('Customer <b>Blackstone</b>');
        expect(link.querySelector('b')).toBeNull();
    });

    it('renders document source markers as inline evidence cards', () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));
        window.AskAi._test.setMessages([{
            id: 'msg-doc',
            role: 'assistant',
            content: 'The press opens at 8am [Source 1].',
            streaming: false,
            sources: [{ id: 'handbook:2', title: 'Handbook', snippet: 'The press opens at 8am.' }],
            citations: { parsed: true, cited: [{ marker: 1, id: 'handbook:2' }] },
            recordLinks: [],
        }]);

        window.AskAi._test.renderThread();

        const marker = thread.querySelector('[data-citation-marker="1"]');
        const card = thread.querySelector('.mcp-ask-ai__citation-card');
        expect(marker).toBeTruthy();
        expect(thread.textContent).toContain('Handbook');
        expect(thread.textContent).toContain('The press opens at 8am.');
        expect(thread.querySelector('.mcp-ask-ai__sources')).toBeNull();

        thread.getBoundingClientRect = () => ({ left: 0, top: 0, right: 1000, bottom: 700, width: 1000, height: 700 });
        marker.getBoundingClientRect = () => ({ left: 400, top: 200, right: 420, bottom: 220, width: 20, height: 20 });
        card.getBoundingClientRect = () => ({ left: 0, top: 0, right: 320, bottom: 150, width: 320, height: 150 });
        marker.focus();
        expect(marker.getAttribute('aria-expanded')).toBe('true');
        expect(card.style.left).toBe('380px');
        expect(card.style.top).toBe('228px');
        let escapedToDocument = false;
        const onDocumentEscape = () => { escapedToDocument = true; };
        document.addEventListener('keydown', onDocumentEscape);
        marker.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        expect(marker.getAttribute('aria-expanded')).toBe('false');
        expect(escapedToDocument).toBe(false);
        document.removeEventListener('keydown', onDocumentEscape);
    });

    it('explains when document sources cannot be linked to individual claims', () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));
        window.AskAi._test.setMessages([{
            id: 'msg-unmatched-source',
            role: 'assistant',
            content: 'Our standard business hours are 8am to 5pm.',
            streaming: false,
            sources: [{ id: 'handbook:2', title: 'Handbook', snippet: 'Business hours.' }],
            citations: { parsed: false, cited: [] },
            recordLinks: [],
        }]);

        window.AskAi._test.renderThread();

        const note = thread.querySelector('.mcp-ask-ai__citation-note');
        expect(note?.textContent).toContain('Source links unavailable');
        expect(note?.textContent).toContain('individual claims could not be linked automatically');
        expect(note?.textContent).not.toContain('matched reliably');
    });

    it('styles interrupted-answer recovery with the shared button system', () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));
        window.AskAi._test.setMessages([{
            id: 'msg-interrupted',
            role: 'assistant',
            content: '',
            streaming: false,
            error: 'Answer interrupted',
            sources: [],
            citations: { parsed: true, cited: [] },
            recordLinks: [],
            retryDescriptor: { question: 'What are your hours?' },
        }]);

        window.AskAi._test.renderThread();

        const button = thread.querySelector('[data-try-again="msg-interrupted"]');
        expect(button?.classList.contains('mcp-btn')).toBe(true);
        expect(button?.classList.contains('mcp-btn--secondary')).toBe(true);
        expect(button?.classList.contains('mcp-ask-ai__try-again')).toBe(true);
        expect(button?.textContent).toContain('Try again');
    });

    it('defaults loader label to Understanding your question when no phases', () => {
        const html = window.AskAi._test.renderThinkingContent({ phases: [] });
        expect(html).toContain('Understanding your question');
        expect(html).toContain('mcp-ask-ai__loading-label');
    });
});
