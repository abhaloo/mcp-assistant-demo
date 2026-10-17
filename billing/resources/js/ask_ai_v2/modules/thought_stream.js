/**
 * Composable Thought Stream module for Ask AI v2.
 *
 * Keeps one reasoning transcript per timeline step: a thought frame names the
 * step it belongs to (activity_id), so the coordinator's decision and the
 * planner's lookup each keep their own text and duration. A frame with no step,
 * as older servers send it, is kept under the run alone. Applies a 500 ms
 * anti-flicker floor on completion, persists finished transcripts to
 * sessionStorage (20-entry cap, per-user key), and announces state changes via
 * 'thought_state' events on the bus.
 */

import { activeMessageId, onThreadRender } from './module_host.js';

const MIN_DURATION_MS = 500;
const MAX_REMEMBERED = 20;

let busInstance = null;
let stopRenderWatch = null;
let allowCacheHydrate = false;
const thoughts = new Map(); // thoughtKey(run_id, activity_id) -> { msgId, runId, activityId, text, finished, durationMs, startedAt, timer }
const currentKeyByRun = new Map(); // run_id -> key chosen on thought_start

// One key per (run, step). A frame without a step shares the run's own key.
function thoughtKey(runId, activityId) {
    return activityId ? `${runId}::${activityId}` : runId;
}

function isInFlight(state) {
    return Boolean(state) && !state.finished && !state.timer;
}

function bindRunKey(runId, activityId) {
    const key = thoughtKey(runId, activityId);
    const previousKey = currentKeyByRun.get(runId);
    if (previousKey && previousKey !== key && isInFlight(thoughts.get(previousKey))) {
        currentKeyByRun.delete(runId);
        return key;
    }
    currentKeyByRun.set(runId, key);
    return key;
}

function inFlightThoughtForRun(runId) {
    const open = [];
    for (const state of thoughts.values()) {
        if (state.runId === runId && isInFlight(state)) {
            open.push(state);
        }
    }
    return open.length === 1 ? open[0] : undefined;
}

function stateForEvent(runId, activityId) {
    if (!runId) return undefined;
    if (activityId) {
        return thoughts.get(thoughtKey(runId, activityId));
    }
    const key = currentKeyByRun.get(runId);
    if (key) return thoughts.get(key);
    return inFlightThoughtForRun(runId);
}

// Withhold cached thoughts; thoughts streamed live in this session stay.
export function quarantineThoughtCache() {
    allowCacheHydrate = false;
}

export function releaseThoughtCache() {
    allowCacheHydrate = true;
    hydrateThoughts();
}

export function thoughtFor(runId, activityId = null) {
    if (!runId) return null;
    const key = thoughtKey(runId, activityId);
    if (!thoughts.has(key)) return null;
    if (typeof window !== 'undefined' && window.McpAskAiRestoreGate?.state?.() === 'checking') {
        return null;
    }
    const t = thoughts.get(key);
    return {
        text: t.text,
        finished: Boolean(t.finished),
        durationMs: t.durationMs || 0,
    };
}

function notifyState(runId, activityId) {
    if (busInstance && runId) {
        const detail = { run_id: runId };
        if (activityId) detail.activity_id = activityId;
        busInstance.dispatchEvent(new CustomEvent('thought_state', { detail }));
    }
}

function evictionKey() {
    let oldestUnfinished = null;
    for (const [key, state] of thoughts) {
        if (state.finished) {
            return key;
        }
        if (oldestUnfinished === null) {
            oldestUnfinished = key;
        }
    }
    return oldestUnfinished;
}

function remember(key, state) {
    const existing = thoughts.get(key);
    if (existing?.timer) {
        clearTimeout(existing.timer);
        existing.timer = null;
    }
    thoughts.set(key, state);
    while (thoughts.size > MAX_REMEMBERED) {
        const victim = evictionKey();
        if (!victim) {
            break;
        }
        const dropped = thoughts.get(victim);
        if (dropped?.timer) clearTimeout(dropped.timer);
        thoughts.delete(victim);
    }
}

function handleThoughtStart(event) {
    const { run_id, activity_id } = event.detail || {};
    if (!run_id) return;
    const msgId = event.detail?.message_id || activeMessageId();
    if (!msgId) return;
    const activityId = activity_id || null;
    const key = bindRunKey(run_id, activityId);
    const state = { msgId, runId: run_id, activityId, text: '', finished: false, durationMs: 0, startedAt: Date.now(), timer: null };
    remember(key, state);
    notifyState(run_id, activityId);
}

