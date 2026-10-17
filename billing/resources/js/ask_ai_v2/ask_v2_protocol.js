/**
 * Ask AI v2 protocol adapter.
 *
 * v2 has its own request contract and its own event vocabulary. The panel is
 * the legacy Ask AI surface and understands neither. This adapter is the one
 * seam between them: it rewrites the request body v2 requires, and renames each
 * v2 event to the canonical event the panel already handles.
 *
 * Events the panel has no concept of — the activity spine and streamed typed
 * tables — are published on a bus instead, where opt-in modules render them.
 * They are deliberately not translated into panel events, because the panel
 * must render identically whether those modules are on or off.
 */

import { stepLabel } from './modules/step_labels.js';
import { isEnabled } from './modules/registry.js';
import { reasonCopy } from './reason_copy.js';
import { terminalStatus, turnStore } from './turn_state.js';
import { projectToolResult, projectCitationSet, projectRecordLinks, projectUiLinks } from './tool_result.js';

const CLIENT_TIMEOUT_MS = 25000;
const IGNORED_ACTIVITIES = new Set(['understand', 'reading_page', 'authorized']);
const ACTIVITY_ALIASES = { plan_search: 'planning' };
const INVALID_INTERACTION_COPY = 'I need one more detail to answer, but the choices could not be shown. Please ask again.';
const TERMINAL = new Set(['turn_outcome', 'stream_error']);

/** Bus the adapter publishes v2-only events on. Modules subscribe to it. */
export const askV2Bus = new EventTarget();
turnStore.onTerminal((detail) => publish('turn_terminal', detail));

// Publications per run, in arrival order. An activity transition waits one
// animation frame so a burst of frames paints as a sequence; anything else
// drains as soon as it reaches the head, so a terminal frame waits behind the
// steps that precede it and never overtakes them. A frame with no run id has
// no queue and publishes at once.
const queues = new Map();
const invalidInteractions = new Set();

// Detail facts arrive in their own frames ahead of the terminal so the receipt
// stays within the producer's frame bound; the terminal envelope carries none.
// They are folded back here, by owner, so the panel keeps reading one canonical
// envelope. The binding rule mirrors the producer's _partition_record_results:
// a detail belongs to the record whose id equals its owner_id, and to no record
// when it names none. Keyed run id -> answer query id -> details.
const streamedDetails = new Map();

function rememberDetails(payload) {
    if (!payload.run_id || !Array.isArray(payload.record_details)) return;
    let byQuery = streamedDetails.get(payload.run_id);
    if (!byQuery) {
        byQuery = new Map();
        streamedDetails.set(payload.run_id, byQuery);
    }
    const key = payload.answer_query_id || '';
    byQuery.set(key, (byQuery.get(key) || []).concat(payload.record_details));
}

function ownsDetail(ref, detail) {
    if (!ref || detail?.owner_id === null || detail?.owner_id === undefined) return false;
    if (String(detail.owner_id) !== String(ref.record_id)) return false;
    return detail.owner_resource === null || detail.owner_resource === undefined
        || detail.owner_resource === ref.resource;
}

function foldDetails(envelope, byQuery) {
    const details = byQuery.get(envelope?.answer_query_id || '') || [];
    if (!envelope || !details.length) return envelope;
    const records = Array.isArray(envelope.records)
        ? envelope.records.map((record) => ({ ...record, details: details.filter((d) => ownsDetail(record?.ref, d)) }))
        : envelope.records;
    return { ...envelope, records, record_details: details };
}

function withStreamedDetails(outcome) {
    const byQuery = streamedDetails.get(outcome.run_id);
    streamedDetails.delete(outcome.run_id);
    const wire = outcome.business_query;
    if (!byQuery || !wire || wire.outcome !== 'answered' || !wire.envelope) return outcome;
    const envelopes = Array.isArray(wire.envelopes) && wire.envelopes.length
        ? wire.envelopes.map((envelope) => foldDetails(envelope, byQuery))
        : [foldDetails(wire.envelope, byQuery)];
    return { ...outcome, business_query: { ...wire, envelope: envelopes[0], envelopes } };
}

function hidden() {
    return typeof document !== 'undefined' && document.visibilityState === 'hidden';
}

// A hidden tab has no frames to paint into (the browser pauses them), so the
// queue drains on the task queue instead and catches up when the tab returns.
// Returns the pending frame's id, or null when the callback went to the task queue.
function nextFrame(fn) {
    if (!hidden() && typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
        return window.requestAnimationFrame(fn);
    }
    setTimeout(fn, 0);
    return null;
}

