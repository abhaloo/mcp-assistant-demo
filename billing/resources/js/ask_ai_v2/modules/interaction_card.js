/**
 * Interaction card module.
 *
 * Two cards, one shape. A clarification card asks the reader to pick one
 * option (or type something else) and Continue. A follow-up card offers the
 * next questions the server can answer. Both dispatch by option id through the
 * panel's send hook; the words on a button are never what gets sent.
 *
 * While this module is on it owns these affordances for the active message,
 * so the panel's own chips and disambiguation pills are hidden there.
 */

import { activeMessageId, messageElement, mountInSlot, onThreadRender, openInNewTab, slotElement } from './module_host.js';
import { turnStore } from '../turn_state.js';
import { mcpIcon } from './icons.js';

const MARKER = 'data-mcp-ask-v2-card';
const FREE_TEXT = '__free_text';
const FREE_TEXT_MAX = 200;
const LEGACY_AFFORDANCES = '.mcp-ask-ai__chips, .mcp-ask-ai__disambiguation-card';

let bus = null;
let stopRenderWatch = null;
// The reply to a card runs as a new turn, so the card that sent it waits here
// and stops its indicator when the next run ends.
let awaitingReply = null;

function send(label, descriptor) {
    window.dispatchEvent(new CustomEvent('mcp:askai:send', { detail: { label, descriptor } }));
}

function replyDescriptor(ticket, choice, text) {
    const descriptor = {
        operation: 'clarification_reply',
        continuation_ref: ticket,
        context_mode: 'none',
    };
    if (choice === FREE_TEXT) {
        descriptor.clarification_free_text = text;
    } else {
        descriptor.clarification_choice_id = choice;
    }
    return descriptor;
}

// Every helper below takes the card it works on. Two cards can be open at
// once (turn 1's clarification, turn 2's follow-ups), so nothing here may
// read a module-level "current card".
function valid(entry) {
    if (!entry || entry.submitted || !entry.choice) return false;
    return entry.choice !== FREE_TEXT || Boolean(entry.text.trim());
}

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function createCard() {
    const node = el('section', 'mcp-card mcp-ask-v2__card');
    node.setAttribute('role', 'region');
    return node;
}

function optionCopy(label, detail) {
    const copy = el('span', 'mcp-ask-v2__option-copy');
    copy.appendChild(el('span', 'mcp-ask-v2__option-label', label));
    if (detail) copy.appendChild(el('span', 'mcp-ask-v2__option-detail', detail));
    return copy;
}

// The View anchor stays a sibling of the label: inside it the anchor would
// join the radio's accessible name and activating it would toggle the choice.
// The focus ring comes from the option's focus-within rule in the card stylesheet.
function optionLink(label, href) {
    if (typeof href !== 'string' || href === '' || window.McpAiCore?.isSameOriginAppPath?.(href) !== true) return null;
    const link = el('a', 'mcp-ask-v2__option-link', 'View');
    link.setAttribute('href', href);
    openInNewTab(link, `View ${label}`);
    return link;
}

function radio(entry, id, label, detail, href) {
    const row = el('div', 'mcp-ask-v2__option mcp-ask-v2__option--radio');
    const control = el('label');
    const input = el('input');
    input.type = 'radio';
    input.name = `mcp-ask-v2-choice-${entry.msgId || 'turn'}`;
    input.value = id;
    input.checked = entry.choice === id;
    input.disabled = entry.submitted;
    input.addEventListener('change', () => {
        entry.choice = id;
        paintCard(entry);
    });
    control.append(input, optionCopy(label, detail));
    row.appendChild(control);
    const link = optionLink(label, href);
    if (link) row.appendChild(link);
    return row;
}

// After Continue the card is the record of the choice: title, check, label.
function paintAnswered(entry, host) {
    host.classList.add('mcp-ask-v2__card--answered');
    const header = el('div', 'mcp-card__header');
    header.appendChild(el('h3', 'mcp-card__title', entry.prompt));
    const body = el('div', 'mcp-card__body mcp-ask-v2__answered');
    const chosen = entry.options.find((o) => o.id === entry.choice);
    body.append(mcpIcon('circle-check', 'mcp-ask-v2__answered-check'), el('strong', 'mcp-ask-v2__answered-label', chosen ? chosen.label : entry.text.trim()));
    host.replaceChildren(header, body);
}

