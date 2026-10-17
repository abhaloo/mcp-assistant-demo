/**
 * Words for the steps a turn reports.
 *
 * The wire names a step by a closed key. The key is right for a log and wrong
 * for a panel, where the reader wants to know what is happening, not which
 * component is doing it. Every string a person reads for a step lives here.
 */

const STEP_LABELS = {
    thinking: 'Thinking',
    planning: 'Planning the lookup',
    authorize: 'Checking your access',
    finding_record: 'Looking up the record',
    querying: 'Searching {subject}',
    searching_documents: 'Searching {subject}',
    sealing: 'Verifying results',
    answering: 'Writing answer',
};

const STEP_SUBJECTS = new Set(['jobs', 'invoices', 'customers', 'orders', 'customer orders', 'inventory']);
const WITH_SUBJECT = new Set(['querying', 'searching_documents']);

/**
 * Words for one step, or null for a key this file does not know. A key the
 * panel cannot name is not shown; the header falls back to Working.
 *
 * @param {string} kind - wire activity_kind
 * @param {'running'|'completed'|'failed'} state
 * @param {string} [subject] - optional step subject
 * @returns {string|null}
 */
export function stepLabel(kind, state, subject) {
    const template = STEP_LABELS[kind];
    if (!template) return null;
    const noun = WITH_SUBJECT.has(kind) && STEP_SUBJECTS.has(subject) ? subject : null;
    const label = noun ? template.replace('{subject}', noun) : template.replace('{subject}', kind === 'searching_documents' ? 'documents' : 'records');
    if (state === 'failed' || state === 'stopped') return `${label} — stopped`;
    return label;
}

/**
 * The pill parts for one step: the verb, the subject noun when the wire named
 * one, the ordinal of N when the step has one, the stopped note, and the whole
 * label for screen readers (the same text labeledStep builds in work_timeline).
 *
 * @param {string} kind
 * @param {'running'|'completed'|'failed'|'stopped'} state
 * @param {string} [subject]
 * @param {number|null} [ordinal]
 * @param {number|null} [of]
 * @returns {{verb: string, subject: string|null, ordinal: string|null, note: string|null, full: string}|null}
 */
export function stepLabelParts(kind, state, subject, ordinal = null, of = null) {
    const base = stepLabel(kind, state, subject);
    if (!base) return null;
    const template = STEP_LABELS[kind];
    const noun = WITH_SUBJECT.has(kind) && STEP_SUBJECTS.has(subject) ? subject : null;
    const verb = noun ? template.replace(' {subject}', '') : template.replace('{subject}', kind === 'searching_documents' ? 'documents' : 'records');
    const position = of == null ? null : `${ordinal + 1} of ${of}`;
    const note = state === 'failed' || state === 'stopped' ? '— stopped' : null;
    const full = position ? `${base} (${position})` : base;
    return { verb, subject: noun, ordinal: position, note, full };
}

export function formatDuration(ms) {
    return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// Whole seconds while running, one decimal once the receipt is in. A turn
// never reads as zero seconds long.
function seconds(ms, whole) {
    if (ms < 1000) {
        return whole ? '1 s' : 'under a second';
    }
    return whole ? `${Math.floor(ms / 1000)} s` : `${(ms / 1000).toFixed(1)} s`;
}

/**
 * A declined turn (an explained refusal) finished; it reads like a done one.
 *
 * @param {'running'|'completed'|'declined'|'clarification'|'failed'|'stopped'} state
 * @param {number} ms
 * @param {string|null} [phaseLabel=null]
 * @param {number} [stepCount=0]
 * @returns {string}
 */
export function headerLabel(state, ms, phaseLabel = null, stepCount = 0) {
    if (state === 'completed' || state === 'declined') {
        if (stepCount > 0) return `${stepCount} ${stepCount === 1 ? 'step' : 'steps'} · ${seconds(ms, false)}`;
        return `${state === 'declined' ? 'Finished' : 'Done'} in ${seconds(ms, false)}`;
    }
    if (state === 'clarification') return 'Needs your input';
    if (state === 'failed' || state === 'stopped') return `Stopped after ${seconds(ms, false)}`;
    return `${phaseLabel || 'Working'} · ${seconds(ms, true)}`;
}
