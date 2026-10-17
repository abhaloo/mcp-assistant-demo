/**
 * The usage meter beside the mode dropdown: a bar of the person's own monthly
 * Ask AI spend, painted from the figure the panel carried on load and refreshed
 * from the credits endpoint once a turn's stream has closed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function panel(credits) {
    document.body.innerHTML = `
        <div id="mcp-ask-ai-root" data-credits-url="/api/ai/credits" ${credits ? `data-credits='${JSON.stringify(credits)}'` : ''}>
            <div class="mcp-ask-ai__meta">
                <div data-mcp-ask-mode></div>
                <div class="mcp-ask-ai__usage" data-mcp-ask-usage hidden></div>
            </div>
        </div>`;
    return document.getElementById('mcp-ask-ai-root');
}

const meter = () => document.querySelector('[data-mcp-ask-usage]');

beforeEach(async () => {
    vi.resetModules();
    await import('../../public/js/mcp-ask-ai-usage-meter.js');
});

afterEach(() => {
    document.body.innerHTML = '';
    vi.useRealTimers();
    vi.restoreAllMocks();
    delete window.McpAskAiUsageMeter;
});

describe('usage meter', () => {
    it('paints the monthly-use line from the figure the panel carried', () => {
        window.McpAskAiUsageMeter.init(panel({ pct: 12.4, reset_date: '2026-10-01' }));

        expect(meter().hidden).toBe(false);
        expect(meter().querySelector('.mcp-ask-ai__usage-track')).toBeNull();
        expect(meter().querySelector('.mcp-ask-ai__usage-fill')).toBeNull();
        expect(meter().textContent).toBe('12% of monthly use');
        expect(meter().title).toBe('Resets 1 Oct');
        expect(meter().textContent).not.toContain('$');
        expect(meter().textContent).not.toContain('1 Oct');
    });

    it('stays hidden when the panel carried no figure', () => {
        window.McpAskAiUsageMeter.init(panel(null));

        expect(meter().hidden).toBe(true);
    });

    it('marks the line near the cap from 80 percent and full at 100', () => {
        const root = panel({ pct: 80, reset_date: '2026-10-01' });
        window.McpAskAiUsageMeter.init(root);
        expect(meter().classList.contains('is-near')).toBe(true);
        expect(meter().classList.contains('is-full')).toBe(false);
        expect(meter().textContent).toBe('80% of monthly use');

        window.McpAskAiUsageMeter.paint({ pct: 100, reset_date: '2026-10-01' });
        expect(meter().classList.contains('is-full')).toBe(true);
        expect(meter().classList.contains('is-near')).toBe(false);
        expect(meter().textContent).toBe('100% of monthly use');
        expect(meter().querySelector('.mcp-ask-ai__usage-fill')).toBeNull();
    });

    it('refresh fetches the credits endpoint and repaints', async () => {
        const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValue({
            ok: true,
            json: async () => ({ pct: 12.9, reset_date: '2026-10-01' }),
        });
        window.McpAskAiUsageMeter.init(panel({ pct: 12.4, reset_date: '2026-10-01' }));

        await window.McpAskAiUsageMeter.refresh();

        expect(fetchMock).toHaveBeenCalledWith('/api/ai/credits', expect.objectContaining({ credentials: 'same-origin' }));
        expect(meter().textContent).toContain('13% of monthly use');
    });

    it('after a completed turn a figure that has not moved is read once more', async () => {
        vi.useFakeTimers();
        const fetchMock = vi.spyOn(window, 'fetch')
            .mockResolvedValueOnce({ ok: true, json: async () => ({ pct: 12.4, reset_date: '2026-10-01' }) })
            .mockResolvedValueOnce({ ok: true, json: async () => ({ pct: 13.1, reset_date: '2026-10-01' }) });
        window.McpAskAiUsageMeter.init(panel({ pct: 12.4, reset_date: '2026-10-01' }));

        const pending = window.McpAskAiUsageMeter.refresh({ afterTurn: true });
        await vi.advanceTimersByTimeAsync(1500);
        await pending;

        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(meter().textContent).toContain('13% of monthly use');
    });

    it('after a completed turn a figure that moved on the first read is not read again', async () => {
        vi.useFakeTimers();
        const fetchMock = vi.spyOn(window, 'fetch')
            .mockResolvedValue({ ok: true, json: async () => ({ pct: 13.1, reset_date: '2026-10-01' }) });
        window.McpAskAiUsageMeter.init(panel({ pct: 12.4, reset_date: '2026-10-01' }));

        const pending = window.McpAskAiUsageMeter.refresh({ afterTurn: true });
        await vi.advanceTimersByTimeAsync(1500);
        await pending;

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(meter().textContent).toContain('13% of monthly use');
    });

    it('a slow older read never paints over a newer figure', async () => {
        let releaseOld;
        const old = new Promise((resolve) => { releaseOld = resolve; });
        vi.spyOn(window, 'fetch')
            .mockImplementationOnce(() => old)
            .mockResolvedValueOnce({ ok: true, json: async () => ({ pct: 20, reset_date: '2026-10-01' }) });
        window.McpAskAiUsageMeter.init(panel({ pct: 12.4, reset_date: '2026-10-01' }));

        const first = window.McpAskAiUsageMeter.refresh();
        await window.McpAskAiUsageMeter.refresh();
        expect(meter().textContent).toContain('20% of monthly use');

        releaseOld({ ok: true, json: async () => ({ pct: 15, reset_date: '2026-10-01' }) });
        await first;

        expect(meter().textContent).toContain('20% of monthly use');
    });

    it('a failed refresh keeps the last figure', async () => {
        vi.spyOn(console, 'warn').mockImplementation(() => {});
        vi.spyOn(window, 'fetch').mockRejectedValue(new Error('offline'));
        window.McpAskAiUsageMeter.init(panel({ pct: 12.4, reset_date: '2026-10-01' }));

        await window.McpAskAiUsageMeter.refresh();

        expect(meter().textContent).toContain('12% of monthly use');
        expect(meter().textContent).not.toContain('$');
    });
});