function syncContinue(entry, host) {
    const button = host.querySelector('.mcp-ask-v2__continue');
    if (button) button.disabled = !valid(entry);
}

function paintClarification(entry, host) {
    host.classList.add('mcp-ask-v2__card--clarification');
    host.setAttribute('aria-label', 'Clarification');
    const answerSlot = slotElement(entry.msgId, 'answer');
    if (answerSlot) answerSlot.hidden = true;
    if (entry.submitted) {
        paintAnswered(entry, host);
        return;
    }
    const header = el('div', 'mcp-card__header');
    header.appendChild(el('h3', 'mcp-card__title', entry.prompt));
    const body = el('div', 'mcp-card__body');
    const group = el('fieldset', 'mcp-ask-v2__options');
    group.appendChild(el('legend', 'visually-hidden', 'Select one of the following'));
    for (const option of entry.options) group.appendChild(radio(entry, option.id, option.label, option.detail, option.href));
    if (entry.freeText) {
        group.appendChild(radio(entry, FREE_TEXT, 'Something else'));
        if (entry.choice === FREE_TEXT) {
            const box = el('input', 'form-control form-control-sm mcp-ask-v2__free-text');
            box.type = 'text';
            box.maxLength = FREE_TEXT_MAX;
            box.value = entry.text;
            box.disabled = entry.submitted;
            box.setAttribute('aria-label', 'Something else');
            box.addEventListener('input', () => {
                entry.text = box.value;
                syncContinue(entry, host);
            });
            group.appendChild(box);
        }
    }
    body.appendChild(group);
    const footer = el('div', 'mcp-ask-v2__card-footer');
    const button = el('button', 'mcp-btn mcp-btn--primary mcp-btn--sm mcp-ask-v2__continue', 'Continue');
    button.type = 'button';
    button.addEventListener('click', () => {
        if (!valid(entry)) return;
        const chosen = entry.options.find((o) => o.id === entry.choice);
        const label = chosen ? chosen.label : entry.text.trim();
        entry.submitted = true;
        entry.pending = true;
        awaitingReply = entry;
        paintCard(entry);
        send(label, replyDescriptor(entry.ticket, entry.choice, entry.text.trim()));
    });
    footer.appendChild(button);
    host.replaceChildren(header, body, footer);
    syncContinue(entry, host);
}

function paintFollowUp(entry, host) {
    host.classList.add('mcp-ask-v2__card--follow-up');
    host.setAttribute('aria-label', 'Next steps');
    const header = el('div', 'mcp-card__header');
    header.appendChild(el('h3', 'mcp-card__title', 'What would you like to do next?'));
    const body = el('div', 'mcp-card__body mcp-card__body--flush');
    const part = entry.unansweredPart || entry.unanswered_part;
    if (part) {
        body.appendChild(el('p', 'mcp-ask-v2__unanswered', `Not answered yet: ${part}`));
    }
    for (const option of entry.options) {
        const button = el('button', 'mcp-ask-v2__option');
        button.type = 'button';
        button.dataset.optionId = option.id;
        button.append(
            mcpIcon('search', 'mcp-ask-v2__option-icon'),
            optionCopy(option.label, option.detail),
            mcpIcon('arrow-right', 'mcp-ask-v2__option-arrow'),
        );
        // `option` is already the entry resolved by id; the button text is
        // never read back.
        button.addEventListener('click', () => send(option.prompt || option.label, null));
        body.appendChild(button);
    }
    host.replaceChildren(header, body);
}

