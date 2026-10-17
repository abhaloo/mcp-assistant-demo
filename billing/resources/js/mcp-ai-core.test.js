import { describe, it, expect, vi, beforeEach } from 'vitest';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import {
    parseSseEvents,
    consumeSse,
    sendQuestion,
    renderMarkdown,
    mapPreStreamError,
    sha256Hex,
    scheduleSrBatch,
    isSameOriginAppPath,
} from '../../public/js/mcp-ai-core.js';

beforeEach(() => {
    global.window.marked = marked;
    global.window.DOMPurify = DOMPurify;
});

describe('parseSseEvents', () => {
    it('returns parsed events and the trailing partial as rest', () => {
        const buffer =
            'event: token\ndata: {"d":"Hello"}\n\n' +
            'event: token\ndata: {"d":" world"}\n\n' +
            'event: done\ndata: {"model":"x"}';

        const { events, rest } = parseSseEvents(buffer);

        expect(events).toEqual([
            { event: 'token', data: '{"d":"Hello"}' },
            { event: 'token', data: '{"d":" world"}' },
        ]);
        expect(rest).toBe('event: done\ndata: {"model":"x"}');
    });

    it('skips heartbeat comment blocks', () => {
        const buffer = ': ping\n\nevent: token\ndata: {"d":"hi"}\n\n';
        const { events } = parseSseEvents(buffer);
        expect(events).toEqual([{ event: 'token', data: '{"d":"hi"}' }]);
    });

    it('normalizes CRLF and CR and preserves multiline and empty data', () => {
        const { events, rest } = parseSseEvents(
            ': ping\r\nevent: token\rdata: {"d":"a"}\r\r' +
            'event: message\r\ndata: first\r\ndata:\r\ndata: third\r\n\r\n'
        );
        expect(rest).toBe('');
        expect(events).toEqual([
            { event: 'token', data: '{"d":"a"}' },
            { event: 'message', data: 'first\n\nthird' },
        ]);
    });

    it('flushes one unterminated terminal frame at EOF', () => {
        const { events, rest } = parseSseEvents(
            'event: done\ndata: {"model":"x"}',
            { eof: true }
        );
        expect(rest).toBe('');
        expect(events).toEqual([{ event: 'done', data: '{"model":"x"}' }]);
    });

    it('rejects oversized lines and events', () => {
        expect(() => parseSseEvents('data: ' + 'x'.repeat(20) + '\n\n', {
            maxLineBytes: 10,
        })).toThrow('sse_line_too_large');
        expect(() => parseSseEvents('data: ' + 'x'.repeat(20) + '\n\n', {
            maxEventBytes: 10,
        })).toThrow('sse_event_too_large');
    });
});

describe('renderMarkdown', () => {
    it('strips script handlers and keeps bold text', () => {
        const html = renderMarkdown('<img src=x onerror=alert(1)>**hi**');
        expect(html).not.toContain('onerror');
        expect(html).toContain('<strong>hi</strong>');
    });

    // Oracle: spec §4.10 gate 12 — the panel shows no clickable control the model
    // wrote; every anchor comes from a sealed link or citation card.
    it('strips model-written links and controls and keeps their text', () => {
        const html = renderMarkdown(
            '[Invoice E7413](/bills/show/999) and [docs](https://example.com)' +
            ' <form action="https://evil.example"><button>View</button></form>' +
            ' <map name="m"><area href="https://evil.example"></map><img usemap="#m">' +
            ' https://auto.example [1]: https://evil.example'
        );
        expect(html).toContain('Invoice E7413');
        expect(html).toContain('docs');
        expect(html).toContain('View');
        expect(html).not.toContain('<a');
        expect(html).not.toContain('<form');
        expect(html).not.toContain('<button');
        expect(html).not.toContain('<map');
        expect(html).not.toContain('<area');
        expect(html).not.toContain('<img');
        expect(html).not.toContain('href');
        expect(html).not.toContain('action');
    });
});