function handleThoughtDelta(event) {
    const { run_id, activity_id, delta } = event.detail || {};
    if (!run_id || !delta) return;
    const state = stateForEvent(run_id, activity_id || null);
    if (!state || state.finished || state.timer) return;
    state.text += delta;
    notifyState(run_id, state.activityId);
}

const THOUGHTS_PREFIX = 'mcp.askAi.v2Thoughts';

function getUserId() {
    if (typeof document === 'undefined') return null;
    return document.querySelector('meta[name="user-id"]')?.getAttribute('content') || null;
}

function thoughtsStorageKey() {
    const uid = getUserId();
    if (uid) return `${THOUGHTS_PREFIX}.${uid}`;
    return THOUGHTS_PREFIX;
}

function saveThoughts() {
    try {
        if (typeof sessionStorage === 'undefined') return;
        const out = {};
        let hasData = false;
        for (const [key, t] of thoughts.entries()) {
            if (!t.finished || !t.text) continue;
            out[key] = {
                msgId: t.msgId,
                runId: t.runId,
                activityId: t.activityId || null,
                text: t.text,
                finished: true,
                durationMs: t.durationMs,
            };
            hasData = true;
        }
        const key = thoughtsStorageKey();
        if (hasData) {
            sessionStorage.setItem(key, JSON.stringify(out));
        } else {
            sessionStorage.removeItem(key);
        }
    } catch (_) {}
}

function hydrateThoughts() {
    if (typeof window !== 'undefined' && window.McpAskAiRestoreGate && !allowCacheHydrate) {
        return;
    }
    try {
        if (typeof sessionStorage === 'undefined') return;
        const raw = sessionStorage.getItem(thoughtsStorageKey());
        if (!raw) return;
        const data = JSON.parse(raw);
        if (!data || typeof data !== 'object') return;
        for (const [key, t] of Object.entries(data)) {
            if (!key || !t || !t.text) continue;
            if (!thoughts.has(key)) {
                // Entries saved before steps were addressed carry only the run id.
                const runId = t.runId || key;
                const activityId = t.activityId || null;
                thoughts.set(key, {
                    msgId: t.msgId,
                    runId,
                    activityId,
                    text: t.text,
                    finished: true,
                    durationMs: t.durationMs || 0,
                    startedAt: 0,
                    timer: null,
                });
                notifyState(runId, activityId);
            }
        }
    } catch (_) {}
}

function handleThoughtDone(event) {
    const { run_id, activity_id, duration_ms } = event.detail || {};
    const state = stateForEvent(run_id, activity_id || null);
    if (!state || state.finished || state.timer) return;

    const elapsed = Date.now() - state.startedAt;
    const finalize = () => {
        state.timer = null;
        state.finished = true;
        state.durationMs = duration_ms != null ? duration_ms : elapsed;
        saveThoughts();
        notifyState(run_id, state.activityId);
    };
    if (elapsed < MIN_DURATION_MS) {
        state.timer = setTimeout(finalize, MIN_DURATION_MS - elapsed);
    } else {
        finalize();
    }
}

function repaint() {
    if (thoughts.size === 0) {
        hydrateThoughts();
    }
    for (const t of thoughts.values()) {
        notifyState(t.runId, t.activityId);
    }
}

export const thoughtStreamModule = {
    name: 'thought_stream',
    mount(bus) {
        busInstance = bus;
        hydrateThoughts();
        bus.addEventListener('thought_start', handleThoughtStart);
        bus.addEventListener('thought_delta', handleThoughtDelta);
        bus.addEventListener('thought_done', handleThoughtDone);
        stopRenderWatch = onThreadRender(repaint);
        repaint();
    },
    unmount() {
        if (stopRenderWatch) {
            stopRenderWatch();
            stopRenderWatch = null;
        }
        if (busInstance) {
            busInstance.removeEventListener('thought_start', handleThoughtStart);
            busInstance.removeEventListener('thought_delta', handleThoughtDelta);
            busInstance.removeEventListener('thought_done', handleThoughtDone);
        }
        for (const state of thoughts.values()) {
            if (state.timer) {
                clearTimeout(state.timer);
                state.timer = null;
                state.finished = true;
            }
        }
        if (allowCacheHydrate || typeof window === 'undefined' || !window.McpAskAiRestoreGate) {
            saveThoughts();
        }
        thoughts.clear();
        currentKeyByRun.clear();
        busInstance = null;
    },
};
