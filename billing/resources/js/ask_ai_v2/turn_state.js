/**
 * Per-turn view state for the Ask AI v2 modules.
 *
 * One entry per assistant message, bound to the server run id by the first
 * frame that names it. A module keeps its state on its own slice of the turn
 * and paints from that slice, so a later turn can never repaint an earlier
 * one and a thread rebuild puts every turn back where it was.
 */

const turns = new Map();
const messageByRun = new Map();
const removeListeners = new Set();
const TERMINAL = new Set(['stopped', 'failed', 'done', 'clarification', 'declined']);
const terminalListeners = new Set();
const STORAGE_PREFIX = 'mcp.askAi.v2TurnStore';
// Finished without an answer by the server's own choice, which it explains: a
// refusal. A stop, a timeout or an error ends as failed, never as declined.
const DECLINED_OUTCOMES = new Set(['unsupported', 'denied', 'shadowed']);

/** The run status a turn_outcome's outcome type ends the run in. */
export function terminalStatus(outcomeType) {
    if (outcomeType === 'answered') return 'done';
    if (outcomeType === 'clarification_required') return 'clarification';
    return DECLINED_OUTCOMES.has(outcomeType) ? 'declined' : 'failed';
}

// The server's restore word for a refusal is failed; the outcome type tells a refusal apart.
function restoredStatus(payload) {
    if (payload.run_status === 'failed' && terminalStatus(payload.turn_result?.outcome_type) === 'declined') return 'declined';
    return payload.run_status;
}

function getUserId() {
    if (typeof document === 'undefined') return null;
    return document.querySelector('meta[name="user-id"]')?.getAttribute('content') || null;
}

function storageKey() {
    const uid = getUserId();
    if (uid) return `${STORAGE_PREFIX}.${uid}`;
    return STORAGE_PREFIX;
}

function serializeTurn(turn) {
    if (!turn) return null;
    const item = {
        msgId: turn.msgId,
        runId: turn.runId,
        restore_ref: turn.restore_ref || null,
    };
    if (turn.result instanceof Map && turn.result.size > 0) {
        item.result = {};
        for (const [tableId, r] of turn.result.entries()) {
            if (r.streaming) continue;
            item.result[tableId] = {
                columns: r.columns || [],
                presentation: r.presentation || null,
                rows: r.rows || [],
                returned: r.returned,
                total: r.total,
                aggregate: r.aggregate || false,
                streaming: false,
                enterFrom: 0,
            };
        }
    }
    if (turn.timeline && turn.timeline.steps && turn.timeline.steps.length > 0) {
        if (turn.timeline.state !== 'running' || turn.timeline.closed) {
            item.timeline = {
                steps: turn.timeline.steps.map((s) => ({
                    kind: s.kind,
                    state: s.state,
                    label: s.label,
                    elapsedMs: s.elapsedMs,
                })),
                state: turn.timeline.state,
                phaseLabel: turn.timeline.phaseLabel,
                durationMs: turn.timeline.durationMs,
                closed: true,
            };
        }
    }
    return (item.result || item.timeline || item.restore_ref || item.runId) ? item : null;
}

function blank(msgId) {
    return {
        msgId,
        runId: null,
        startedAt: performance.now(),
        timeline: null,
        result: new Map(),
        interaction: null,
    };
}

const restoreSlices = new Map();

export function projectRestoredSources(sources) {
    if (!Array.isArray(sources)) return undefined;
    return sources.map((source) => {
        if (!source || typeof source !== 'object') return null;
        const projected = {};
        if (source.id != null) projected.id = source.id;
        if (typeof source.type === 'string') projected.type = source.type;
        if (typeof source.title === 'string') projected.title = source.title;
        if (typeof source.url === 'string') projected.url = source.url;
        if (typeof source.snippet === 'string') projected.snippet = source.snippet;
        if (typeof source.section === 'string') projected.section = source.section;
        return projected;
    }).filter(Boolean);
}

export function projectRestoredCitations(citations) {
    if (!citations || typeof citations !== 'object') return undefined;
    return {
        parsed: citations.parsed === true,
        cited: Array.isArray(citations.cited)
            ? citations.cited.map((citation) => ({
                marker: citation?.marker,
                id: citation?.id,
            }))
            : [],
    };
}

function applyBoundedEvidence(turn, payload) {
    const sources = projectRestoredSources(payload.sources);
    if (sources) turn.sources = sources;
    else delete turn.sources;
    const citations = projectRestoredCitations(payload.citations);
    if (citations) turn.citations = citations;
    else delete turn.citations;
    if (typeof payload.exchange_id === 'string' && payload.exchange_id) {
        turn.exchange_id = payload.exchange_id;
    }
    if (Array.isArray(payload.source_exchange_ids)) {
        turn.source_exchange_ids = payload.source_exchange_ids.filter((id) => typeof id === 'string');
    } else {
        delete turn.source_exchange_ids;
    }
    if (typeof payload.thread_id === 'string' && payload.thread_id) {
        turn.thread_id = payload.thread_id;
    }
}

