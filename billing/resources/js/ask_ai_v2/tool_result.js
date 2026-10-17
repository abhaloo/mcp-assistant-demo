/**
 * Tool turn result parser and presentation adapter.
 *
 * Implements version parsing for version-1 tool results and citations.
 * Discriminated result kinds:
 * - 'legacy': tool_result_version absent, retains existing behavior without coercion
 * - 'v1': valid tool_result_version=1 payload with verified completeness, omissions, and components
 * - 'invalid': malformed or contradictory metadata, yields safe incomplete/untrusted display
 */

export const VALID_TOOLS = Object.freeze(new Set(['business_query', 'document_search']));

export const VALID_COMPLETENESS = Object.freeze(new Set(['full', 'partial', 'none']));

export const VALID_OMISSION_REASONS = Object.freeze(new Set([
    'policy_denied',
    'circuit_open',
    'capacity_exhausted',
    'retriever_unavailable',
    'stage_timeout',
    'invalid_provenance',
    'output_limit',
    'clarification_required',
    'unsupported',
    'incomplete',
    'shadowed',
]));

export const VALID_COMPONENT_STATUSES = Object.freeze(new Set([
    'succeeded',
    'denied',
    'unavailable',
    'timeout',
    'failed',
    'clarification_required',
    'unsupported',
    'incomplete',
    'shadowed',
]));

/**
 * @typedef {'business_query' | 'document_search'} ToolName
 *
 * @typedef {'policy_denied' | 'circuit_open' | 'capacity_exhausted' | 'retriever_unavailable' | 'stage_timeout' | 'invalid_provenance' | 'output_limit' | 'clarification_required' | 'unsupported' | 'incomplete' | 'shadowed'} ToolOmissionReason
 *
 * @typedef {'succeeded' | 'denied' | 'unavailable' | 'timeout' | 'failed' | 'clarification_required' | 'unsupported' | 'incomplete' | 'shadowed'} ComponentStatus
 *
 * @typedef {Object} ToolOmission
 * @property {ToolName} tool
 * @property {ToolOmissionReason} reason
 *
 * @typedef {Object} ComponentEvidence
 * @property {ToolName} tool
 * @property {string} invocation_id
 * @property {ComponentStatus} status
 * @property {string[]} [answer_query_ids]
 * @property {string | null} [evidence_digest]
 *
 * @typedef {Object} LegacyToolResult
 * @property {'legacy'} kind
 * @property {null} completeness
 * @property {ToolOmission[]} omissions
 * @property {ComponentEvidence[]} components
 * @property {null} restoreRef
 *
 * @typedef {Object} V1ToolResult
 * @property {'v1'} kind
 * @property {'full' | 'partial' | 'none'} completeness
 * @property {ToolOmission[]} omissions
 * @property {ComponentEvidence[]} components
 * @property {string | null} restoreRef
 *
 * @typedef {Object} InvalidToolResult
 * @property {'invalid'} kind
 * @property {null} completeness
 * @property {ToolOmission[]} omissions
 * @property {ComponentEvidence[]} components
 * @property {null} restoreRef
 *
 * @typedef {LegacyToolResult | V1ToolResult | InvalidToolResult} ProjectedToolResult
 */

const LEGACY_RESULT = Object.freeze({
    kind: 'legacy',
    completeness: null,
    omissions: Object.freeze([]),
    components: Object.freeze([]),
    restoreRef: null,
});

const INVALID_RESULT = Object.freeze({
    kind: 'invalid',
    completeness: null,
    omissions: Object.freeze([]),
    components: Object.freeze([]),
    restoreRef: null,
});

/**
 * Parse and project a tool turn result payload.
 *
 * @param {object|null|undefined} payload
 * @returns {ProjectedToolResult}
 */
