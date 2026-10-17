/**
 * Work timeline module.
 *
 * One collapsible block per turn, listing the steps the server reported with
 * a live state and duration each. The block opens while the turn runs, shows
 * the server's timing receipt when it ends, and closes on its own once.
 */

import {
    activeMessageId,
    claimPanelProgress,
    claimProgressOwnership,
    messageElement,
    mountInSlot,
    onThreadRender,
    releasePanelProgress,
} from './module_host.js';
import { formatDuration, headerLabel as formatHeaderLabel, stepLabel, stepLabelParts } from './step_labels.js';
import { stepRows } from './step_rows.js';
import { turnStore } from '../turn_state.js';
import { thoughtFor } from './thought_stream.js';
import { mcpIcon } from './icons.js';

const MARKER = 'data-mcp-ask-v2-work';
const OWNER_CLASS = 'mcp-ask-ai__msg--v2-work';
const LOGO_SRC = '/img/loaders/ai-chat-loader.svg';
const AUTO_CLOSE_MS = 1000;
const TICK_MS = 1000;
const TURN_STARTED = 'mcp:askai:turn-started';
const CLOSES = new Set(['completed', 'declined', 'clarification']);
const THOUGHT_HOST_KINDS = ['planning', 'thinking'];
const TERMINAL_STATUSES = new Set(['done', 'declined', 'clarification', 'failed', 'stopped']);

let bus = null;
let stopRenderWatch = null;
let stopRemoveWatch = null;
let ticker = null;

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
const timelineState = (status) => (status === 'done' ? 'completed' : ['declined', 'clarification', 'stopped'].includes(status) ? status : 'failed');

function headerLabel(state, ms, phaseLabel = null, stepCount = 0) {
    if (ms === null || ms === undefined) {
        if (state === 'completed') return 'Done';
        if (state === 'declined') return 'Finished';
        if (state === 'clarification') return 'Needs your input';
        if (state === 'failed' || state === 'stopped') return 'Stopped';
        return phaseLabel || 'Working';
    }
    return formatHeaderLabel(state, ms, phaseLabel, stepCount);
}

function newTimeline() {
    return {
        steps: [],
        state: 'running',
        phaseLabel: null,
        durationMs: null,
        provisional: false,
        expectedOpen: true,
        userToggled: false,
        closed: false,
        closeTimer: null,
        renderedState: null,
    };
}

// A restored turn's steps and thought rows come only from its authorized restore
// payload, never from the cache, and are painted closed.
function restoredSteps(steps) {
    if (!Array.isArray(steps)) return [];
    return steps
        .filter((s) => s && typeof s.activity_id === 'string' && typeof s.kind === 'string')
        .map((s) => ({
            id: s.activity_id,
            kind: s.kind,
            subject: typeof s.subject === 'string' ? s.subject : undefined,
            state: s.state === 'failed' ? 'failed' : 'completed',
            elapsedMs: Number.isFinite(s.elapsed_ms) ? s.elapsed_ms : 0,
            observedAt: 0,
            ordinal: Number.isInteger(s.ordinal) ? s.ordinal : undefined,
            of: Number.isInteger(s.of) ? s.of : undefined,
        }));
}

function restoredThoughts(thoughts) {
    const byStep = new Map();
    for (const t of Array.isArray(thoughts) ? thoughts : []) {
        if (!t || typeof t.activity_id !== 'string' || typeof t.text !== 'string' || !t.text) continue;
        byStep.set(t.activity_id, { text: t.text, finished: true, durationMs: Number.isFinite(t.duration_ms) ? t.duration_ms : 0 });
    }
    return byStep;
}

export const workTimelineRestoreSlice = {
    name: 'work_timeline',
    restore(turn, payload) {
        turn.restoredSteps = restoredSteps(payload.steps);
        turn.restoredThoughts = restoredThoughts(payload.thoughts);
    },
    clear(turn) {
        delete turn.restoredSteps;
        delete turn.restoredThoughts;
        if (turn.timeline?.restored) turn.timeline = null;
    },
};

