import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { escapeHtml } from '../../public/js/mcp-ai-core.js';

describe('Cycle UX-4B: "Explain Query" Trust Drawer UI Component & XSS Guard', () => {
    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();

        window.matchMedia = vi.fn((query) => ({
            matches: false,
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));

        window.McpAiCore = {
            escapeHtml,
            isSameOriginAppPath: (url) => typeof url === 'string' && url.startsWith('/') && !url.startsWith('//'),
            renderMarkdown: (t) => '<p>' + escapeHtml(t) + '</p>',
            sendQuestion: vi.fn(),
            sha256Hex: vi.fn(),
            scheduleSrBatch: () => ({ start: () => {}, flush: () => {}, stop: () => {} }),
        };

        document.body.innerHTML = `
            <aside id="mcpAskAiSidebar">
                <div id="mcp-ask-ai-root" data-rag-enabled="1">
                    <div class="mcp-ask-ai__thread"></div>
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
        vi.restoreAllMocks();
    });

    it('test_trust_drawer_and_verified_badge_are_suppressed', async () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));

        const explanation = {
            plain_english: 'Showing unpaid invoices for customer Acme Holdings created in the last 30 days.',
            applied_filters: ['Customer: Acme Holdings', 'Status: Unpaid', 'Date >= 2026-07-21'],
            matched_records_count: 3,
            verified_badge: false,
            execution_time_ms: 8,
        };

        window.AskAi._test.setMessages([
            {
                id: 'msg-trust-1',
                role: 'assistant',
                content: 'Found 3 invoices for Acme Holdings.',
                streaming: false,
                recordLinks: [
                    { table: 'bills', record_id: 1042, label: 'INV-1042', url: '/bills/show/1042' },
                ],
                sqlExplanation: explanation,
            },
        ]);

        window.AskAi._test.renderThread();

        // Verified Query Badge and Trust Toggle are intentionally removed from UI
        const badge = thread.querySelector('.mcp-ask-ai__trust-badge');
        expect(badge).toBeNull();

        const toggle = thread.querySelector('.mcp-ask-ai__trust-toggle');
        expect(toggle).toBeNull();

        const drawer = thread.querySelector('.mcp-ask-ai__trust-drawer');
        expect(drawer).toBeNull();
    });
});