export function projectToolResult(payload) {
    if (!payload || typeof payload !== 'object') {
        return LEGACY_RESULT;
    }

    if (!Object.hasOwn(payload, 'tool_result_version')) {
        return LEGACY_RESULT;
    }

    if (payload.tool_result_version !== 1 || !VALID_COMPLETENESS.has(payload.completeness)) {
        return INVALID_RESULT;
    }

    // Contradictory metadata: full cannot have omissions
    const rawOmissions = payload.omissions;
    const hasOmissions = Array.isArray(rawOmissions) && rawOmissions.length > 0;
    if (payload.completeness === 'full' && hasOmissions) {
        return INVALID_RESULT;
    }

    // Contradictory metadata: partial/none cannot be trusted
    if (payload.completeness !== 'full' && payload.trusted === true) {
        return INVALID_RESULT;
    }

    // Partial turn must have explicit omissions
    if (payload.completeness === 'partial' && (!Array.isArray(rawOmissions) || rawOmissions.length === 0)) {
        return INVALID_RESULT;
    }

    // Validate omissions array
    const normalizedOmissions = [];
    if (rawOmissions !== undefined && rawOmissions !== null) {
        if (!Array.isArray(rawOmissions)) {
            return INVALID_RESULT;
        }
        for (const omission of rawOmissions) {
            if (!omission || typeof omission !== 'object') {
                return INVALID_RESULT;
            }
            if (!VALID_TOOLS.has(omission.tool) || !VALID_OMISSION_REASONS.has(omission.reason)) {
                return INVALID_RESULT;
            }
            normalizedOmissions.push({
                tool: omission.tool,
                reason: omission.reason,
            });
        }
    }

    // Validate components array
    const normalizedComponents = [];
    const rawComponents = payload.components;
    if (rawComponents !== undefined && rawComponents !== null) {
        if (!Array.isArray(rawComponents)) {
            return INVALID_RESULT;
        }
        for (const component of rawComponents) {
            if (!component || typeof component !== 'object') {
                return INVALID_RESULT;
            }
            if (!VALID_TOOLS.has(component.tool)) {
                return INVALID_RESULT;
            }
            if (typeof component.invocation_id !== 'string' || !component.invocation_id.trim() || component.invocation_id.length > 64) {
                return INVALID_RESULT;
            }
            if (!VALID_COMPONENT_STATUSES.has(component.status)) {
                return INVALID_RESULT;
            }

            const answerQueryIds = component.answer_query_ids;
            if (answerQueryIds !== undefined && answerQueryIds !== null) {
                if (!Array.isArray(answerQueryIds) || !answerQueryIds.every((id) => typeof id === 'string')) {
                    return INVALID_RESULT;
                }
            }

            const evidenceDigest = component.evidence_digest;
            if (evidenceDigest !== undefined && evidenceDigest !== null && typeof evidenceDigest !== 'string') {
                return INVALID_RESULT;
            }

            normalizedComponents.push({
                tool: component.tool,
                invocation_id: component.invocation_id,
                status: component.status,
                answer_query_ids: Array.isArray(answerQueryIds) ? [...answerQueryIds] : [],
                evidence_digest: typeof evidenceDigest === 'string' ? evidenceDigest : null,
            });
        }
    }

    // Validate restore_ref
    let restoreRef = null;
    const rawRestoreRef = Object.hasOwn(payload, 'restore_ref') ? payload.restore_ref : payload.restoreRef;
    if (rawRestoreRef !== undefined && rawRestoreRef !== null) {
        if (typeof rawRestoreRef !== 'string') {
            return INVALID_RESULT;
        }
        restoreRef = rawRestoreRef;
    }

    return {
        kind: 'v1',
        completeness: payload.completeness,
        omissions: normalizedOmissions,
        components: normalizedComponents,
        restoreRef,
    };
}

/**
 * Projects a citation_set into ordered canonical panel events: sources then citations.
 *
 * @param {object|null|undefined} payload
 * @returns {Array<{event: string, payload: object}>}
 */
export function projectCitationSet(payload) {
    if (!payload || typeof payload !== 'object') {
        return [
            { event: 'sources', payload: [] },
            { event: 'citations', payload: {} },
        ];
    }

    const sources = Array.isArray(payload.sources) ? payload.sources : [];
    const citations = payload.citations && typeof payload.citations === 'object' ? payload.citations : {};

    return [
        { event: 'sources', payload: sources },
        { event: 'citations', payload: citations },
    ];
}

const outcomeEnvelopes = (outcome) => {
    const wire = outcome?.business_query;
    if (Array.isArray(wire?.envelopes) && wire.envelopes.length > 0) return wire.envelopes;
    if (wire?.envelope) return [wire.envelope];
    return [];
};

/**
 * Projects a turn outcome's record refs into the legacy sql_provenance event
 * the panel already paints. Refs without an href or a non-blank label are
 * skipped and one link per url survives (joined rows repeat refs). The event
 * is emitted only when a link survives, and always ahead of the terminal so
 * the panel links before the terminal render.
 *
 * @param {object|null|undefined} outcome
 * @returns {Array<{event: string, payload: object}>}
 */
export function projectRecordLinks(outcome) {
    const links = [];
    const seenUrls = new Set();
    for (const envelope of outcomeEnvelopes(outcome)) {
        const refs = Array.isArray(envelope?.record_refs) ? envelope.record_refs : [];
        for (const ref of refs) {
            if (!ref || typeof ref.href !== 'string' || ref.href === '') continue;
            if (typeof ref.label !== 'string' || ref.label.trim() === '') continue;
            if (seenUrls.has(ref.href)) continue;
            seenUrls.add(ref.href);
            links.push({
                table: ref.resource,
                record_id: ref.record_id,
                label: ref.label,
                url: ref.href,
                preview: ref.preview ?? null,
            });
        }
    }
    if (links.length === 0) return [];
    return [{ event: 'sql_provenance', payload: { queries: [], record_links: links } }];
}

/**
 * Page and action links the RAG service resolved for this viewer. Emitted ahead of
 * the terminal, like record links; nothing when the outcome carries none.
 *
 * @param {object|null|undefined} outcome
 * @returns {Array<{event: string, payload: object}>}
 */
export function projectUiLinks(outcome) {
    const raw = Array.isArray(outcome?.ui_links) ? outcome.ui_links : [];
    const links = raw
        .filter((l) => l && typeof l.href === 'string' && typeof l.label === 'string' && l.label.trim() !== '')
        .map((l) => ({ key: l.key, kind: l.kind, label: l.label, url: l.href }));
    return links.length ? [{ event: 'ui_links', payload: { ui_links: links } }] : [];
}