function paintCard(entry) {
    const host = mountInSlot(entry.msgId, 'interaction', MARKER, createCard);
    if (!host) return;
    if (entry.kind === 'clarification') paintClarification(entry, host); else paintFollowUp(entry, host);
    if (entry.pending) {
        const pending = el('div', 'mcp-ask-v2__pending-indicator');
        pending.appendChild(mcpIcon('loader-circle', 'mcp-ask-v2__icon mcp-ask-v2__icon--spin'));
        host.appendChild(pending);
    }
    messageElement(entry.msgId)?.querySelectorAll(LEGACY_AFFORDANCES).forEach((node) => { node.hidden = true; });
}

function repaint() {
    turnStore.prune((id) => Boolean(messageElement(id)));
    for (const turn of turnStore.all()) {
        if (turn.interaction) paintCard(turn.interaction);
    }
}

function remember(entry, runId) {
    const turn = turnStore.resolve(runId, activeMessageId());
    if (!turn) return;
    entry.msgId = turn.msgId;
    turn.interaction = entry;
    paintCard(entry);
}

function onInteraction(event) {
    const d = event.detail || {};
    remember({
        kind: 'clarification',
        prompt: String(d.prompt || ''),
        ticket: d.continuation_ref,
        options: (d.options || []).map((o) => ({
            id: String(o.id),
            label: String(o.label),
            detail: o.detail == null || o.detail === '' ? null : String(o.detail),
            href: typeof o.href === 'string' && o.href !== '' ? o.href : null,
        })),
        freeText: Boolean(d.free_text_allowed),
        choice: null,
        text: '',
        submitted: false,
    }, d.run_id);
}

// The follow-up card for a terminal frame or a restore payload: both carry
// follow_ups and unanswered_part in one shape. Null when there is nothing to offer.
function followUpEntry(d) {
    const follow = (Array.isArray(d.follow_ups) ? d.follow_ups : [])
        .filter((f) => f && f.id && f.label && f.prompt)
        .map((f) => ({
            id: String(f.id),
            label: String(f.label),
            prompt: String(f.prompt),
            detail: f.detail == null || f.detail === '' ? null : String(f.detail),
        }));
    const part = typeof d.unanswered_part === 'string' && d.unanswered_part.trim() ? d.unanswered_part.trim() : null;
    if (!follow.length && !part) return null;
    return { kind: 'follow_up', options: follow, unansweredPart: part, unanswered_part: part };
}

function settleReply() {
    const entry = awaitingReply;
    if (!entry) return;
    awaitingReply = null;
    entry.pending = false;
    paintCard(entry);
}

// A clarification already has its card; every other outcome that carries follow-ups paints them, an explained failure included.
function onOutcome(event) {
    const d = event.detail || {};
    settleReply();
    if (d.outcome_type === 'clarification_required') return; // the interaction card is already up
    const entry = followUpEntry(d);
    if (entry) remember(entry, d.run_id);
}

// A reloaded turn gets back the next steps it offered, from its authorized
// restore payload only; the thread render then paints them like a live card.
export const interactionCardRestoreSlice = {
    name: 'interaction_card',
    restore(turn, payload) {
        const entry = followUpEntry(payload);
        if (!entry) return;
        entry.msgId = turn.msgId;
        entry.restored = true;
        turn.interaction = entry;
    },
    clear(turn) {
        if (turn.interaction?.restored) turn.interaction = null;
    },
};

// Registered where the card is defined, so every page that loads it restores its next steps.
turnStore.registerRestoreSlice(interactionCardRestoreSlice.name, interactionCardRestoreSlice);

function onTerminal() {
    settleReply();
}

export const interactionCardModule = {
    name: 'interaction_card',

    mount(eventBus) {
        bus = eventBus;
        bus.addEventListener('interaction', onInteraction);
        bus.addEventListener('turn_outcome', onOutcome);
        bus.addEventListener('turn_terminal', onTerminal);
        stopRenderWatch = onThreadRender(repaint);
    },

    unmount() {
        bus?.removeEventListener('interaction', onInteraction);
        bus?.removeEventListener('turn_outcome', onOutcome);
        bus?.removeEventListener('turn_terminal', onTerminal);
        stopRenderWatch?.();
        stopRenderWatch = null;
        for (const turn of turnStore.all()) turn.interaction = null;
        awaitingReply = null;
        bus = null;
    },
};