// A frame requested while the tab was visible never fires once the tab is hidden.
// Move that callback to the task queue so the run keeps draining in the background.
function releaseFrame(queue) {
    if (queue.frame === null || typeof queue.pending !== 'function') return;
    if (typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(queue.frame);
    }
    queue.frame = null;
    setTimeout(queue.pending, 0);
}

function enqueue(runId, type, detail) {
    if (!runId) {
        publish(type, detail);
        return;
    }
    if (turnStore.isTerminal(runId)) return;
    let queue = queues.get(runId);
    if (!queue) {
        queue = { items: [], waiting: false, frame: null, pending: null };
        queues.set(runId, queue);
    }
    queue.items.push({ type, detail });
    drain(runId);
}

function drain(runId) {
    const queue = queues.get(runId);
    if (!queue || queue.waiting) return;
    while (queue.items.length) {
        if (queue.items[0].type === 'activity') {
            queue.waiting = true;
            queue.pending = () => {
                if (queues.get(runId) !== queue) return; // reset while waiting
                queue.waiting = false;
                queue.frame = null;
                queue.pending = null;
                const item = queue.items.shift();
                if (item) publish(item.type, item.detail);
                drain(runId);
            };
            queue.frame = nextFrame(queue.pending);
            return;
        }
        const item = queue.items.shift();
        publish(item.type, item.detail);
        if (TERMINAL.has(item.type)) {
            queues.delete(runId);
            return;
        }
    }
}

/** Drops every pending publication. A new turn or a closed panel makes them stale. */
export function resetProjectionQueues() {
    for (const turn of turnStore.all()) {
        const runId = turn.runId;
        if (runId && turn.timeline?.state === 'running') {
            turnStore.endRun(runId, 'stopped', null);
        }
    }
    queues.clear();
    invalidInteractions.clear();
    streamedDetails.clear();
}

if (typeof window !== 'undefined') {
    window.addEventListener('mcp:askai:turn-started', resetProjectionQueues);
    window.addEventListener('mcp:askai:turn-stopped', (event) => {
        const runId = event.detail?.runId;
        if (runId) turnStore.endRun(runId, 'stopped', null);
    });
    document.addEventListener('visibilitychange', () => {
        for (const queue of queues.values()) {
            if (hidden() && queue.waiting) releaseFrame(queue);
        }
        for (const runId of [...queues.keys()]) drain(runId);
    });
}

function randomHex32() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function publish(type, detail) {
    askV2Bus.dispatchEvent(new CustomEvent(type, { detail }));
}

/**
 * Rewrites a legacy request body into the v2 contract.
 *
 * The panel supplies the question, thread, page context and record context. v2
 * additionally requires an operation, a run id, an idempotency key and an
 * absolute client deadline, and it requires a thread id on the very first turn
 * where the panel still has none.
 */
export function buildV2Body(v1Body) {
    const operation = v1Body.operation === 'regenerate'
        ? 'regenerate'
        : v1Body.operation === 'clarification_reply'
            ? 'clarification_reply'
            : 'new_question';

    const body = {
        protocol_version: '2',
        operation,
        run_id: v1Body.run_id || randomHex32(),
        thread_id: v1Body.thread_id || `th-${randomHex32()}`,
        idempotency_key: v1Body.idempotency_key || `idemp-${randomHex32()}`,
        deadline_at_ms: Date.now() + CLIENT_TIMEOUT_MS,
        response_policy: 'allow_partial',
    };

    if (operation === 'regenerate') {
        body.continuation_ref = v1Body.target_exchange_id;
    } else if (operation === 'clarification_reply') {
        const hasChoice = Boolean(v1Body.clarification_choice_id);
        const hasText = Boolean(v1Body.clarification_free_text);
        if (hasChoice === hasText) {
            throw new Error('clarification_reply needs exactly one of choice id or free text');
        }
        body.continuation_ref = v1Body.continuation_ref;
        if (hasChoice) {
            body.clarification_choice_id = v1Body.clarification_choice_id;
        } else {
            body.clarification_free_text = v1Body.clarification_free_text;
        }
    } else {
        body.question = v1Body.question;
    }
    if (v1Body.page_context) {
        body.page_context = v1Body.page_context;
    }
    if (v1Body.record_context) {
        body.record_context = v1Body.record_context;
    }
    return body;
}

