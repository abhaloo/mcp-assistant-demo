/**
 * Source and Citation turn slice and module.
 *
 * Restores and renders server-authorized document sources and citations for Ask AI turns.
 * Reuses existing trust affordances and safe same-origin link helpers.
 * Never displays cached content before authorization succeeds (quarantine).
 * Never leaks raw SQL, tool names, schema fields, or internal IDs.
 */

import { activeMessageId, mountInSlot, onThreadRender, openInNewTab, slotElement } from './module_host.js';
import { turnStore, projectRestoredSources, projectRestoredCitations } from '../turn_state.js';
import { mcpIcon } from './icons.js';
import { attachSourceCard, closeCardWithin, sectionHeading } from './source_card.js';

const MARKER = 'data-mcp-ask-v2-sources';
// The panel's own source notice, painted straight into the references slot.
const PANEL_SOURCE_NOTE = ':scope > .mcp-ask-ai__citation-note';

const el = (tag, className, text) => {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text instanceof Node) n.appendChild(text);
    else if (text !== undefined) n.textContent = text;
    return n;
};

function isSafeOrigin(url) {
    if (typeof window !== 'undefined' && typeof window.McpAiCore?.isSameOriginAppPath === 'function') {
        return window.McpAiCore.isSameOriginAppPath(url);
    }
    return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//');
}

function cacheReadsBlocked() {
    return typeof window !== 'undefined' && window.McpAskAiRestoreGate?.state?.() === 'checking';
}

// The panel root advertises the source section as a data attribute, read at
// every paint. A missing root or attribute keeps today's always-open list.
function collapsibleSourcesEnabled() {
    const root = typeof document !== 'undefined'
        ? document.getElementById('mcp-ask-ai-root')
        : null;
    if (!root) return true;
    const value = root.dataset.collapsibleSources;
    return value === undefined ? true : value !== '0';
}

export function documentEvidenceForTurn(turn) {
    if (!turn || !Array.isArray(turn.sources)) return new Map();
    if (typeof window !== 'undefined' && typeof window.McpAskAiStreamUi?.documentEvidence === 'function') {
        try {
            return window.McpAskAiStreamUi.documentEvidence({ sources: turn.sources, citations: turn.citations });
        } catch (_) {}
    }
    const sourcesMap = new Map(turn.sources.map((s) => [String(s.id), s]));
    const evidence = new Map();
    (turn.citations?.cited || []).forEach((citation) => {
        const marker = String(citation.marker);
        const source = sourcesMap.get(String(citation.id));
        if (!source || evidence.has(marker)) return;
        evidence.set(marker, {
            marker,
            id: source.id,
            type: source.type || 'Document',
            title: source.title || 'Company document',
            url: source.url || null,
            snippet: source.snippet || null,
            section: source.section || null,
            links: Array.isArray(source.records) ? source.records : (Array.isArray(source.links) ? source.links : []),
        });
    });
    return evidence;
}

function renderCitationNote() {
    const aside = el('aside', 'mcp-ask-ai__citation-note');
    aside.setAttribute('role', 'note');
    aside.setAttribute('aria-label', 'Source link status');
    const icon = mcpIcon('circle-help', 'mcp-ask-ai__citation-icon');
    const span = el('span');
    span.appendChild(el('strong', '', 'Source links unavailable'));
    span.appendChild(document.createTextNode(' This answer used company documents, but individual claims could not be linked automatically.'));
    aside.append(icon, span);
    return aside;
}

// One row of the Sources list: the marker, the title, the heading inside the
// document, and a card with the passage. The title is the focusable element
// the card describes.
function sourceItem(source, marker) {
    const li = el('li', 'mcp-ask-v2__source-item');
    if (marker) li.appendChild(el('span', 'mcp-ask-v2__source-marker', `[${marker}]`));

    const title = source.title || 'Document';
    let trigger;
    if (source.url && isSafeOrigin(source.url)) {
        trigger = el('a', 'mcp-ask-v2__source-link', title);
        trigger.setAttribute('href', source.url);
        openInNewTab(trigger, title);
    } else {
        trigger = el('button', 'mcp-ask-v2__source-title', title);
        trigger.type = 'button';
    }
    li.appendChild(trigger);

    const heading = sectionHeading(source.section, title);
    if (heading) li.appendChild(el('span', 'mcp-ask-v2__source-heading', ` — ${heading}`));
    attachSourceCard(li, trigger, { title, heading, passage: source.snippet || '' });
    return li;
}