describe('consumeSse', () => {
    it('calls onError when the stream ends without done or error', async () => {
        const encoder = new TextEncoder();
        const stream = new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode('event: token\ndata: {"d":"partial"}\n\n'));
                controller.close();
            },
        });

        const onError = vi.fn();
        const terminal = await consumeSse(stream.getReader(), {
            onToken: () => {},
            onSources: () => {},
            onDone: () => {},
            onError,
        });

        expect(terminal).toBe('incomplete');
        expect(onError).toHaveBeenCalledWith(expect.objectContaining({
            error: 'incomplete_stream',
        }));
    });

    it('dispatches onSqlProvenance for sql_provenance frames', async () => {
        const sse =
            'event: sql_provenance\ndata: {"queries":[],"record_links":[{"table":"work_orders","record_id":1,"label":"Job 1","url":"/jobs/1"}]}\n\n' +
            'event: done\ndata: {"model":"m"}\n\n';
        const encoder = new TextEncoder();
        const stream = new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode(sse));
                controller.close();
            },
        });

        const onSqlProvenance = vi.fn();
        await consumeSse(stream.getReader(), {
            onToken: () => {},
            onSources: () => {},
            onSqlProvenance,
            onDone: () => {},
            onError: () => {},
        });

        expect(onSqlProvenance).toHaveBeenCalledWith(expect.objectContaining({
            record_links: [expect.objectContaining({ url: '/jobs/1' })],
        }));
    });

    it('decodes split UTF-8 and flushes an EOF terminal', async () => {
        const bytes = new TextEncoder().encode(
            'event: token\r\ndata: {"d":"café"}\r\n\r\nevent: done\r\ndata: {"model":"m"}'
        );
        const split = bytes.indexOf(0xc3) + 1;
        const reader = {
            read: vi.fn()
                .mockResolvedValueOnce({ value: bytes.slice(0, split), done: false })
                .mockResolvedValueOnce({ value: bytes.slice(split), done: false })
                .mockResolvedValueOnce({ done: true }),
            cancel: vi.fn().mockResolvedValue(undefined),
        };
        const onToken = vi.fn();
        const onDone = vi.fn();
        const terminal = await consumeSse(reader, {
            onToken, onDone, onError: vi.fn(),
        });
        expect(terminal).toBe('done');
        expect(onToken).toHaveBeenCalledWith('café');
        expect(onDone).toHaveBeenCalledOnce();
    });

    it('rejects malformed JSON and duplicate terminal events', async () => {
        const encoder = new TextEncoder();
        for (const sse of [
            'event: token\ndata: nope\n\n',
            'event: done\ndata: {}\n\nevent: done\ndata: {}\n\n',
        ]) {
            const reader = {
                read: vi.fn()
                    .mockResolvedValueOnce({ value: encoder.encode(sse), done: false })
                    .mockResolvedValueOnce({ done: true }),
                cancel: vi.fn().mockResolvedValue(undefined),
            };
            const onError = vi.fn();
            const terminal = await consumeSse(reader, { onError });
            expect(terminal).toBe('error');
            expect(onError).toHaveBeenCalled();
        }
    });

    it('throws AbortError when the turn is superseded during beforeEmitTimings', async () => {
        const encoder = new TextEncoder();
        const sse = 'event: done\ndata: {"model":"m"}\n\n';
        const reader = {
            read: vi.fn()
                .mockResolvedValueOnce({ value: encoder.encode(sse), done: false })
                .mockResolvedValueOnce({ done: true }),
            cancel: vi.fn().mockResolvedValue(undefined),
        };
        const controller = new AbortController();

        await expect(consumeSse(reader, {
            signal: controller.signal,
            shouldContinue: () => false,
            beforeEmitTimings: async () => {
                controller.abort();
            },
            onDone: vi.fn(),
            onError: vi.fn(),
        })).rejects.toMatchObject({ name: 'AbortError' });
    });
});

describe('isSameOriginAppPath', () => {
    it('rejects protocol-relative, backslash, schemes, and encoded origin confusion', () => {
        expect(isSameOriginAppPath('/jobs/1')).toBe(true);
        expect(isSameOriginAppPath('/jobs/show/1')).toBe(true);
        expect(isSameOriginAppPath('//evil')).toBe(false);
        expect(isSameOriginAppPath('//host')).toBe(false);
        expect(isSameOriginAppPath('\\evil')).toBe(false);
        expect(isSameOriginAppPath('javascript:alert(1)')).toBe(false);
        expect(isSameOriginAppPath('/%2F%2Fhost')).toBe(false);
        expect(isSameOriginAppPath('/javascript%3Aalert(1)')).toBe(false);
    });
});

