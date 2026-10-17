/*
 * MCP AI Core — shared transport + safe markdown for Ask AI panel and Ctrl+K seed.
 * ONE send site (sendQuestion) and ONE done handler contract (onDone payload.thread_id).
 */
(function () {
    'use strict';

    const SR_BATCH_MS = 2500;
    const MAX_SSE_LINE_BYTES = 64 * 1024;
    const MAX_SSE_EVENT_BYTES = 256 * 1024;

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /**
     * Same-origin app path guard for server-minted record links.
     * Accepts a non-empty string with exactly one leading `/`.
     * Rejects `//`, `\`, controls, encoded scheme/origin confusion, non-strings.
     */
    function isSameOriginAppPath(url) {
        if (typeof url !== 'string' || url === '') return false;
        if (url.charCodeAt(0) !== 47 /* / */) return false;
        if (url.length > 1 && url.charCodeAt(1) === 47) return false;
        if (url.includes('\\')) return false;

        for (let i = 0; i < url.length; i++) {
            const c = url.charCodeAt(i);
            if (c <= 0x1f || c === 0x7f) return false;
        }

        let decoded;
        try {
            decoded = decodeURIComponent(url);
        } catch (_) {
            return false;
        }

        if (decoded === '' || decoded.charCodeAt(0) !== 47) return false;
        if (decoded.length > 1 && decoded.charCodeAt(1) === 47) return false;
        if (decoded.includes('\\')) return false;

        for (let i = 0; i < decoded.length; i++) {
            const c = decoded.charCodeAt(i);
            if (c <= 0x1f || c === 0x7f) return false;
        }

        // Encoded scheme confusion after the leading slash (e.g. /javascript%3A…).
        if (/^\/[a-zA-Z][a-zA-Z0-9+.-]*:/i.test(decoded)) return false;

        return true;
    }

    function parseSseEvents(buffer, options = {}) {
        const events = [];
        const eof = options.eof === true;
        const maxLineBytes = options.maxLineBytes || MAX_SSE_LINE_BYTES;
        const maxEventBytes = options.maxEventBytes || MAX_SSE_EVENT_BYTES;
        let rest = String(buffer || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        let idx;

        while ((idx = rest.indexOf('\n\n')) !== -1 || (eof && rest.length > 0)) {
            if (idx === -1) idx = rest.length;
            const block = rest.slice(0, idx);
            rest = idx === rest.length ? '' : rest.slice(idx + 2);
            if (block.length > maxEventBytes) {
                throw new Error('sse_event_too_large');
            }
            if (!block) continue;

            let event = 'message';
            const dataLines = [];
            let hasData = false;
            for (const line of block.split('\n')) {
                if (line.length > maxLineBytes) {
                    throw new Error('sse_line_too_large');
                }
                if (line.startsWith(':')) continue;
                const colon = line.indexOf(':');
                const field = colon === -1 ? line : line.slice(0, colon);
                let value = colon === -1 ? '' : line.slice(colon + 1);
                if (value.startsWith(' ')) value = value.slice(1);
                if (field === 'event') event = value;
                if (field === 'data') {
                    hasData = true;
                    dataLines.push(value);
                }
            }

            if (hasData) {
                events.push({ event, data: dataLines.join('\n') });
            }
        }

        return { events, rest };
    }

    function waitForNextPaint() {
        if (typeof requestAnimationFrame !== 'function') {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            requestAnimationFrame(() => requestAnimationFrame(resolve));
        });
    }

    async function consumeSse(reader, {
        onToken = () => {},
        onSources = () => {},
        onSqlProvenance = () => {},
        onUiLinks = () => {},
        onCitations = () => {},
        onDone = () => {},
        onError = () => {},
        onStatus = () => {},
        onAccepted = () => {},
        onTimings = () => {},
        beforeEmitTimings = null,
        shouldContinue = null,
        signal = null,
        uiFirstTextRef = null,
        // A protocol adapter renames a stream's own event vocabulary to the
        // canonical one handled below, so the panel renders every protocol
        // through one path. Without one the wire names are already canonical.
        protocol = null,
    }) {
        const decoder = new TextDecoder('utf-8', { fatal: true });
        let sseBuffer = '';
        let terminal = null;
        let pendingCr = false;
        const streamStart = performance.now();
        if (uiFirstTextRef) {
            uiFirstTextRef.stamp = () => {
                if (uiFirstTextRef.ms === null) {
                    uiFirstTextRef.ms = Math.round(performance.now() - streamStart);
                }
            };
        }

        function emitTimings() {
            const completionMs = Math.round(performance.now() - streamStart);
            const uiFirst = uiFirstTextRef?.ms ?? completionMs;
            onTimings({
                ui_first_text_ms: uiFirst,
                completion_latency_ms: completionMs,
            });
        }

        function normalizeChunk(text, flush = false) {
            let value = text;
            if (pendingCr) {
                value = '\r' + value;
                pendingCr = false;
            }
            if (!flush && value.endsWith('\r')) {
                pendingCr = true;
                value = value.slice(0, -1);
            }
            return value.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        }

        async function protocolError(code, detail) {
            terminal = 'error';
            onError({ error: code, detail });
            try {
                await reader.cancel();
            } catch (_) { /* already closed */ }
            return terminal;
        }

        async function dispatch(events) {
            for (const { event, data } of events) {
                let payload;
                try {
                    payload = JSON.parse(data);
                } catch (_) {
                    return protocolError('malformed_sse_event', 'The answer stream was malformed.');
                }

                if (terminal !== null) {
                    return protocolError('duplicate_terminal_event', 'The answer stream ended more than once.');
                }

                const canonical = protocol
                    ? protocol.normalizeEvent(event, payload)
                    : [{ event, payload }];
                for (const step of canonical) {
                    await handleCanonical(step.event, step.payload);
                    if (terminal === 'error') {
                        return terminal;
                    }
                }
            }
            return terminal;
        }

        async function handleCanonical(event, payload) {
            if (event === 'token' && typeof payload?.d === 'string') {
                onToken(payload.d);
            }
            if (event === 'status' && payload) {
                onStatus(payload);
            }
            if (event === 'sources' && Array.isArray(payload)) {
                onSources(payload);
            }
            if (event === 'sql_provenance' && payload) {
                onSqlProvenance(payload);
            }
            if (event === 'ui_links' && payload) {
                onUiLinks(payload);
            }
            if (event === 'citations' && payload) {
                onCitations(payload);
            }
            if (event === 'accepted' && payload) {
                onAccepted(payload);
            }
            if (event === 'done' && payload) {
                terminal = 'done';
                onDone(payload);
                if (beforeEmitTimings) {
                    await beforeEmitTimings();
                }
                if (signal?.aborted || (typeof shouldContinue === 'function' && !shouldContinue())) {
                    throw new DOMException('Aborted', 'AbortError');
                }
                emitTimings();
            }
            if (event === 'error') {
                terminal = 'error';
                onError(payload);
                await reader.cancel().catch(() => {});
            }
        }

        try {
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                sseBuffer += normalizeChunk(decoder.decode(value, { stream: true }));
                let parsed;
                try {
                    parsed = parseSseEvents(sseBuffer);
                } catch (error) {
                    return protocolError(error.message, 'The answer stream exceeded its safety limit.');
                }
                sseBuffer = parsed.rest;
                const result = await dispatch(parsed.events);
                if (result !== null) {
                    // Read no bytes after the one authoritative terminal event.
                    await reader.cancel().catch(() => {});
                    return result;
                }
            }

            sseBuffer += normalizeChunk(decoder.decode(), true);
            if (pendingCr) {
                sseBuffer += '\n';
                pendingCr = false;
            }
            let parsed;
            try {
                parsed = parseSseEvents(sseBuffer, { eof: true });
            } catch (error) {
                return protocolError(error.message, 'The answer stream exceeded its safety limit.');
            }
            const result = await dispatch(parsed.events);
            if (result !== null) return result;
        } catch (error) {
            if (error?.name === 'AbortError') throw error;
            return protocolError('invalid_utf8_stream', 'The answer stream contained invalid text.');
        }

        onError({
            error: 'incomplete_stream',
            detail: 'The answer did not finish. Try again.',
        });
        return 'incomplete';
    }

    function mapPreStreamError(status, body) {
        if (status === 419 || status === 401) {
            return 'Session expired — refresh to continue';
        }
        if (status === 422) {
            const errors = body?.errors;
            if (errors?.question?.[0]) return errors.question[0];
            if (errors?.thread_id?.[0]) return errors.thread_id[0];
            if (errors?.page_context?.[0]) return errors.page_context[0];
            if (typeof body?.message === 'string' && body.message.trim()) return body.message;
            return 'Please check your question and try again.';
        }
        if (status === 409) {
            return body?.message || 'This page changed. Refresh and try again.';
        }
        if (status === 429) {
            return "You're asking quickly — try again in a moment";
        }
        if (status === 402) {
            const b = body || {};
            return 'Monthly AI budget reached (' + (b.used ?? '?') + ' / ' + (b.cap ?? '?') +
                '). Resets ' + (b.reset_date ?? 'next month') + '.';
        }
        if (status === 503) {
            return 'AI search temporarily unavailable';
        }
        return 'Something went wrong. Please try again.';
    }

    async function sha256Hex(text) {
        if (!window.crypto?.subtle) return '';
        const data = new TextEncoder().encode(text);
        const digest = await window.crypto.subtle.digest('SHA-256', data);
        return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
    }

    function scheduleSrBatch(srEl, getText, intervalMs) {
        const ms = intervalMs || SR_BATCH_MS;
        let timer = null;
        let lastAnnounced = '';

        function announce(force) {
            if (!srEl) return;
            const text = getText() || '';
            if (!force && text === lastAnnounced) return;
            // Announce only the newly appended slice when the answer is growing.
            if (!force && lastAnnounced && text.startsWith(lastAnnounced)) {
                const delta = text.slice(lastAnnounced.length).trim();
                if (!delta) return;
                srEl.textContent = delta;
            } else {
                srEl.textContent = text;
            }
            lastAnnounced = text;
        }

        return {
            start() {
                if (timer || !srEl) return;
                lastAnnounced = '';
                timer = setInterval(() => announce(false), ms);
            },
            flush() {
                announce(true);
            },
            stop() {
                if (timer) {
                    clearInterval(timer);
                    timer = null;
                }
            },
        };
    }

    // Allowlist of inert elements with no attributes. Links, forms and images are
    // never model-written: the panel attaches them from trusted provenance, so
    // any anchor the model emits degrades to its text.
    const MARKDOWN_ALLOWED_TAGS = [
        'p', 'br', 'strong', 'b', 'em', 'i', 'code', 'pre',
        'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'del', 's', 'sup', 'sub', 'span',
    ];

    function renderMarkdown(text) {
        const input = String(text || '');
        if (!window.marked || !window.DOMPurify) {
            return escapeHtml(input);
        }

        // A reference definition would turn a citation marker into a hidden
        // link target; drop those lines before parsing.
        const withoutRefs = input.replace(/^\[\d+\]:\s.*$/gm, '');
        const raw = window.marked.parse(withoutRefs, { breaks: true });
        return window.DOMPurify.sanitize(raw, {
            ALLOWED_TAGS: MARKDOWN_ALLOWED_TAGS,
            ALLOWED_ATTR: [],
        });
    }

    class PreStreamError extends Error {
        constructor(status, body) {
            super(mapPreStreamError(status, body));
            this.name = 'PreStreamError';
            this.status = status;
            this.body = body;
        }
    }

    async function sendQuestion({
        streamUrl,
        cancelUrl,
        timingUrl,
        question,
        continuationRef = null,
        clarificationChoiceId = null,
        clarificationFreeText = null,
        idempotencyKey = null,
        threadId,
        pageContext,
        recordContext,
        operation,
        targetExchangeId,
        runId,
        signal,
        csrfToken,
        onToken,
        onSources,
        onSqlProvenance,
        onUiLinks,
        onCitations,
        onDone,
        onError,
        onStatus,
        onAccepted,
        onBodyBuilt = null,
        beforeEmitTimings = null,
        shouldContinue = null,
        uiFirstTextRef = null,
        protocol = null,
    }) {
        const v1Body = {
            ...(question !== undefined && question !== null ? { question } : {}),
            operation: operation || 'ask',
            ...(continuationRef ? { continuation_ref: continuationRef } : {}),
            ...(clarificationChoiceId ? { clarification_choice_id: clarificationChoiceId } : {}),
            ...(clarificationFreeText ? { clarification_free_text: clarificationFreeText } : {}),
            ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
            ...(threadId ? { thread_id: threadId } : {}),
            ...(targetExchangeId ? { target_exchange_id: targetExchangeId } : {}),
            ...(runId ? { run_id: runId } : {}),
            ...(pageContext ? { page_context: pageContext } : {}),
            // Task B4 / R3: identifiers-only, untrusted — Billing re-resolves and
            // re-authorizes every reference server-side before anything reaches
            // RAG. One-shot: attached only to the turn that triggered it, never
            // persisted/re-sent on retry/regenerate (see mcp-ask-ai.js sendTurn()).
            ...(recordContext ? { record_context: recordContext } : {}),
        };
        // A protocol with its own request contract rewrites the body here, so
        // the panel keeps one send site whichever stream answers it.
        const body = protocol ? protocol.buildBody(v1Body) : v1Body;
        // The body is the one place the wire's own identities converge; whoever
        // owns the turn needs them the instant they are pinned, not on done.
        onBodyBuilt?.({ runId: body.run_id, threadId: body.thread_id });

        let cancelSent = false;
        const sendCancel = () => {
            if (cancelSent || !cancelUrl || !runId) return;
            cancelSent = true;
            fetch(cancelUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: JSON.stringify({ run_id: runId }),
                credentials: 'same-origin',
                keepalive: true,
            }).catch(() => {});
        };
        if (signal) {
            signal.addEventListener('abort', sendCancel, { once: true });
        }

        try {
            const response = await fetch(streamUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'text/event-stream',
                    'X-CSRF-TOKEN': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: JSON.stringify(body),
                signal,
                credentials: 'same-origin',
            });

            const contentType = response.headers.get('content-type') || '';

            if (!response.ok || !contentType.includes('text/event-stream')) {
                const bodyJson = (response.status === 402 || response.status === 422 || response.status === 409)
                    ? await response.json().catch(() => ({}))
                    : null;
                throw new PreStreamError(response.status, bodyJson);
            }

            let feedbackToken = null;
            const terminal = await consumeSse(response.body.getReader(), {
                protocol,
                signal,
                shouldContinue,
                onToken,
                onSources,
                onSqlProvenance,
                onUiLinks,
                onCitations,
                onDone(payload) {
                    feedbackToken = payload?.feedback_token || null;
                    onDone(payload);
                },
                onError,
                onStatus,
                onAccepted,
                beforeEmitTimings,
                uiFirstTextRef,
                onTimings(timings) {
                    const traceId = runId;
                    if (!timingUrl || !traceId || !feedbackToken) return;
                    fetch(timingUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRF-TOKEN': csrfToken,
                            'X-Requested-With': 'XMLHttpRequest',
                        },
                        body: JSON.stringify({
                            trace_id: traceId,
                            feedback_token: feedbackToken,
                            ui_first_text_ms: timings.ui_first_text_ms,
                            completion_latency_ms: timings.completion_latency_ms,
                        }),
                        credentials: 'same-origin',
                        keepalive: true,
                    }).catch(() => {});
                },
            });

            return { terminal };
        } finally {
            signal?.removeEventListener('abort', sendCancel);
        }
    }

    const api = {
        parseSseEvents,
        consumeSse,
        sendQuestion,
        renderMarkdown,
        escapeHtml,
        isSameOriginAppPath,
        mapPreStreamError,
        sha256Hex,
        scheduleSrBatch,
        waitForNextPaint,
        PreStreamError,
        SR_BATCH_MS,
        MAX_SSE_LINE_BYTES,
        MAX_SSE_EVENT_BYTES,
    };

    window.McpAiCore = api;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})();