function renderSourceNodes(turn) {
    if (!turn.sources || !turn.sources.length) return [];

    if (turn.citations?.parsed === false) {
        const note = renderCitationNote();
        return note ? [note] : [];
    }

    const evidence = documentEvidenceForTurn(turn);
    const list = el('ul', 'mcp-ask-v2__sources-list');

    if (evidence.size > 0) {
        for (const [marker, item] of evidence.entries()) list.appendChild(sourceItem(item, marker));
    } else {
        // Fallback for document sources without explicit citation markers
        for (const source of turn.sources) list.appendChild(sourceItem(source, null));
    }

    if (!collapsibleSourcesEnabled()) {
        const wrap = el('div', 'mcp-ask-v2__sources');
        wrap.append(el('p', 'mcp-ask-v2__sources-label', 'Sources:'), list);
        return [wrap];
    }

    const details = el('details', 'mcp-ask-v2__sources mcp-ask-v2__sources--collapsible');
    details.appendChild(el('summary', 'mcp-ask-v2__sources-summary', `Sources (${list.childElementCount})`));
    details.appendChild(list);
    // Collapsing the section must not leave a card open inside it.
    details.addEventListener('toggle', () => {
        if (!details.open) closeCardWithin(details);
    });
    return [details];
}

function paintTurn(turn) {
    if (!turn) return;
    if (cacheReadsBlocked()) return;

    const host = mountInSlot(turn.msgId, 'references', MARKER, () => el('div', 'mcp-ask-v2__references'));
    if (!host) return;

    const nodes = renderSourceNodes(turn);
    if (!nodes || nodes.length === 0) {
        host.replaceChildren();
        return;
    }
    host.replaceChildren(...nodes);
    // This module owns the source display of a turn it holds sources for, so
    // the panel's notice for the same frame never shows beside it.
    slotElement(turn.msgId, 'references')?.querySelectorAll(PANEL_SOURCE_NOTE).forEach((note) => note.remove());
}

function repaint() {
    if (cacheReadsBlocked()) return;
    turnStore.ensureHydrated();
    for (const turn of turnStore.all()) {
        paintTurn(turn);
    }
}

let bus = null;
let stopRenderWatch = null;

function onCitationSet(event) {
    const d = event.detail || {};
    const turn = turnStore.resolve(d.run_id, activeMessageId());
    if (!turn) return;
    if (Array.isArray(d.sources)) {
        const sources = projectRestoredSources(d.sources);
        if (sources) turn.sources = sources;
    }
    const citations = projectRestoredCitations(d.citations);
    if (citations) turn.citations = citations;
    paintTurn(turn);
}

export const sourceRestoreSlice = {
    name: 'source_restore',
    restore(turn, payload) {
        const sources = projectRestoredSources(payload.sources);
        if (sources) turn.sources = sources;
        else delete turn.sources;
        const citations = projectRestoredCitations(payload.citations);
        if (citations) turn.citations = citations;
        else delete turn.citations;
    },
    clear(turn) {
        delete turn.sources;
        delete turn.citations;
    },
};

export const sourceRestoreModule = {
    name: 'source_restore',
    mount(eventBus) {
        bus = eventBus;
        bus?.addEventListener?.('citation_set', onCitationSet);
        stopRenderWatch = onThreadRender(repaint);
        repaint();
    },
    unmount() {
        bus?.removeEventListener?.('citation_set', onCitationSet);
        stopRenderWatch?.();
        stopRenderWatch = null;
        bus = null;
    },
};