// Registered where the timeline is defined, so every page that loads it restores its steps.
turnStore.registerRestoreSlice(workTimelineRestoreSlice.name, workTimelineRestoreSlice);

function timelineOf(turn) {
    if (!turn.timeline) {
        if (turn?.status && TERMINAL_STATUSES.has(turn.status)) {
            turn.timeline = {
                steps: (turn.restoredSteps || []).map((s) => ({ ...s })),
                state: timelineState(turn.status),
                phaseLabel: null,
                durationMs: turn.durationMs ?? null,
                provisional: turn.durationMs == null,
                expectedOpen: false,
                userToggled: false,
                closed: false,
                closeTimer: null,
                renderedState: null,
                restored: true,
            };
        } else {
            turn.timeline = newTimeline();
        }
    }
    return turn.timeline;
}

function elapsed(turn) {
    if (turn?.timeline?.durationMs != null) return turn.timeline.durationMs;
    if (turn?.startedAt != null) return performance.now() - turn.startedAt;
    return null;
}

function stepElapsed(step) {
    return step.state === 'running' ? step.elapsedMs + (performance.now() - step.observedAt) : step.elapsedMs;
}

function mark(state) {
    const slot = document.createElement('span');
    slot.className = 'mcp-ask-v2__work-mark';
    slot.setAttribute('aria-hidden', 'true');
    if (state === 'running') {
        if (reducedMotion()) {
            slot.appendChild(Object.assign(document.createElement('span'), { className: 'mcp-ask-v2__mark-static' }));
        } else {
            const logo = document.createElement('img');
            logo.className = 'mcp-ask-v2__logo';
            logo.src = LOGO_SRC;
            logo.alt = '';
            logo.width = 24;
            logo.height = 24;
            slot.appendChild(logo);
        }
        return slot;
    }
    const iconName = {
        completed: 'circle-check',
        declined: 'circle-minus',
        clarification: 'circle-help',
        failed: 'circle-x',
        stopped: 'circle-x',
    }[state];
    if (iconName) {
        slot.appendChild(mcpIcon(iconName, `mcp-ask-v2__icon mcp-ask-v2__icon--${state}`));
    }
    return slot;
}

function stepState(state) {
    const el = document.createElement('span');
    el.className = 'mcp-ask-v2__step-state mcp-ask-v2__sr-only';
    el.setAttribute('role', 'status');
    el.textContent = state === 'running' ? 'Running' : (state === 'failed' || state === 'stopped') ? 'Stopped' : 'Completed';
    return el;
}

function pillPart(className, text) {
    const part = document.createElement('span');
    part.className = className;
    part.textContent = text;
    return part;
}

function pillFor(step, state) {
    const parts = stepLabelParts(step.kind, state, step.subject, step.ordinal, step.of);
    if (!parts) return null;
    const pill = document.createElement('span');
    pill.className = 'mcp-ask-v2__pill';
    pill.setAttribute('aria-label', parts.full);
    pill.appendChild(pillPart('mcp-ask-v2__pill-verb', parts.verb));
    if (parts.subject) pill.appendChild(pillPart('mcp-ask-v2__pill-subject', parts.subject));
    if (parts.ordinal) pill.appendChild(pillPart('mcp-ask-v2__pill-ordinal', parts.ordinal));
    if (parts.note) pill.appendChild(pillPart('mcp-ask-v2__pill-note', parts.note));
    return pill;
}

