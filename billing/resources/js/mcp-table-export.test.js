import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { escapeHtml } from '../../public/js/mcp-ai-core.js';

describe('Cycle UX-5B: Table TSV Copy, Contextual Markdown Copy & RFC 4180 Anti-Formula CSV Export', () => {
    let clipboardText = '';

    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();
        clipboardText = '';

        window.matchMedia = vi.fn((query) => ({
            matches: false,
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));

        Object.assign(navigator, {
            clipboard: {
                writeText: vi.fn().mockImplementation((text) => {
                    clipboardText = text;
                    return Promise.resolve();
                }),
            },
        });

        // Mock URL.createObjectURL
        window.URL.createObjectURL = vi.fn().mockReturnValue('blob:mock-url');
        window.URL.revokeObjectURL = vi.fn();

        window.McpAiCore = {
            escapeHtml,
            isSameOriginAppPath: (url) => typeof url === 'string' && url.startsWith('/') && !url.startsWith('//'),
            renderMarkdown: (t) => {
                // Return simple table html for testing
                return `
                    <table>
                        <thead>
                            <tr><th>Invoice #</th><th>Customer</th><th>Amount</th><th>Formula</th></tr>
                        </thead>
                        <tbody>
                            <tr><td>INV-101</td><td>Acme Corp</td><td>KES 145,000</td><td> =1+1</td></tr>
                            <tr><td>INV-102</td><td>Beta LLC</td><td>KES 200,000</td><td>@SUM(A1:A10)</td></tr>
                            <tr><td>INV-103</td><td>Gamma Inc</td><td>KES 50,000</td><td> -50</td></tr>
                            <tr><td>INV-104</td><td>Delta, Co</td><td>KES 80,000</td><td>|calc</td></tr>
                            <tr><td>INV-105</td><td>Epsilon "Quote"</td><td>KES 90,000</td><td>%00</td></tr>
                        </tbody>
                    </table>
                `;
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

    it('test_table_export_tsv_clipboard_and_rfc4180_formula_sanitization', async () => {
        const thread = document.querySelector('.mcp-ask-ai__thread');
        window.AskAi._test.setThreadEl(thread);
        window.AskAi._test.setRoot(document.getElementById('mcp-ask-ai-root'));

        // 1. Streaming State Guard: when msg.streaming === true, table action buttons are NOT mounted
        window.AskAi._test.setMessages([
            {
                id: 'msg-stream-table',
                role: 'assistant',
                question: 'Show all unpaid invoices',
                content: '| Invoice # | Customer | Amount |\n|---|---|---|',
                streaming: true,
            },
        ]);
        window.AskAi._test.renderThread();
        expect(thread.querySelector('.mcp-ask-ai__table-actions')).toBeNull();

        // 2. Completed Turn: when msg.streaming === false, table action buttons ARE mounted
        window.AskAi._test.setMessages([
            {
                id: 'msg-completed-table',
                role: 'assistant',
                question: 'How many invoices are overdue?',
                content: '**3 invoices** are overdue, totalling **KES 145,000.00**.',
                streaming: false,
                recordLinks: [],
            },
        ]);
        window.AskAi._test.renderThread();

        const tableWrap = thread.querySelector('.mcp-ask-ai__table-wrap');
        expect(tableWrap).toBeTruthy();

        const tableActions = tableWrap.querySelector('.mcp-ask-ai__table-actions');
        expect(tableActions).toBeTruthy();

        const copyTableBtn = tableActions.querySelector('[data-action="copy-table"]');
        const exportCsvBtn = tableActions.querySelector('[data-action="export-csv"]');
        expect(copyTableBtn).toBeTruthy();
        expect(exportCsvBtn).toBeTruthy();

        // 3. TSV Clipboard Copy
        copyTableBtn.click();
        await vi.waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled());
        expect(clipboardText).toContain("Invoice #\tCustomer\tAmount\tFormula\n");
        expect(clipboardText).toContain("INV-101\tAcme Corp\tKES 145,000\t=1+1");

        // 4. Message-level Copy
        const messageCopyBtn = thread.querySelector('[data-copy="msg-completed-table"]');
        expect(messageCopyBtn).toBeTruthy();
        messageCopyBtn.click();
        await vi.waitFor(() => expect(clipboardText).toBe('**3 invoices** are overdue, totalling **KES 145,000.00**.'));

        // 5. CSV Export with RFC 4180 & Formula Sanitization
        let createdBlob = null;
        global.Blob = vi.fn().mockImplementation((chunks, options) => {
            createdBlob = { chunks, options };
            return createdBlob;
        });

        exportCsvBtn.click();
        expect(global.Blob).toHaveBeenCalled();
        expect(createdBlob.options.type).toBe('text/csv;charset=utf-8;');
        expect(createdBlob.chunks[0]).toBe('\uFEFF'); // UTF-8 BOM

        const csvContent = createdBlob.chunks[1];
        // Formula prefix checks
        expect(csvContent).toContain("\"'=1+1\"");
        expect(csvContent).toContain("\"'@SUM(A1:A10)\"");
        expect(csvContent).toContain("\"'-50\"");
        expect(csvContent).toContain("\"'|calc\"");
        expect(csvContent).toContain("\"'%00\"");

        // RFC 4180 Quote Escaping: Epsilon "Quote" -> "Epsilon ""Quote"""
        expect(csvContent).toContain('"Epsilon ""Quote"""');
        // Comma escaping: Delta, Co -> "Delta, Co"
        expect(csvContent).toContain('"Delta, Co"');
    });
});
