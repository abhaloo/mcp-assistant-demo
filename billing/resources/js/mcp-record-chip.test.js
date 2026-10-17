import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { escapeHtml } from '../../public/js/mcp-ai-core.js';

describe('Cycle UX-2B: Interactive Record Chip & XSS-Safe Rich Hover Tooltip', () => {
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
            renderMarkdown: (t) => {
                // Mock simple link parsing
                return t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
            },
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

    it('test_record_chip_renders_hover_card_with_xss_sanitization', async () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));

        const previewData = {
            resource_type: 'invoice',
            title: 'INV-1042',
            subtitle: 'Acme Holdings',
            status: 'Pending',
            badge_variant: 'warning',
            amount: 'KES 145,000',
            updated_at: '2026-08-19',
        };

        window.AskAi._test.setMessages([
            {
                id: 'msg-rec-1',
                role: 'assistant',
                content: 'Found invoice [INV-1042](/bills/show/1042) for Acme.',
                streaming: false,
                recordLinks: [
                    {
                        table: 'bills',
                        record_id: 1042,
                        label: 'INV-1042',
                        url: '/bills/show/1042',
                        preview: previewData,
                    },
                ],
            },
        ]);

        window.AskAi._test.renderThread();

        const chip = thread.querySelector('.mcp-ask-ai__record-chip');
        expect(chip).toBeTruthy();
        expect(chip.getAttribute('href')).toBe('/bills/show/1042');
        expect(chip.textContent).toContain('INV-1042');

        // Trigger mouseenter / pointerenter on the chip
        chip.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));

        const hoverCard = thread.querySelector('.mcp-ask-ai__hover-card');
        expect(hoverCard).toBeTruthy();
        expect(hoverCard.getAttribute('role')).toBe('tooltip');
        expect(hoverCard.textContent).toContain('INV-1042');
        expect(hoverCard.textContent).toContain('Acme Holdings');
        expect(hoverCard.textContent).toContain('KES 145,000');
        expect(hoverCard.textContent).toContain('Pending');

        const badge = hoverCard.querySelector('.mcp-ask-ai__badge--warning');
        expect(badge).toBeTruthy();
        expect(badge.textContent).toBe('Pending');

        // Action buttons
        expect(hoverCard.querySelector('[data-action="open-record"]')).toBeTruthy();
        expect(hoverCard.querySelector('[data-action="copy-link"]')).toBeTruthy();
    });

    it('sanitizes malicious script and img tags in preview dynamic fields without XSS execution', async () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));

        const maliciousPreview = {
            resource_type: 'invoice',
            title: '<script>alert(1)</script>',
            subtitle: '<img src=x onerror=alert(1)>',
            amount: '<b onmouseover=alert(1)>100</b>',
            status: '<svg onload=alert(1)>Active</svg>',
            badge_variant: 'success',
        };

        window.AskAi._test.setMessages([
            {
                id: 'msg-xss-rec',
                role: 'assistant',
                content: 'Check [Suspicious Record](/bills/show/999) here.',
                streaming: false,
                recordLinks: [
                    {
                        table: 'bills',
                        record_id: 999,
                        label: 'Suspicious Record',
                        url: '/bills/show/999',
                        preview: maliciousPreview,
                    },
                ],
            },
        ]);

        window.AskAi._test.renderThread();

        const chip = thread.querySelector('.mcp-ask-ai__record-chip');
        expect(chip).toBeTruthy();
        chip.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));

        const hoverCard = thread.querySelector('.mcp-ask-ai__hover-card');
        expect(hoverCard).toBeTruthy();

        // Strict XSS assertion: zero executable elements created in DOM
        expect(hoverCard.querySelector('script')).toBeNull();
        expect(hoverCard.querySelector('img')).toBeNull();
        expect(hoverCard.querySelector('svg')).toBeNull();

        // Raw text content must be safely preserved as textContent
        expect(hoverCard.textContent).toContain('<script>alert(1)</script>');
        expect(hoverCard.textContent).toContain('<img src=x onerror=alert(1)>');
        expect(hoverCard.textContent).toContain('<b onmouseover=alert(1)>100</b>');
    });
});