function restoreEnvelopes(payload) {
    const bq = payload?.business_query;
    if (Array.isArray(bq?.envelopes) && bq.envelopes.length > 0) return bq.envelopes;
    if (bq?.envelope) return [bq.envelope];
    return [];
}

function tableIdForEnvelope(envelope, count) {
    if (typeof envelope?.table_id === 'string' && envelope.table_id) return envelope.table_id;
    if (count === 1) return '__default__';
    return null;
}

function projectResultSlice(table, payload, single) {
    const rows = Array.isArray(table?.rows) ? table.rows : [];
    const columns = Array.isArray(table?.columns) ? table.columns : [];
    const presentation = table?.presentation || (single ? (payload?.presentation || null) : null);
    return {
        columns,
        presentation,
        rows,
        returned: Number.isInteger(table?.returned_row_count) ? table.returned_row_count : rows.length,
        total: Number.isInteger(table?.total_row_count) ? table.total_row_count : null,
        aggregate: false,
        streaming: false,
        enterFrom: 0,
    };
}

function clearAuthorizedProjection(turn) {
    if (!(turn.result instanceof Map)) turn.result = new Map();
    turn.result.clear();
    for (const slice of restoreSlices.values()) {
        try {
            slice.clear?.(turn);
        } catch (_) {}
    }
    delete turn.sources;
    delete turn.citations;
    delete turn.exchange_id;
    delete turn.source_exchange_ids;
    delete turn.thread_id;
}

function applyAuthorizedPayload(store, turn, payload) {
    if (payload.run_id && !turn.runId) store.bind(turn.msgId, payload.run_id);
    clearAuthorizedProjection(turn);
    // A projected restore carries the same tables the live paint used, keyed by
    // table id; older payloads fall back to the sealed envelope columns.
    const tables = Array.isArray(payload.tables) ? payload.tables.filter((t) => t && typeof t === 'object') : [];
    if (tables.length > 0) {
        for (const table of tables) {
            if (typeof table.table_id !== 'string' || !table.table_id) continue;
            turn.result.set(table.table_id, projectResultSlice(table, payload, tables.length === 1));
        }
    } else {
        const envelopes = restoreEnvelopes(payload);
        const count = envelopes.length;
        for (const envelope of envelopes) {
            const tableId = tableIdForEnvelope(envelope, count);
            if (!tableId) continue;
            turn.result.set(tableId, projectResultSlice(envelope, payload, count === 1));
        }
    }
    if (turn.result.size === 0 && payload.presentation) {
        turn.result.set('__default__', {
            columns: [],
            presentation: payload.presentation || null,
            rows: [],
            returned: 0,
            total: null,
            aggregate: false,
            streaming: false,
            enterFrom: 0,
        });
    }
    applyBoundedEvidence(turn, payload);
    for (const slice of restoreSlices.values()) {
        try {
            slice.restore?.(turn, payload);
        } catch (_) {
            try {
                slice.clear?.(turn);
            } catch (_) {}
            applyBoundedEvidence(turn, payload);
        }
    }
}

