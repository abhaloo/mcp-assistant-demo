/**
 * Display rows for one turn.
 *
 * The wire steps stay in arrival order. This module only decides which of
 * them share a row: a planning/access run folds into the next record or
 * table step, an open run reads as a search while the turn is still going,
 * and two adjacent rows with the same words become one.
 */

import { stepLabel } from './step_labels.js';

const LOOKUP_PREFIX = new Set(['planning', 'authorize']);
const DATA_KINDS = new Set(['finding_record', 'querying']);

function hasLabel(step) {
    return stepLabel(step.kind, step.state, step.subject) !== null;
}

function storedMs(step) {
    return Number.isFinite(step.elapsedMs) ? step.elapsedMs : 0;
}

function sumStored(steps) {
    return steps.reduce((total, step) => total + storedMs(step), 0);
}

function runningMemberId(steps) {
    const running = steps.find((step) => step.state === 'running');
    return running ? running.id : null;
}

function memberIds(steps) {
    return steps.map((step) => step.id);
}

/**
 * Words a person reads for a row, including the table position and the
 * stopped note. A thought row has no pill, so it has no words to compare.
 *
 * @param {object} row
 * @returns {string|null}
 */
function fullLabel(row) {
    if (row.thoughtStepId) return null;
    if (row.labelOverride) return row.labelOverride;
    const base = stepLabel(row.kind, row.state, row.subject ?? undefined);
    if (!base) return null;
    if (row.of == null) return base;
    return `${base} (${Number(row.ordinal) + 1} of ${row.of})`;
}

function ownRow(step, thought) {
    return {
        id: step.id,
        kind: step.kind,
        state: step.state,
        subject: step.subject ?? null,
        ordinal: step.ordinal ?? null,
        of: step.of ?? null,
        storedElapsedMs: storedMs(step),
        runningMemberId: step.state === 'running' ? step.id : null,
        thoughtStepId: thought ? step.id : null,
        memberIds: [step.id],
        labelOverride: null,
    };
}

function dataRow(prefix, data) {
    const members = [...prefix, data];
    return {
        id: prefix[0].id,
        kind: data.kind,
        state: data.state,
        subject: data.subject ?? null,
        ordinal: data.ordinal ?? null,
        of: data.of ?? null,
        storedElapsedMs: sumStored(members),
        runningMemberId: runningMemberId(members),
        thoughtStepId: null,
        memberIds: memberIds(members),
        labelOverride: null,
    };
}

function openRow(members, turnState) {
    const first = members[0];
    const last = members[members.length - 1];
    if (turnState === 'running') {
        return {
            id: first.id,
            kind: 'querying',
            state: 'running',
            subject: null,
            ordinal: null,
            of: null,
            storedElapsedMs: sumStored(members),
            runningMemberId: runningMemberId(members),
            thoughtStepId: null,
            memberIds: memberIds(members),
            labelOverride: null,
        };
    }
    const stopped = last.state === 'failed' || last.state === 'stopped';
    const state = stopped ? last.state : 'completed';
    return {
        id: first.id,
        kind: 'planning',
        state,
        subject: null,
        ordinal: null,
        of: null,
        storedElapsedMs: sumStored(members),
        runningMemberId: null,
        thoughtStepId: null,
        memberIds: memberIds(members),
        labelOverride: stepLabel('planning', state),
    };
}

function mergeRepeats(rows) {
    const merged = [];
    for (const row of rows) {
        const previous = merged[merged.length - 1];
        const label = fullLabel(row);
        if (previous && label && fullLabel(previous) === label) {
            previous.state = row.state;
            previous.kind = row.kind;
            previous.subject = row.subject;
            previous.ordinal = row.ordinal;
            previous.of = row.of;
            previous.storedElapsedMs += row.storedElapsedMs;
            previous.memberIds = previous.memberIds.concat(row.memberIds);
            previous.runningMemberId = row.runningMemberId;
            previous.labelOverride = row.labelOverride;
            continue;
        }
        merged.push({ ...row, memberIds: row.memberIds.slice() });
    }
    return merged;
}

/**
 * Rows for display. `steps` are the timeline steps in arrival order.
 * `hasThought(stepId)` is true when that step carries a transcript.
 * The module never sees the turn.
 *
 * @param {Array<object>} steps
 * @param {string} turnState
 * @param {(stepId: string) => boolean} hasThought
 * @returns {Array<object>}
 */
export function stepRows(steps, turnState, hasThought) {
    const thoughtOn = typeof hasThought === 'function' ? hasThought : () => false;
    const visible = (Array.isArray(steps) ? steps : []).filter(hasLabel);
    const folded = [];
    let buffer = [];

    const flushOwn = () => {
        for (const step of buffer) folded.push(ownRow(step, false));
        buffer = [];
    };

    for (const step of visible) {
        const planningThought = step.kind === 'planning' && thoughtOn(step.id);
        if (LOOKUP_PREFIX.has(step.kind) && !planningThought) {
            buffer.push(step);
            continue;
        }
        if (planningThought) {
            flushOwn();
            folded.push(ownRow(step, true));
            continue;
        }
        if (DATA_KINDS.has(step.kind) && buffer.length) {
            folded.push(dataRow(buffer, step));
            buffer = [];
            continue;
        }
        if (buffer.length) flushOwn();
        folded.push(ownRow(step, thoughtOn(step.id)));
    }
    if (buffer.length) folded.push(openRow(buffer, turnState));

    return mergeRepeats(folded);
}