/**
 * Maps a restored turn to the v2 request the panel sends for regenerate
 * or an explanation follow-up. Regenerates bind continuation_ref to the
 * restored exchange id. Explanations stay on the same thread as a new
 * question and never replay the original data question.
 */
export function followupBodyFromRestoredTurn(turn, kind, question) {
    const threadId = turn?.thread_id;
    if (kind === 'regenerate') {
        const continuation = turn?.exchange_id || (Array.isArray(turn?.source_exchange_ids) ? turn.source_exchange_ids[0] : null);
        return buildV2Body({
            operation: 'regenerate',
            target_exchange_id: continuation,
            thread_id: threadId,
        });
    }
    if (kind === 'explain') {
        return buildV2Body({
            operation: 'ask',
            question: question || 'simplify that',
            thread_id: threadId,
        });
    }
    throw new Error('followupBodyFromRestoredTurn needs regenerate or explain');
}

/**
 * Renames one v2 event to the canonical events the panel handles.
 *
 * @returns {Array<{event: string, payload: object}>} zero or more panel events
 */
export function normalizeV2Event(event, payload) {
    const type = payload?.event_type || event;

    switch (type) {
        case 'turn_accepted': {
            const turn = turnStore.byRun(payload.run_id);
            if (turn) {
                if (payload.restore_ref && !turn.restore_ref) turn.restore_ref = payload.restore_ref;
                turnStore.save();
            }
            const accepted = { run_id: payload.run_id, thread_id: payload.thread_id, restore_ref: payload.restore_ref };
            enqueue(payload.run_id, 'accepted', accepted);
            return [{ event: 'accepted', payload: accepted }];
        }
        case 'text_delta':
            if (payload.content_kind === 'table_fallback' && isEnabled('result_table')) {
                return [];
            }
            return [{ event: 'token', payload: { d: payload.delta } }];

        case 'activity': {
            const kind = ACTIVITY_ALIASES[payload.activity_kind] || payload.activity_kind;
            if (IGNORED_ACTIVITIES.has(kind)) {
                return [];
            }
            enqueue(payload.run_id, 'activity', { ...payload, activity_kind: kind });
            if (payload.state !== 'running') {
                return [];
            }
            return [{ event: 'status', payload: { phase: kind, tool: payload.tool_kind, label: stepLabel(kind, 'running', payload.subject) || 'Working' } }];
        }

        case 'table_start':
        case 'table_rows':
        case 'table_end':
            enqueue(payload.run_id, type, payload);
            return [];

        case 'record_details':
            rememberDetails(payload);
            return [];

        case 'thought_start':
        case 'thought_delta':
        case 'thought_done':
            if (isEnabled('thought_stream')) {
                enqueue(payload.run_id, type, payload);
            }
            return [];

        case 'result_stream_authorized':
            enqueue(payload.run_id, 'result_authorized', payload);
            return [];

        case 'interaction':
            if (payload.continuation_ref && payload.prompt && (payload.options?.length || payload.free_text_allowed)) {
                enqueue(payload.run_id, 'interaction', payload);
            } else {
                invalidInteractions.add(payload.run_id);
            }
            return [];

        case 'citation_set':
            enqueue(payload.run_id, 'citation_set', payload);
            return projectCitationSet(payload);

        case 'turn_outcome': {
            const toolResult = projectToolResult(payload);
            const isInvalid = toolResult.kind === 'invalid';
            const invalidInteraction = payload.outcome_type === 'clarification_required' && invalidInteractions.delete(payload.run_id);

            let normalizedOutcomeType = payload.outcome_type;
            let normalizedTrusted = payload.trusted;
            let normalizedMessage = payload.message;

            if (isInvalid) {
                normalizedOutcomeType = 'incomplete';
                normalizedTrusted = false;
            } else if (invalidInteraction) {
                normalizedOutcomeType = 'incomplete';
                normalizedMessage = INVALID_INTERACTION_COPY;
            }

            const outcome = withStreamedDetails({
                ...payload,
                outcome_type: normalizedOutcomeType,
                trusted: normalizedTrusted,
                message: normalizedMessage,
                ...(toolResult.kind === 'v1' ? {
                    tool_result_version: 1,
                    completeness: toolResult.completeness,
                    omissions: toolResult.omissions,
                    components: toolResult.components,
                    restore_ref: toolResult.restoreRef,
                } : {}),
            });
            enqueue(payload.run_id, 'turn_outcome', outcome);
            turnStore.endRun(payload.run_id, terminalStatus(outcome.outcome_type), outcome.duration_ms ?? null);
            return [...projectRecordLinks(outcome), ...projectUiLinks(outcome), { event: 'done', payload: {
                thread_id: outcome.thread_id || null,
                answer_query_id: outcome.answer_query_id,
                evidence_digest: outcome.evidence_digest,
                query_record_ref: outcome.query_record_ref,
                outcome: outcome.outcome_type,
                trusted: outcome.trusted,
                duration_ms: outcome.duration_ms ?? null,
                answer_mode: outcome.answer_mode ?? null,
                source_exchange_ids: Array.isArray(outcome.source_exchange_ids) ? outcome.source_exchange_ids : [],
                // Follow-ups are a card the interaction module paints from the
                // bus; the panel's chips never render them.
                follow_up_suggestions: [],
                business_query: outcome.business_query || null,
                budget: outcome.budget ?? null,
                reason_code: outcome.reason_code,
                feedback_token: outcome.feedback_token || null,
                message: (outcome.outcome_type === 'clarification_required' || outcome.outcome_type === 'answered')
                    ? (outcome.message || null)
                    : (outcome.message || reasonCopy(outcome.reason_code)),
                ...(toolResult.kind === 'v1' ? {
                    tool_result_version: 1,
                    completeness: toolResult.completeness,
                    omissions: toolResult.omissions,
                    components: toolResult.components,
                    restore_ref: toolResult.restoreRef,
                } : {}),
            } }];
        }

        case 'stream_error':
            streamedDetails.delete(payload.run_id);
            enqueue(payload.run_id, 'stream_error', payload);
            turnStore.endRun(payload.run_id, 'failed', null);
            return [{ event: 'error', payload: { error: payload.code, detail: payload.message || reasonCopy(payload.code), retryable: payload.retryable } }];

        default:
            return [];
    }
}