// Thought text is shown as plain words: markdown markers never reach the panel.
function plainThought(text) {
    return (text || '').replace(/\*\*|__|`/g, '').replace(/^#+\s*/gm, '');
}

function thoughtCaption(thought, state) {
    const base = thought.finished ? `Thought for ${formatDuration(thought.durationMs)}` : 'Thinking…';
    return state === 'failed' || state === 'stopped' ? `${base} — stopped` : base;
}

function labeledStep(step, state) {
    const base = stepLabel(step.kind, state, step.subject);
    if (!base) return null;
    if (step.of == null) return base;
    return `${base} (${step.ordinal + 1} of ${step.of})`;
}

function thoughtDetailsShouldOpen(step, thought) {
    return step.detailsUserToggled ? Boolean(step.detailsExpectedOpen) : !thought.finished;
}

function buildThoughtDetails(step, thought, state) {
    const details = document.createElement('details');
    details.className = 'mcp-ask-v2__planning';
    const summary = document.createElement('summary');
    summary.className = 'mcp-ask-v2__planning-summary';
    summary.setAttribute('aria-label', 'Reasoning transcript');
    summary.appendChild(pillPart('mcp-ask-v2__thought-caption', thoughtCaption(thought, state)));

    const pre = document.createElement('pre');
    pre.className = 'mcp-ask-v2__planning-body';
    pre.setAttribute('tabindex', '0');
    pre.setAttribute('role', 'region');
    pre.setAttribute('aria-label', 'Reasoning transcript');
    pre.textContent = plainThought(thought.text);

    details.append(summary, pre);

    // The transcript stays open while its step thinks and folds when it ends,
    // unless the reader toggled it; the memory lives on the step.
    const shouldOpen = thoughtDetailsShouldOpen(step, thought);
    details.open = shouldOpen;
    step.detailsExpectedOpen = shouldOpen;

    details.addEventListener('toggle', () => {
        if (details.open !== step.detailsExpectedOpen) {
            step.detailsUserToggled = true;
            step.detailsExpectedOpen = details.open;
        }
    });

    return details;
}

function bindUnaddressedThoughtStep(turn) {
    const t = turn?.timeline;
    if (!t) return null;
    if (!t.unaddressedThoughtStepId) {
        const firstHost = t.steps.find((s) => THOUGHT_HOST_KINDS.includes(s.kind));
        if (firstHost) t.unaddressedThoughtStepId = firstHost.id;
    }
    return t.unaddressedThoughtStepId || null;
}

// The transcript addressed to this step; a transcript with no step address
// (older servers) nests under the host bound once at ingest.
function thoughtForStep(step, turn) {
    if (!THOUGHT_HOST_KINDS.includes(step.kind)) return null;
    const runId = turn?.runId;
    const addressed = thoughtFor(runId, step.id) || turn?.restoredThoughts?.get(step.id) || null;
    if (addressed) return addressed;
    const boundId = turn?.timeline?.unaddressedThoughtStepId;
    if (boundId) {
        return boundId === step.id ? thoughtFor(runId) : null;
    }
    const unaddressed = thoughtFor(runId);
    if (!unaddressed) return null;
    return bindUnaddressedThoughtStep(turn) === step.id ? unaddressed : null;
}

function effectiveStepState(step, thought) {
    if (thought && step.state === 'running' && thought.finished) {
        return 'completed';
    }
    return step.state;
}

function hostStep(turn, row) {
    const id = row.thoughtStepId || row.id;
    return turn.timeline.steps.find((step) => step.id === id) || null;
}

// Stored member time plus the running member's clock, added once.
function rowLiveMs(row, turn) {
    const stored = Number.isFinite(row.storedElapsedMs) ? row.storedElapsedMs : 0;
    if (!row.runningMemberId) return stored;
    const member = turn.timeline.steps.find((step) => step.id === row.runningMemberId);
    if (!member || member.state !== 'running' || !Number.isFinite(member.observedAt)) return stored;
    const delta = performance.now() - member.observedAt;
    return Number.isFinite(delta) ? stored + Math.max(0, delta) : stored;
}

function rowDurationText(row, turn) {
    return formatDuration(rowLiveMs(row, turn));
}

function pillForRow(row) {
    if (row.labelOverride) {
        const state = row.state === 'failed' || row.state === 'stopped' ? row.state : 'completed';
        const parts = stepLabelParts('planning', state);
        if (parts && parts.full === row.labelOverride) {
            return pillFor({ kind: 'planning', subject: null, ordinal: null, of: null }, state);
        }
    }
    return pillFor({
        kind: row.kind,
        subject: row.subject,
        ordinal: row.ordinal,
        of: row.of,
    }, row.state);
}

function rowPhaseLabel(row) {
    if (!row) return null;
    if (row.labelOverride) return row.labelOverride;
    return labeledStep({
        kind: row.kind,
        subject: row.subject,
        ordinal: row.ordinal,
        of: row.of,
    }, row.state);
}

function displayRows(turn) {
    const steps = turn.timeline?.steps || [];
    return stepRows(steps, turn.timeline?.state, (stepId) => {
        const step = steps.find((item) => item.id === stepId);
        return Boolean(step && thoughtForStep(step, turn));
    });
}

function buildStep(row, turn) {
    const item = document.createElement('li');
    item.dataset.stepId = row.id;
    item.dataset.stepKind = row.kind;
    const host = hostStep(turn, row);
    const thought = row.thoughtStepId && host ? thoughtForStep(host, turn) : null;
    const effectiveState = thought && host ? effectiveStepState(host, thought) : row.state;
    item.className = `mcp-ask-v2__step mcp-ask-v2__step--enter mcp-ask-v2__step--${effectiveState}`;
    item.appendChild(stepState(effectiveState));
    if (thought && host) {
        item.classList.add('mcp-ask-v2__step--planning-has-thought');
        item.appendChild(buildThoughtDetails(host, thought, effectiveState));
        return item;
    }
    const pill = pillForRow(row);
    if (pill) item.appendChild(pill);
    const time = document.createElement('span');
    time.className = 'mcp-ask-v2__step-elapsed';
    time.textContent = rowDurationText(row, turn);
    item.append(time);
    return item;
}

function syncThoughtDetails(li, step, thought, state, durationText) {
    const details = li.querySelector('details.mcp-ask-v2__planning');
    if (!thought) {
        if (details) {
            details.remove();
            li.classList.remove('mcp-ask-v2__step--planning-has-thought');
            const pill = pillFor(step, state);
            if (pill) li.appendChild(pill);
            const time = document.createElement('span');
            time.className = 'mcp-ask-v2__step-elapsed';
            time.textContent = durationText;
            li.appendChild(time);
        }
        return;
    }

    li.classList.add('mcp-ask-v2__step--planning-has-thought');
    if (!details) {
        const pill = li.querySelector('.mcp-ask-v2__pill');
        if (pill) pill.remove();
        const time = li.querySelector('.mcp-ask-v2__step-elapsed');
        if (time) time.remove();
        li.appendChild(buildThoughtDetails(step, thought, state));
        return;
    }

    const captionEl = details.querySelector('.mcp-ask-v2__thought-caption');
    const nextCaption = thoughtCaption(thought, state);
    if (captionEl && captionEl.textContent !== nextCaption) {
        captionEl.textContent = nextCaption;
    }

    const pre = details.querySelector('.mcp-ask-v2__planning-body');
    if (pre && pre.textContent !== plainThought(thought.text)) {
        pre.textContent = plainThought(thought.text);
    }
    const shouldOpen = thoughtDetailsShouldOpen(step, thought);
    if (details.open !== shouldOpen) {
        step.detailsExpectedOpen = shouldOpen;
        details.open = shouldOpen;
    }
}

function patchStep(li, row, turn) {
    const host = hostStep(turn, row);
    const thought = row.thoughtStepId && host ? thoughtForStep(host, turn) : null;
    const effectiveState = thought && host ? effectiveStepState(host, thought) : row.state;
    const durationText = rowDurationText(row, turn);

    li.dataset.stepKind = row.kind;
    li.classList.remove('mcp-ask-v2__step--running', 'mcp-ask-v2__step--completed', 'mcp-ask-v2__step--failed', 'mcp-ask-v2__step--stopped');
    li.classList.add(`mcp-ask-v2__step--${effectiveState}`);

    const stateEl = li.querySelector('.mcp-ask-v2__step-state');
    if (stateEl) {
        const nextState = effectiveState === 'running' ? 'Running' : (effectiveState === 'failed' || effectiveState === 'stopped') ? 'Stopped' : 'Completed';
        if (stateEl.textContent !== nextState) {
            stateEl.textContent = nextState;
        }
    }

    syncThoughtDetails(li, host || row, thought, effectiveState, durationText);

    if (!thought) {
        const pill = li.querySelector('.mcp-ask-v2__pill');
        const nextPill = pillForRow(row);
        if (pill && nextPill) {
            if (pill.getAttribute('aria-label') !== nextPill.getAttribute('aria-label')) {
                pill.replaceWith(nextPill);
            }
        } else if (!pill && nextPill) {
            const timeEl = li.querySelector('.mcp-ask-v2__step-elapsed');
            if (timeEl) {
                li.insertBefore(nextPill, timeEl);
            } else {
                li.appendChild(nextPill);
            }
        }

        const timeEl = li.querySelector('.mcp-ask-v2__step-elapsed');
        if (timeEl && timeEl.textContent !== durationText) {
            timeEl.textContent = durationText;
        } else if (!timeEl) {
            const time = document.createElement('span');
            time.className = 'mcp-ask-v2__step-elapsed';
            time.textContent = durationText;
            li.appendChild(time);
        }
    }
}

function createBlock(turn) {
    return () => {
        const block = document.createElement('details');
        block.className = 'mcp-ask-v2__work';
        const summary = document.createElement('summary');
        summary.className = 'mcp-ask-v2__work-summary';
        summary.setAttribute('aria-live', 'polite');
        const list = document.createElement('ol');
        list.className = 'mcp-ask-v2__steps';
        list.setAttribute('aria-label', 'Steps taken');
        block.append(summary, list);
        setOpen(block, turn, turn.timeline.expectedOpen);
        if (turn.timeline.userToggled) block.dataset.userToggled = '1';
        // The toggle event also fires for this module's own writes, so the
        // reader is detected by intent: a state that differs from the last one written here.
        block.addEventListener('toggle', () => {
            if (!turn.timeline) return;
            if (block.open !== turn.timeline.expectedOpen) {
                turn.timeline.userToggled = true;
                turn.timeline.expectedOpen = block.open;
                block.dataset.userToggled = '1';
            }
        });
        return block;
    };
}

function setOpen(block, turn, open) {
    turn.timeline.expectedOpen = open;
    block.open = open;
}

function paintTurn(turn) {
    if (!turn.timeline && (!turn?.status || !TERMINAL_STATUSES.has(turn.status))) return;
    const t = timelineOf(turn);
    const block = mountInSlot(turn.msgId, 'work', MARKER, createBlock(turn));
    if (!block) return;
    const host = messageElement(turn.msgId);
    host?.classList.add(OWNER_CLASS);
    claimProgressOwnership(turn.msgId);
    // This block is the turn's only progress surface: remove the legacy status
    // badge here, and the owner class stops the loader rebuilding it. The CSS
    // hide on this class stays as cover for panel rebuilds between paints.
    host?.querySelector('.mcp-ask-ai__status-badge')?.remove();
    const summary = block.querySelector('summary');
    let markEl = summary.querySelector('.mcp-ask-v2__work-mark');
    let labelEl = summary.querySelector('.mcp-ask-v2__work-label');
    let caretEl = summary.querySelector('.mcp-ask-v2__work-caret');

    if (!markEl || !labelEl || !caretEl) {
        summary.innerHTML = '';
        markEl = mark(t.state);
        labelEl = document.createElement('span');
        labelEl.className = 'mcp-ask-v2__work-label';
        caretEl = document.createElement('span');
        caretEl.className = 'mcp-ask-v2__work-caret';
        caretEl.setAttribute('aria-hidden', 'true');
        summary.append(markEl, labelEl, caretEl);
        t.renderedState = t.state;
    } else if (t.renderedState !== t.state) {
        const newMark = mark(t.state);
        markEl.replaceWith(newMark);
        t.renderedState = t.state;
    }

    const ol = block.querySelector('ol');
    const rows = displayRows(turn);
    const runningRow = rows.find((row) => row.state === 'running');
    if (t.state === 'running') t.phaseLabel = rowPhaseLabel(runningRow);

    const nextHeaderText = headerLabel(t.state, elapsed(turn), t.state === 'running' ? t.phaseLabel : null, rows.length);
    if (labelEl.textContent !== nextHeaderText) {
        labelEl.textContent = nextHeaderText;
    }

    const existingMap = new Map();
    for (const child of Array.from(ol.children)) {
        const id = child.dataset.stepId;
        if (id) existingMap.set(id, child);
    }

    const validStepIds = new Set();
    rows.forEach((row, idx) => {
        validStepIds.add(row.id);
        let li = existingMap.get(row.id);
        if (li) {
            patchStep(li, row, turn);
        } else {
            li = buildStep(row, turn);
        }
        const currentAtIdx = ol.children[idx];
        if (currentAtIdx !== li) {
            ol.insertBefore(li, currentAtIdx || null);
        }
    });

    for (const child of Array.from(ol.children)) {
        if (!validStepIds.has(child.dataset.stepId)) {
            child.remove();
        }
    }

    // The header holds the only running spinner; drop any spinner row an
    // earlier paint left in the block.
    block.querySelector('.mcp-ask-v2__step-spinner')?.remove();

    if (t.closed && !t.userToggled && block.open) setOpen(block, turn, false);
    if (t.durationMs !== null) {
        const slot = host?.querySelector('.mcp-ask-ai__model');
        if (slot) slot.textContent = formatDuration(t.durationMs);
    }
}

function cacheReadsBlocked() {
    return typeof window !== 'undefined' && window.McpAskAiRestoreGate?.state?.() === 'checking';
}

function repaint() {
    if (cacheReadsBlocked()) return;
    turnStore.ensureHydrated();
    const thread = document.querySelector('.mcp-ask-ai__thread');
    const hasMessages = Boolean(thread?.querySelector('.mcp-ask-ai__msg--assistant'));
    if (hasMessages) {
        turnStore.prune((msgId) => Boolean(messageElement(msgId)));
    }
    turnStore.all().forEach(paintTurn);
    syncTicker();
}

// Whichever module pruned the turn, this module's timer is cleared here.
function onRemoved(turn) {
    if (turn.timeline?.closeTimer) clearTimeout(turn.timeline.closeTimer);
}

function syncTicker() {
    const running = turnStore.all().some((turn) => turn.timeline?.state === 'running');
    if (running && ticker === null) ticker = setInterval(() => turnStore.all().forEach(paintTurn), TICK_MS);
    if (!running && ticker !== null) {
        clearInterval(ticker);
        ticker = null;
    }
}

function onTurnStarted(event) {
    const { messageId, runId } = event.detail || {};
    const turn = turnStore.bind(messageId, runId);
    if (!turn) return;
    turn.startedAt = performance.now();
    turn.timeline = newTimeline();
    paintTurn(turn);
    syncTicker();
}

function onActivity(event) {
    const d = event.detail || {};
    const turn = turnStore.resolve(d.run_id, activeMessageId());
    if (!turn) return;
    const t = timelineOf(turn);
    if (!stepLabel(d.activity_kind, 'running')) return; // unknown keys are not rows
    let step = t.steps.find((s) => s.id === d.activity_id);
    if (!step) {
        step = { id: d.activity_id, kind: d.activity_kind };
        t.steps.push(step);
    }
    step.subject = d.subject;
    step.state = d.state || 'running';
    step.elapsedMs = d.elapsed_ms ?? 0;
    step.observedAt = performance.now();
    if (d.of != null) {
        step.ordinal = d.ordinal;
        step.of = d.of;
    }
    paintTurn(turn);
    syncTicker();
}

function thoughtBody(host, stepId) {
    if (!host || stepId == null) return null;
    for (const li of host.querySelectorAll('li[data-step-id]')) {
        if (li.dataset.stepId === stepId) return li.querySelector('.mcp-ask-v2__planning-body');
    }
    return null;
}

function onThoughtState(event) {
    const { run_id, activity_id } = event.detail || {};
    if (!run_id) return;
    const turn = turnStore.resolve(run_id, activeMessageId());
    if (!turn) return;
    const t = timelineOf(turn);
    const stepId = activity_id || bindUnaddressedThoughtStep(turn);
    const step = t.steps.find((s) => s.id === stepId);
    const thought = step ? thoughtForStep(step, turn) : null;
    if (!step || !thought) return;

    const host = messageElement(turn.msgId);
    const pre = thoughtBody(host, step.id);

    const previouslyFinished = step.thoughtFinished ?? false;
    const finishedFlipped = thought.finished !== previouslyFinished;

    // A streaming delta only rewrites its own row's text; a state flip repaints.
    if (pre && !finishedFlipped) {
        if (pre.textContent !== plainThought(thought.text)) {
            pre.textContent = plainThought(thought.text);
        }
        return;
    }

    step.thoughtFinished = thought.finished;
    paintTurn(turn);
}

function endTurn(turn, state, durationMs) {
    const t = timelineOf(turn);
    if (t.state !== 'running') return; // a second terminal frame changes nothing
    t.state = state;
    t.provisional = durationMs === null || durationMs === undefined;
    t.durationMs = t.provisional ? Math.max(1, Math.round(elapsed(turn))) : durationMs;
    for (const step of t.steps) {
        if (step.state === 'running') {
            step.elapsedMs = stepElapsed(step);
            step.state = (state === 'failed' || state === 'stopped') ? 'failed' : 'completed';
        }
    }
    paintTurn(turn);
    syncTicker();
    turnStore.save();
    if (CLOSES.has(state) && t.closeTimer === null) {
        t.closeTimer = setTimeout(() => {
            t.closeTimer = null;
            if (!t.userToggled) {
                t.closed = true;
                paintTurn(turn);
                turnStore.save();
            }
        }, AUTO_CLOSE_MS);
    }
}

const onTerminal = (event) => {
    const d = event.detail || {};
    const turn = turnStore.resolve(d.run_id, activeMessageId());
    if (turn) endTurn(turn, timelineState(d.status), d.duration_ms ?? null);
};

export const workTimelineModule = {
    name: 'activity_spine',
    mount(eventBus) {
        bus = eventBus;
        claimPanelProgress();
        window.addEventListener(TURN_STARTED, onTurnStarted);
        bus.addEventListener('activity', onActivity);
        bus.addEventListener('turn_terminal', onTerminal);
        bus.addEventListener('thought_state', onThoughtState);
        stopRenderWatch = onThreadRender(repaint);
        stopRemoveWatch = turnStore.onRemove(onRemoved);
        repaint();
    },

    unmount() {
        releasePanelProgress();
        window.removeEventListener(TURN_STARTED, onTurnStarted);
        bus?.removeEventListener('activity', onActivity);
        bus?.removeEventListener('turn_terminal', onTerminal);
        bus?.removeEventListener('thought_state', onThoughtState);
        stopRenderWatch?.();
        stopRenderWatch = null;
        stopRemoveWatch?.();
        stopRemoveWatch = null;
        if (ticker !== null) {
            clearInterval(ticker);
            ticker = null;
        }
        for (const turn of turnStore.all()) {
            if (turn.timeline?.closeTimer) clearTimeout(turn.timeline.closeTimer);
            turn.timeline = null;
        }
        bus = null;
    },
};