describe('sendQuestion', () => {
    it('surfaces thread_id from a done frame via onDone', async () => {
        const sse =
            'event: token\ndata: {"d":"Hi"}\n\n' +
            'event: done\ndata: {"model":"m","thread_id":"abc12345678901234567890123456789012"}\n\n';

        const encoder = new TextEncoder();
        const stream = new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode(sse));
                controller.close();
            },
        });

        global.fetch = vi.fn().mockResolvedValue({
            ok: true,
            headers: { get: (k) => (k === 'content-type' ? 'text/event-stream' : null) },
            body: stream,
        });

        let donePayload = null;
        await sendQuestion({
            streamUrl: '/ai-search/stream',
            question: 'hello there',
            threadId: null,
            csrfToken: 'tok',
            onToken: () => {},
            onSources: () => {},
            onDone: (p) => { donePayload = p; },
            onError: () => {},
        });

        expect(donePayload.thread_id).toBe('abc12345678901234567890123456789012');
        expect(global.fetch).toHaveBeenCalledWith(
            '/ai-search/stream',
            expect.objectContaining({
                body: JSON.stringify({ question: 'hello there', operation: 'ask' }),
            })
        );
    });

    it('includes thread_id in the request body when provided', async () => {
        const sse = 'event: done\ndata: {"model":"m"}\n\n';
        const encoder = new TextEncoder();
        const stream = new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode(sse));
                controller.close();
            },
        });

        global.fetch = vi.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'text/event-stream' },
            body: stream,
        });

        await sendQuestion({
            streamUrl: '/ai-search/stream',
            question: 'follow up',
            threadId: 'thread-abc',
            csrfToken: 'tok',
            onToken: () => {},
            onSources: () => {},
            onDone: () => {},
            onError: () => {},
        });

        expect(global.fetch).toHaveBeenCalledWith(
            '/ai-search/stream',
            expect.objectContaining({
                body: JSON.stringify({
                    question: 'follow up',
                    operation: 'ask',
                    thread_id: 'thread-abc',
                }),
            })
        );
    });

    it('includes record_context in the request body when provided, and omits it otherwise', async () => {
        const sse = 'event: done\ndata: {"model":"m"}\n\n';
        const encoder = new TextEncoder();
        const stream = () => new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode(sse));
                controller.close();
            },
        });

        global.fetch = vi.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'text/event-stream' },
            body: stream(),
        });

        const recordContext = {
            version: 1,
            origin: 'explicit_selection',
            query: 'E8520',
            references: [{ resource_type: 'quotation', record_id: '1234' }],
        };

        await sendQuestion({
            streamUrl: '/ai-search/stream',
            question: 'about this quotation',
            recordContext,
            csrfToken: 'tok',
            onToken: () => {},
            onSources: () => {},
            onDone: () => {},
            onError: () => {},
        });

        expect(global.fetch).toHaveBeenCalledWith(
            '/ai-search/stream',
            expect.objectContaining({
                body: JSON.stringify({
                    question: 'about this quotation',
                    operation: 'ask',
                    record_context: recordContext,
                }),
            })
        );

        global.fetch = vi.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'text/event-stream' },
            body: stream(),
        });

        await sendQuestion({
            streamUrl: '/ai-search/stream',
            question: 'no context here',
            csrfToken: 'tok',
            onToken: () => {},
            onSources: () => {},
            onDone: () => {},
            onError: () => {},
        });

        const [, options] = global.fetch.mock.calls[0];
        expect(JSON.parse(options.body)).not.toHaveProperty('record_context');
    });

    it('sends one keepalive cancel when aborted', async () => {
        const controller = new AbortController();
        global.fetch = vi.fn()
            .mockImplementationOnce(() => new Promise((resolve, reject) => {
                controller.signal.addEventListener('abort', () => {
                    reject(new DOMException('Aborted', 'AbortError'));
                });
            }))
            .mockResolvedValueOnce({ ok: true });

        const promise = sendQuestion({
            streamUrl: '/ai-search/stream',
            cancelUrl: '/ai-search/cancel',
            runId: 'a'.repeat(32),
            question: 'stop me',
            signal: controller.signal,
            csrfToken: 'tok',
        });
        controller.abort();
        await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
        expect(global.fetch).toHaveBeenCalledTimes(2);
        expect(global.fetch.mock.calls[1][1]).toEqual(expect.objectContaining({
            keepalive: true,
            body: JSON.stringify({ run_id: 'a'.repeat(32) }),
        }));
    });

    it('never sends a result_page_cursor', async () => {
        const sse = 'event: done\ndata: {"model":"m"}\n\n';
        const encoder = new TextEncoder();
        const stream = new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode(sse));
                controller.close();
            },
        });

        global.fetch = vi.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'text/event-stream' },
            body: stream,
        });

        await sendQuestion({ streamUrl: '/s', question: 'q', threadId: null, pageContext: null, recordContext: null, operation: 'ask', csrfToken: 'x', onDone() {}, onError() {}, onToken() {}, onStatus() {} });
        const body = JSON.parse(global.fetch.mock.calls.at(-1)[1].body);
        expect(body).not.toHaveProperty('result_page_cursor');
        expect(body.question).toBe('q');
    });
});