export { projectToolResult, projectCitationSet, projectRecordLinks, projectUiLinks } from './tool_result.js';

function isExplanationFollowup(question) {
    return typeof question === 'string' && question.trim().toLowerCase() === 'simplify that';
}

function isAuthorizedCacheRestore(turn) {
    if (typeof turn?.restore_ref !== 'string' || !turn.restore_ref) return false;
    if (typeof turn.exchange_id === 'string' && turn.exchange_id) return true;
    return Array.isArray(turn.source_exchange_ids)
        && turn.source_exchange_ids.some((id) => typeof id === 'string' && id);
}

function matchesRestoredExchange(turn, target) {
    return turn.exchange_id === target
        || (Array.isArray(turn.source_exchange_ids) && turn.source_exchange_ids.includes(target));
}

function activeRestoredTurn(v1Body) {
    const restored = turnStore.all().filter(isAuthorizedCacheRestore);
    if (!restored.length) return null;
    const threadId = v1Body?.thread_id;
    const scoped = threadId ? restored.filter((turn) => turn.thread_id === threadId) : restored;
    const pool = scoped.length ? scoped : restored;
    const target = v1Body?.target_exchange_id;
    if (target) {
        return pool.find((turn) => matchesRestoredExchange(turn, target)) || null;
    }
    return pool[pool.length - 1];
}

function bindPanelIds(body, v1Body) {
    if (v1Body?.run_id) body.run_id = v1Body.run_id;
    if (v1Body?.page_context) body.page_context = v1Body.page_context;
    if (v1Body?.record_context) body.record_context = v1Body.record_context;
    return body;
}

/**
 * Production request seam. Regenerates and explanation follow-ups after an
 * authorized restore bind to that turn. Every other request still uses the
 * ordinary v1-to-v2 rewrite.
 */
export function buildProtocolBody(v1Body) {
    const turn = activeRestoredTurn(v1Body);
    if (turn) {
        if (v1Body.operation === 'regenerate') {
            return bindPanelIds(followupBodyFromRestoredTurn(turn, 'regenerate'), v1Body);
        }
        if (isExplanationFollowup(v1Body.question)) {
            return bindPanelIds(followupBodyFromRestoredTurn(turn, 'explain', v1Body.question.trim()), v1Body);
        }
    }
    return buildV2Body(v1Body);
}

export const askV2Protocol = {
    name: 'v2',
    buildBody: buildProtocolBody,
    normalizeEvent: normalizeV2Event,
    bus: askV2Bus,
};
