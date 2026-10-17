/*
 * MCP Ask AI — stream client. Owns sending one turn to core().sendQuestion()
 * and building the typed handler set that parses the resulting SSE events
 * into assistant-message / thread-state mutations.
 *
 * Pure and dependency-injected: every module-level value the panel needs to
 * read or mutate is passed in explicitly via `deps`, so this file has no
 * hidden coupling to the panel's own closure state.
 */
(function () {
    'use strict';

    /**
     * Build the typed SSE-event handler set for one turn. Each handler
     * receives an already-parsed event payload from core().sendQuestion()
     * and applies it to the assistant message / thread state.
     *
     * @param {object} assistant - the streaming assistant message being built
     * @param {Element|null} srEl - the live-region element for screen readers
     * @param {{errored: boolean, completed: boolean, revealer: object}} state
     * @param {number} turnId - the turn identity this handler set belongs to
     * @param {string|null} sentThreadId - the thread_id sent WITH this request
     * @param {object} deps - injected collaborators, see below
     * @returns {object} handler set consumed by core().sendQuestion()
     */
    function buildStreamHandlers(assistant, srEl, state, turnId, sentThreadId, deps) {
        const {
            PHASE_GROUPS,
            Persist,
            StreamUi,
            clearNeutralStatusTimer,
            updateStreamingStatusUI,
            isActiveTurn,
            setCurrentThreadId,
            setThreadState,
            updateModeLabel,
            normalizeFollowupSuggestions,
            renderThread,
            persistThread,
        } = deps;

        return {
            onStatus(payload) {
                const phase = payload?.phase || '';
                if (phase && phase !== 'understanding') clearNeutralStatusTimer(assistant);
                if (phase === 'searching_database' || phase === 'searching_both') {
                    assistant.queryType = 'structured';
                }
                const label = payload?.label || PHASE_GROUPS[phase];
                if (phase && label) {
                    assistant.phases = assistant.phases || [];
                    if (assistant.phases.at(-1)?.phase !== phase) {
                        assistant.phases.push({ label, phase });
                    }
                    if (!String(assistant.content || '').trim()) updateStreamingStatusUI(assistant);
                    if (srEl) srEl.textContent = label;
                }
            },
            onToken(chunk) {
                clearNeutralStatusTimer(assistant);
                state.revealer.push(chunk);
            },
            onSources(payload) {
                assistant.sources = payload;
            },
            onSqlProvenance(payload) {
                const links = Array.isArray(payload?.record_links) ? payload.record_links : [];
                assistant.recordLinks = links.filter((l) => l && typeof l.url === 'string');
                assistant.sqlQueries = Array.isArray(payload?.queries) ? payload.queries : [];
                assistant.sqlExplanation = payload?.explanation || null;
                assistant.databaseEvidence = assistant.recordLinks.length > 0
                    || assistant.sqlQueries.length > 0
                    || !!assistant.sqlExplanation;
            },
            onUiLinks(payload) {
                const links = Array.isArray(payload?.ui_links) ? payload.ui_links : [];
                assistant.uiLinks = links.filter((l) => l && typeof l.url === 'string');
            },
            onCitations(payload) {
                assistant.citations = Persist.normalizeCitations(payload, assistant.sources);
            },
            onDisambiguation(payload) {
                assistant.disambiguation = payload;
            },
            onClientAction(payload) {
                if (!payload || typeof payload !== 'object') return;

                try {
                    window.dispatchEvent(new CustomEvent('mcp:action', { detail: payload }));
                } catch (_) {}

                // Tab Switcher
                if (typeof payload.switch_tab === 'string') {
                    const tabSelector = payload.switch_tab;
                    if (/^#[a-zA-Z0-9_-]{1,64}$/.test(tabSelector)) {
                        try {
                            const btn = document.querySelector(
                                `[data-bs-target="${tabSelector}"], [data-target="${tabSelector}"], a[href="${tabSelector}"]`
                            );
                            btn?.click?.();
                        } catch (_) {}
                    }
                }

                // Element Pulse Highlighter
                if (payload.type === 'highlight_element' && typeof payload.selector === 'string') {
                    const selector = payload.selector;
                    if (/^#[a-zA-Z0-9_-]{1,64}$/.test(selector)) {
                        try {
                            const el = document.querySelector(selector);
                            if (el) {
                                el.classList.remove('mcp-highlight-pulse');
                                void el.offsetWidth;
                                el.classList.add('mcp-highlight-pulse');
                                try {
                                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                } catch (_) {}
                                setTimeout(() => {
                                    el.classList.remove('mcp-highlight-pulse');
                                }, 2500);
                            }
                        } catch (_) {}
                    }
                }
            },
            // The stream's identity frame: pin the attempt reference on the
            // assistant shell before any terminal, and adopt the thread the
            // stream says this attempt belongs to when the panel had none.
            onAccepted(payload) {
                if (!payload || typeof payload !== 'object') return;
                assistant.restore_ref = payload.restore_ref || null;
                if (!sentThreadId && typeof payload.thread_id === 'string' && payload.thread_id) {
                    setCurrentThreadId(payload.thread_id);
                }
                persistThread();
            },
            onDone(payload) {
                clearNeutralStatusTimer(assistant);
                state.completed = true;
                assistant.businessQuery = payload?.business_query || payload?.businessQuery || null;
                assistant.outcome = typeof payload?.outcome === 'string' ? payload.outcome : null;
                const envelope = assistant.businessQuery?.envelope;
                const envelopes = assistant.businessQuery?.envelopes;
                if (assistant.businessQuery?.outcome === 'answered') {
                    if (Array.isArray(envelopes) && envelopes.length) {
                        assistant.databaseEvidence = envelopes.every((item) => item?.evidence_sealed === true);
                    } else if (envelope?.evidence_sealed === true) {
                        assistant.databaseEvidence = true;
                    }
                }
                // The explanation rides on done and feeds the trust drawer. It
                // says what was read, not whether it was sealed, so it never
                // decides databaseEvidence.
                if (payload?.sql_provenance?.explanation) {
                    assistant.sqlExplanation = payload.sql_provenance.explanation;
                }
                if (payload.disambiguation) {
                    assistant.disambiguation = payload.disambiguation;
                }
                if (!isActiveTurn(turnId)) return;
                const returnedThreadId = typeof payload.thread_id === 'string' && payload.thread_id.trim()
                    ? payload.thread_id
                    : null;
                if (sentThreadId && returnedThreadId && returnedThreadId !== sentThreadId) {
                    assistant.threadExpiredNotice = 'Your previous conversation expired — starting a new one.';
                }
                setCurrentThreadId(returnedThreadId);
                setThreadState(returnedThreadId ? 'ready' : 'independent');
                updateModeLabel();
                // A terminal without a reference (a clarification carries none)
                // never erases the one the stream pinned on turn_accepted.
                assistant.restore_ref = payload.restore_ref || payload.restoreRef || assistant.restore_ref || null;
                assistant.exchangeId = payload.exchange_id || null;
                assistant.traceId = payload.trace_id || null;
                assistant.feedbackToken = payload.feedback_token || null;
                assistant.followupSuggestions = normalizeFollowupSuggestions(
                    payload.follow_up_suggestions
                );
                assistant.contextMode = payload.context_mode || assistant.retryDescriptor?.context_mode || 'none';
            },
            onError(payload) {
                clearNeutralStatusTimer(assistant);
                state.errored = true;
                state.completed = false;
                state.revealer.flush();
                assistant.streaming = false;
                const code = payload?.error || 'unknown';
                assistant.errorCode = code;
                assistant.error = StreamUi.mapStreamError(payload);
                if (code === 'capability_unavailable') {
                    assistant.retryDescriptor = null;
                }
                if (window.Sentry) {
                    window.Sentry.captureMessage('AI search: ' + code, {
                        level: 'warning',
                        tags: { ai_error_code: code },
                        extra: { detail: payload?.detail || null },
                    });
                }
                renderThread();
            },
        };
    }

    /**
     * Send one turn to core().sendQuestion() and wire its callbacks to the
     * typed handler set above. Returns whatever core().sendQuestion() itself
     * resolves to (the panel decides how to react to the result).
     *
     * @param {object} descriptor - the retry descriptor for this turn
     * @param {object} assistant
     * @param {Element|null} srEl
     * @param {object} state
     * @param {object|null} recordContext
     * @param {number} turnId
     * @param {object} uiFirstTextRef
     * @param {object} deps - injected collaborators (core, urls, csrf, clock,
     *   run-id source, thread-state accessors, and everything buildStreamHandlers needs)
     */
    async function sendQuestionAndParse(descriptor, assistant, srEl, state, recordContext, turnId, uiFirstTextRef, deps) {
        const {
            core, streamUrl, cancelUrl, timingUrl,
            getCurrentThreadId, streamAbortSignal, csrfToken,
            runId, waitForLoaderPaint,
            onBodyBuilt = null,
        } = deps;

        const sentThreadId = getCurrentThreadId();
        const handlers = buildStreamHandlers(assistant, srEl, state, turnId, sentThreadId, deps);
        // The active protocol adapter, when a stream speaks its own event
        // vocabulary. Null means the wire is already canonical.
        const protocol = window.McpAskAiProtocols?.active() || null;

        return core.sendQuestion({
            protocol,
            streamUrl,
            cancelUrl,
            timingUrl,
            question: descriptor.question,
            threadId: sentThreadId,
            pageContext: descriptor.page_context,
            recordContext,
            operation: descriptor.operation,
            targetExchangeId: descriptor.target_exchange_id,
            continuationRef: descriptor.continuation_ref || null,
            clarificationChoiceId: descriptor.clarification_choice_id || null,
            clarificationFreeText: descriptor.clarification_free_text || null,
            runId,
            signal: streamAbortSignal,
            csrfToken,
            uiFirstTextRef,
            onBodyBuilt,
            beforeEmitTimings: async () => {
                state.revealer?.flush();
                await waitForLoaderPaint();
            },
            shouldContinue: () => deps.isActiveTurn(turnId),
            ...handlers,
        });
    }

    window.McpAskStreamClient = {
        buildStreamHandlers,
        sendQuestionAndParse,
    };
})();