describe('mapPreStreamError', () => {
    it('maps 401 to session expired', () => {
        expect(mapPreStreamError(401)).toBe('Session expired — refresh to continue');
    });

    it('maps 409 to stale page message', () => {
        expect(mapPreStreamError(409, { message: 'This page changed. Refresh and try again.' }))
            .toBe('This page changed. Refresh and try again.');
        expect(mapPreStreamError(409)).toBe('This page changed. Refresh and try again.');
    });

    it('maps 422 using server message when field errors are absent', () => {
        expect(mapPreStreamError(422, { message: 'Invalid page context.' }))
            .toBe('Invalid page context.');
        expect(mapPreStreamError(422, { errors: { page_context: ['Bad shape'] } }))
            .toBe('Bad shape');
    });
});

describe('sha256Hex', () => {
    it('hashes trim then lowercase input', async () => {
        const hash = await sha256Hex('  Hello  '.trim().toLowerCase());
        expect(hash).toHaveLength(64);
        expect(hash).toMatch(/^[0-9a-f]+$/);
    });
});

describe('scheduleSrBatch', () => {
    it('flushes text to the live region', () => {
        vi.useFakeTimers();
        const el = { textContent: '' };
        const batch = scheduleSrBatch(el, () => 'updated');
        batch.start();
        vi.advanceTimersByTime(2500);
        expect(el.textContent).toBe('updated');
        batch.stop();
        vi.useRealTimers();
    });

    it('announces only newly appended text on subsequent ticks', () => {
        vi.useFakeTimers();
        let text = 'Hello';
        const el = { textContent: '' };
        const batch = scheduleSrBatch(el, () => text);
        batch.start();
        vi.advanceTimersByTime(2500);
        expect(el.textContent).toBe('Hello');
        text = 'Hello world';
        vi.advanceTimersByTime(2500);
        expect(el.textContent).toBe('world');
        batch.stop();
        vi.useRealTimers();
    });
});

describe('consumeSse', () => {
    it('calls onError and stops on error events', async () => {
        const sse = 'event: error\ndata: {"error":"fail"}\n\n';
        const encoder = new TextEncoder();
        let cancelled = false;
        const reader = {
            read: vi.fn()
                .mockResolvedValueOnce({ value: encoder.encode(sse), done: false })
                .mockResolvedValueOnce({ done: true }),
            cancel: vi.fn().mockImplementation(() => { cancelled = true; }),
        };

        const onError = vi.fn();
        await consumeSse(reader, {
            onToken: () => {},
            onSources: () => {},
            onDone: () => {},
            onError,
        });

        expect(onError).toHaveBeenCalledWith({ error: 'fail' });
        expect(cancelled).toBe(true);
    });
});
