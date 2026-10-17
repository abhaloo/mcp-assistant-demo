/*
 * MCP Ask AI — sessionStorage thread persistence.
 */
(function () {
    'use strict';

    const PERSIST_MAX_MESSAGES = 40;
    const STORAGE_THREAD_PREFIX = 'mcp.askAi.thread.v1';
    const FOLLOWUP_SUGGESTION_LIMIT = 3;
    const CITATION_LIMIT = 20;
    // The panel shows a thread for one day; the server keeps its records.
    const PANEL_WINDOW_MS = 24 * 60 * 60 * 1000;
    const RESTORE_NOTICE = 'This answer is no longer available. Ask again for a fresh answer.';
    const THREAD_CLEARED_NOTICE = 'Your last conversation was more than a day old, so a new one has started.';
    // A reload cannot show a clarification's choices; a turn with no text says so.
    const CLARIFICATION_NOT_SHOWN = 'I need one more detail to answer, but the choices could not be shown. Please ask again.';

    function normalizeCitations(raw, sources) {
        if (raw?.parsed !== true || !Array.isArray(raw.cited)) {
            return { parsed: false, cited: [] };
        }

        const sourceIds = new Set(
            Array.isArray(sources) ? sources.map((source) => String(source?.id || '')) : []
        );
        const cited = [];
        const seenMarkers = new Set();
        for (const citation of raw.cited) {
            const marker = String(citation?.marker || '').trim();
            const id = String(citation?.id || '').trim();
            if (!/^\d+$/.test(marker) || Number(marker) < 1 || !sourceIds.has(id) || seenMarkers.has(marker)) {
                continue;
            }
            seenMarkers.add(marker);
            cited.push({ marker, id });
            if (cited.length === CITATION_LIMIT) break;
        }

        return cited.length ? { parsed: true, cited } : { parsed: false, cited: [] };
    }

    function storageThreadKey() {
        const uid = document.querySelector('meta[name="user-id"]')?.getAttribute('content');
        return uid ? STORAGE_THREAD_PREFIX + '.' + uid : STORAGE_THREAD_PREFIX;
    }

    function clearPersistedThread(key) {
        try {
            const threadKey = typeof key === 'string' && key ? key : storageThreadKey();
            sessionStorage.removeItem(threadKey);
            sessionStorage.removeItem(threadKey.replace('mcp.askAi.thread.v1', 'mcp.askAi.v2TurnStore'));
            sessionStorage.removeItem(threadKey.replace('mcp.askAi.thread.v1', 'mcp.askAi.v2Thoughts'));
        } catch (_) { /* storage unavailable */ }
    }


    function normalizeFollowupSuggestions(raw) {
        if (!Array.isArray(raw)) return [];
        const suggestions = [];
        const seen = new Set();
        for (const value of raw) {
            if (typeof value !== 'string') continue;
            const suggestion = value.replace(/\s+/g, ' ').trim().slice(0, 160).trim();
            const key = suggestion.toLocaleLowerCase();
            if (suggestion.length < 2 || seen.has(key)) continue;
            seen.add(key);
            suggestions.push(suggestion);
            if (suggestions.length === FOLLOWUP_SUGGESTION_LIMIT) break;
        }
        return suggestions;
    }

    function serializeMessage(m) {
        return {
            id: m.id,
            role: m.role,
            content: m.content,
            question: m.question || null,
            sources: m.sources || [],
            citations: normalizeCitations(m.citations, m.sources),
            recordLinks: m.recordLinks || [],
            sqlQueries: m.sqlQueries || [],
            databaseEvidence: m.databaseEvidence === true,
            feedback: m.feedback || null,
            feedbackReason: m.feedbackReason || null,
            traceId: m.traceId || null,
            feedbackToken: m.feedbackToken || null,
            exchangeId: m.exchangeId || null,
            restore_ref: m.restore_ref || null,
            followupSuggestions: normalizeFollowupSuggestions(m.followupSuggestions),
            retryDescriptor: m.retryDescriptor || null,
            contextLabel: m.contextLabel || null,
            phases: m.phases || [],
            queryType: m.queryType || null,
            outcome: m.outcome || null,
            error: m.error || null,
            stopped: !!m.stopped,
        };
    }

    // The window's clock starts when a thread is first saved and stays with it;
    // a new thread id starts a new clock.
    function threadStartedAt(threadId, now) {
        try {
            const prior = JSON.parse(sessionStorage.getItem(storageThreadKey()) || 'null');
            const sameThread = prior && (prior.threadId === threadId || prior.threadId == null);
            return sameThread && Number.isFinite(prior.startedAt) ? prior.startedAt : now;
        } catch (_) {
            return now;
        }
    }

    function persistThread({ threadId, threadState, messages }) {
        try {
            sessionStorage.setItem(storageThreadKey(), JSON.stringify({
                threadId,
                threadState,
                startedAt: threadStartedAt(threadId, Date.now()),
                messages: messages
                    // An answer still streaming is kept once the server named it, so a
                    // reload mid-answer can still ask the server for that turn.
                    .filter((m) => !m.streaming || m.restore_ref)
                    .slice(-PERSIST_MAX_MESSAGES)
                    .map(serializeMessage),
            }));
        } catch (_) { /* storage full or unavailable */ }
    }

    function loadPersistedThread(deepCloneFreeze) {
        try {
            const raw = sessionStorage.getItem(storageThreadKey());
            if (!raw) return null;
            const saved = JSON.parse(raw);
            const messages = (saved.messages || []).map((m) => ({
                ...m,
                citations: normalizeCitations(m.citations, m.sources),
                streaming: false,
                retryDescriptor: m.retryDescriptor ? deepCloneFreeze(m.retryDescriptor) : null,
            }));
            const ids = messages.map((m) => parseInt(String(m.id).slice(4), 10)).filter(Number.isFinite);
            const msgId = ids.length ? Math.max(...ids) : 0;
            return {
                threadId: saved.threadId ?? null,
                threadState: saved.threadState ?? null,
                startedAt: Number.isFinite(saved.startedAt) ? saved.startedAt : null,
                messages,
                msgId,
            };
        } catch (_) {
            clearPersistedThread();
            return null;
        }
    }

    // A saved thread at least one window old opens as a new, empty thread. A
    // thread saved before the clock existed has no start and starts its clock
    // at its next save.
    function threadExpired(saved, now) {
        return Number.isFinite(saved?.startedAt) && now - saved.startedAt >= PANEL_WINDOW_MS;
    }

    function quarantineAssistant(m) {
        if (m.role !== 'assistant') return m;
        return Object.assign({}, m, {
            content: '', sources: [], citations: { parsed: false, cited: [] },
            sqlQueries: [], recordLinks: [], quarantined: true,
        });
    }

    // A turn the panel cannot show again keeps its place with a fixed notice.
    // The cached body never paints.
    function noticeAssistant(m) {
        return Object.assign(quarantineAssistant(m), {
            quarantined: false, error: RESTORE_NOTICE, stopped: false, followupSuggestions: [],
        });
    }

    // Shells still waiting on a restore become notices when it is cut short;
    // live answers from this session stay.
    function settleQuarantined(list) {
        return list.map((m) => (m.quarantined ? noticeAssistant(m) : m));
    }

    // Cached assistant bodies never paint. A turn the server can vouch for
    // (it carries a restore_ref) stays a blank shell until the restore answers.
    // Any other assistant turn, and a question left with no answer, shows the
    // notice, so a reload never leaves a blank turn.
    function quarantineThread(list) {
        const out = [];
        list.forEach((m, i) => {
            if (m.role === 'assistant') {
                out.push(m.restore_ref ? quarantineAssistant(m) : noticeAssistant(m));
                return;
            }
            out.push(m);
            if (m.role === 'user' && list[i + 1]?.role !== 'assistant') {
                out.push(noticeAssistant({ id: m.id + '-answer', role: 'assistant', content: '' }));
            }
        });
        return out;
    }

    function applyAuthorizedMessages(savedMessages, data) {
        const byRef = {};
        (data.results || []).forEach((r) => {
            if (r.status === 'authorized' && r.payload) byRef[r.restore_ref] = r.payload;
        });
        return quarantineThread(savedMessages).map((m) => {
            if (!m.quarantined) return m;
            const payload = byRef[m.restore_ref];
            // Denied, unavailable and expired turns keep their place with the
            // notice, and no next steps.
            if (!payload) return noticeAssistant(m);
            // Server content lands on the blank shell, never on the raw cache. The
            // next steps the payload offers paint from the turn store
            // (resources/js/ask_ai_v2/modules/interaction_card.js).
            const blankClarification = payload.run_status === 'clarification' && !String(payload.answer_text || '').trim();
            return Object.assign({}, m, {
                content: blankClarification ? CLARIFICATION_NOT_SHOWN : (payload.answer_text || ''),
                sources: payload.sources || [],
                citations: payload.citations || { parsed: false, cited: [] },
                recordLinks: Array.isArray(payload.record_links) ? payload.record_links : [],
                stopped: payload.run_status === 'stopped',
                quarantined: false,
            });
        });
    }

    function restoreIdentity(threadId) {
        return {
            userId: document.querySelector('meta[name="user-id"]')?.getAttribute('content') || '',
            entityId: document.querySelector('meta[name="entity-id"]')?.getAttribute('content') || '',
            threadId: threadId || '',
        };
    }

    window.McpAskAiPersist = {
        STORAGE_THREAD_PREFIX,
        PERSIST_MAX_MESSAGES,
        PANEL_WINDOW_MS,
        FOLLOWUP_SUGGESTION_LIMIT,
        RESTORE_NOTICE,
        THREAD_CLEARED_NOTICE,
        storageThreadKey,
        clearPersistedThread,
        normalizeFollowupSuggestions,
        normalizeCitations,
        persistThread,
        loadPersistedThread,
        threadExpired,
        quarantineThread,
        settleQuarantined,
        applyAuthorizedMessages,
        restoreIdentity,
    };
})();
