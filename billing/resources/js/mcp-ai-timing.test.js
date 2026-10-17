import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

const STREAM_START = 1000;
const STATUS_AT = 1100;
const TOKEN_ARRIVES_AT = 1500;
const PAINT_AT = 1800;
const DONE_AT = 2000;
const TOKEN_DELTA_MS = TOKEN_ARRIVES_AT - STREAM_START;
const EXPECTED_UI_FIRST_MS = PAINT_AT - STREAM_START;
const EXPECTED_COMPLETION_MS = DONE_AT - STREAM_START;

function buildFixtureBody() {
    const lines = [
        ': ping',
        '',
        'event: status',
        'data: {"phase":"searching_documents","label":"Searching"}',
        '',
        'event: token',
        'data: {"d":"Hello"}',
        '',
        'event: done',
        'data: {"model":"test","trace_id":"' + 'a'.repeat(32) + '","feedback_token":"signed-token"}',
        '',
    ];
    return lines.join('\n');
}

/** Mirrors mcp-ask-ai.js makeRevealer stamp-on-first-paint wrapper. */
function makeRevealerWithStamp(StreamUi, assistant, uiFirstTextRef) {
    let stamped = false;
    return StreamUi.makeRevealer(assistant, () => {
        if (!stamped && String(assistant.content || '').trim()) {
            stamped = true;
            uiFirstTextRef?.stamp?.();
        }
    });
}

describe('consumeSse UI timing (T-1b.5)', () => {
    let rafQueue;

    beforeEach(() => {
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
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    function drainRaf() {
        const cbs = rafQueue.filter(Boolean);
        rafQueue = [];
        cbs.forEach((cb) => cb(0));
    }

    it('records ui_first_text at revealer paint, not SSE token or status', async () => {
        vi.useFakeTimers();
        let now = STREAM_START;
        vi.spyOn(performance, 'now').mockImplementation(() => now);

        await import('../../public/js/mcp-ask-ai-stream-ui.js');
        const { consumeSse } = await import('../../public/js/mcp-ai-core.js');
        const StreamUi = window.McpAskAiStreamUi;

        const encoder = new TextEncoder();
        let pushed = false;
        const reader = {
            async read() {
                if (pushed) {
                    return { value: undefined, done: true };
                }
                pushed = true;
                now = DONE_AT;
                return { value: encoder.encode(buildFixtureBody()), done: false };
            },
            cancel: vi.fn(),
        };

        const assistant = { content: '' };
        const uiFirstTextRef = { ms: null };
        const revealer = makeRevealerWithStamp(StreamUi, assistant, uiFirstTextRef);
        const timings = [];

        await consumeSse(reader, {
            uiFirstTextRef,
            onToken: (chunk) => {
                now = TOKEN_ARRIVES_AT;
                revealer.push(chunk);
            },
            onStatus: () => {
                now = STATUS_AT;
            },
            beforeEmitTimings: async () => {
                now = PAINT_AT;
                drainRaf();
                if (uiFirstTextRef.ms === null) {
                    revealer.flush();
                }
                now = DONE_AT;
            },
            onTimings: (payload) => timings.push(payload),
        });

        expect(timings).toHaveLength(1);
        expect(uiFirstTextRef.ms).toBe(EXPECTED_UI_FIRST_MS);
        expect(timings[0].ui_first_text_ms).toBe(EXPECTED_UI_FIRST_MS);
        expect(timings[0].ui_first_text_ms).toBeGreaterThan(TOKEN_DELTA_MS);
        expect(timings[0].completion_latency_ms).toBe(EXPECTED_COMPLETION_MS);
        expect(timings[0].ui_first_text_ms).toBeLessThan(timings[0].completion_latency_ms);
        expect(String(assistant.content || '').trim().length).toBeGreaterThan(0);
        vi.useRealTimers();
    });
});