export const turnStore = {
    registerRestoreSlice(name, slice) {
        if (!name || typeof slice !== 'object') return () => {};
        restoreSlices.set(name, slice);
        return () => restoreSlices.delete(name);
    },
    open(msgId) {
        if (!msgId) return null;
        if (!turns.has(msgId)) turns.set(msgId, blank(msgId));
        return turns.get(msgId);
    },
    bind(msgId, runId) {
        const turn = this.open(msgId);
        if (turn && runId && !turn.runId) {
            turn.runId = runId;
            turn.status = 'submitted';
            messageByRun.set(runId, msgId);
        }
        return turn;
    },
    /** One status per run. A terminal status never changes again. */
    status(runId) {
        const turn = this.byRun(runId);
        return turn?.status ?? null;
    },
    isTerminal(runId) {
        return TERMINAL.has(this.status(runId));
    },
    /**
     * Ends a run once. Returns true when this call made it terminal. Every module
     * that needs "the run is over" listens for the one turn_terminal event this
     * publishes, instead of deciding from turn_outcome or stream_error on its own.
     */
    endRun(runId, status, durationMs) {
        const turn = this.byRun(runId);
        if (!turn || !TERMINAL.has(status) || TERMINAL.has(turn.status)) return false;
        turn.status = status;
        turn.durationMs = durationMs ?? null;
        this.save();
        terminalListeners.forEach((fn) => fn({ run_id: runId, status, duration_ms: durationMs ?? null, provisional: durationMs === null || durationMs === undefined }));
        return true;
    },
    onTerminal(listener) {
        terminalListeners.add(listener);
        return () => terminalListeners.delete(listener);
    },
    byRun(runId) {
        const msgId = messageByRun.get(runId);
        return msgId ? turns.get(msgId) || null : null;
    },
    byMessage(msgId) {
        return turns.get(msgId) || null;
    },
    resolve(runId, fallbackMsgId) {
        if (runId && messageByRun.has(runId)) return this.byRun(runId);
        const turn = fallbackMsgId ? this.open(fallbackMsgId) : null;
        if (!turn) return null;
        if (turn.runId && runId && turn.runId !== runId) return null;
        return runId ? this.bind(fallbackMsgId, runId) : turn;
    },
    all() {
        return [...turns.values()];
    },

    save() {
        try {
            if (typeof sessionStorage === 'undefined') return;
            const out = {};
            let hasData = false;
            for (const [msgId, turn] of turns.entries()) {
                const serialized = serializeTurn(turn);
                if (serialized) {
                    out[msgId] = serialized;
                    hasData = true;
                }
            }
            const key = storageKey();
            if (hasData) {
                sessionStorage.setItem(key, JSON.stringify(out));
            } else {
                sessionStorage.removeItem(key);
            }
        } catch (_) {}
    },
    _hydrated: false,
    isHydrated() {
        return this._hydrated;
    },
    ensureHydrated() {
        if (typeof window !== 'undefined' && window.McpAskAiRestoreGate?.state?.() === 'checking') {
            return;
        }
        if (!this._hydrated) {
            this.hydrate();
        }
    },
    hydrate() {
        this._hydrated = true;
        try {
            if (typeof sessionStorage === 'undefined') return;
            const key = storageKey();
            const raw = sessionStorage.getItem(key);
            if (!raw) return;
            const data = JSON.parse(raw);
            if (!data || typeof data !== 'object') return;
            for (const [msgId, item] of Object.entries(data)) {
                if (!msgId || !item) continue;
                const turn = this.open(msgId);
                if (!turn) continue;
                delete turn.startedAt;
                delete turn.timeline;
                if (item.runId && !turn.runId) {
                    turn.runId = item.runId;
                    messageByRun.set(item.runId, msgId);
                }
                // Cached rows and work steps never hydrate a render view: only a
                // server payload for this turn's restore_ref fills them (spec §7).
                if (item.restore_ref) turn.restore_ref = item.restore_ref;
            }
        } catch (_) {}
    },
    applyAuthorizedRestore(data) {
        // Modules may have mounted while the gate was checking, so the cached
        // identities (msgId, runId, restore_ref) are loaded here first.
        if (!this._hydrated) this.hydrate();
        const byRef = {};
        const unresolved = new Set();
        for (const row of data?.results || []) {
            if (!row || typeof row.restore_ref !== 'string') continue;
            if (row.status === 'authorized' && row.payload && typeof row.payload === 'object') {
                byRef[row.restore_ref] = row.payload;
            } else {
                unresolved.add(row.restore_ref);
            }
        }
        for (const turn of this.all()) {
            const payload = turn.restore_ref ? byRef[turn.restore_ref] : null;
            if (!payload || !TERMINAL.has(payload.run_status)) continue;
            // The server's terminal truth for this run. It never starts a ticker:
            // a restored turn has no startedAt in this page session.
            if (!TERMINAL.has(turn.status)) {
                turn.status = restoredStatus(payload);   // done | clarification | declined | failed | stopped
                turn.durationMs = Number.isFinite(payload.duration_ms) ? payload.duration_ms : null;  // the server retains the duration for every terminal, stopped included
            }
        }
        for (const turn of turns.values()) {
            const ref = turn.restore_ref;
            if (!ref) continue;
            const payload = byRef[ref];
            if (!payload) {
                if (unresolved.has(ref)) clearAuthorizedProjection(turn);
                continue;
            }
            applyAuthorizedPayload(this, turn, payload);
        }
        this._hydrated = true;
    },
    hasPersisted() {
        try {
            if (typeof sessionStorage === 'undefined') return false;
            return Boolean(sessionStorage.getItem(storageKey()));
        } catch (_) {
            return false;
        }
    },
    resetInMemory() {
        for (const turn of turns.values()) {
            delete turn.sources;
            delete turn.citations;
            for (const slice of restoreSlices.values()) {
                try {
                    slice.clear?.(turn);
                } catch (_) {}
            }
        }
        turns.clear();
        messageByRun.clear();
        this._hydrated = false;
    },
    prune(isPresent) {
        const removed = [];
        for (const [msgId, turn] of turns) {
            if (isPresent(msgId)) continue;
            turns.delete(msgId);
            if (turn.runId) messageByRun.delete(turn.runId);
            removed.push(turn);
            for (const listener of removeListeners) listener(turn);
        }
        if (removed.length > 0) {
            this.save();
        }
        return removed;
    },
    /** A module that keeps a timer on its slice clears it here, whichever module pruned. */
    onRemove(listener) {
        removeListeners.add(listener);
        return () => removeListeners.delete(listener);
    },
    clear() {
        this.resetInMemory();
        try {
            if (typeof sessionStorage !== 'undefined') {
                sessionStorage.removeItem(storageKey());
            }
        } catch (_) {}
    },
};

