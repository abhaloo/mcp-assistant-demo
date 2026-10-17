/**
 * Shared attachment rules for Ask AI modules.
 *
 * The panel owns the thread and rebuilds it from scratch on every render, so a
 * module that paints once loses its work the next time the panel renders. Every
 * module therefore keeps its own state and repaints from it, and this file holds
 * the two things they all need: where to paint, and when.
 */

const THREAD = '.mcp-ask-ai__thread';
const ASSISTANT_MESSAGE = '.mcp-ask-ai__msg--assistant';
const RENDER_EVENT = 'mcp:askai:thread-rendered';
export const PROGRESS_OWNER_ATTR = 'data-progress-owner';
const MODULE_PROGRESS_OWNER = 'module';

function escapeAttributeValue(value) {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
        return CSS.escape(value);
    }
    return String(value).replace(/["\\]/g, '\\$&');
}

/**
 * The assistant message a module should paint into: the newest one, which is
 * the turn in progress while streaming and the turn just finished after it.
 *
 * @returns {HTMLElement|null}
 */
/**
 * Marks an anchor as leaving the panel: the answer and its thread stay put
 * while the record opens beside them. `label` names the destination for a
 * reader who cannot see the link's surroundings.
 */
export function openInNewTab(anchor, label) {
    anchor.setAttribute('target', '_blank');
    anchor.setAttribute('rel', 'noopener');
    anchor.setAttribute('aria-label', `${label ? `${label} ` : ''}(opens in a new tab)`);
    return anchor;
}

export function activeAssistantMessage() {
    const messages = document.querySelector(THREAD)?.querySelectorAll(ASSISTANT_MESSAGE);
    return messages?.[messages.length - 1] || null;
}

/**
 * Finds this module's own element inside the active message, or creates and
 * attaches it. Returning the existing element keeps a repaint idempotent --
 * calling this on every render must not stack duplicates.
 *
 * @param {string} marker - data attribute identifying the owning module
 * @param {() => HTMLElement} create - builds the element when it is absent
 * @param {"prepend"|"append"} position - where it sits in the message
 * @returns {HTMLElement|null} null when there is no message to paint into
 */
export function mountPoint(marker, create, position = 'append') {
    return mountPointIn(null, marker, create, position);
}

/**
 * Calls `repaint` whenever the panel rebuilds the thread. Returns the
 * unsubscribe function, which a module's `unmount` must call.
 *
 * @param {() => void} repaint
 * @returns {() => void}
 */
export function onThreadRender(repaint) {
    window.addEventListener(RENDER_EVENT, repaint);
    return () => window.removeEventListener(RENDER_EVENT, repaint);
}

export function activeMessageId() {
    return activeAssistantMessage()?.dataset.msgId || null;
}

/** Marks the message so the panel skips legacy progress chrome for this turn. */
export function claimProgressOwnership(msgId) {
    messageElement(msgId)?.setAttribute(PROGRESS_OWNER_ATTR, MODULE_PROGRESS_OWNER);
}

/** True when a module owns the turn's progress display for this message. */
export function messageOwnsModuleProgress(target) {
    const host = typeof target === 'string' ? messageElement(target) : target;
    return host?.getAttribute(PROGRESS_OWNER_ATTR) === MODULE_PROGRESS_OWNER;
}

export function messageElement(msgId) {
    if (!msgId) {
        return activeAssistantMessage();
    }
    const escaped = escapeAttributeValue(msgId);
    return document.querySelector(`${THREAD} ${ASSISTANT_MESSAGE}[data-msg-id="${escaped}"]`);
}

const PREPEND_SLOTS = new Set(['work']);

export function slotElement(msgId, slot) {
    return messageElement(msgId)?.querySelector(`[data-slot="${slot}"]`) || null;
}

/**
 * Finds this module's element inside the named slot of a message, or creates
 * it there. A message from the module-off panel has no slots; the element then
 * goes where the reading order expects it (work first, everything else above
 * the legacy actions row, or last when there is none).
 */
export function mountInSlot(msgId, slot, marker, create) {
    const host = messageElement(msgId);
    if (!host) {
        return null;
    }
    let markerAttr = marker;
    let getElement = create;
    if (typeof marker !== 'string') {
        markerAttr = `data-mcp-ask-v2-${slot}`;
        getElement = marker;
    }
    const existing = host.querySelector(`[${markerAttr}]`);
    if (existing) {
        return existing;
    }
    const element = typeof getElement === 'function' ? getElement() : getElement;
    if (!element) {
        return null;
    }
    element.setAttribute(markerAttr, '');
    const target = host.querySelector(`[data-slot="${slot}"]`);
    if (target) {
        target.appendChild(element);
    } else if (PREPEND_SLOTS.has(slot)) {
        host.prepend(element);
    } else {
        const actions = host.querySelector(':scope > .mcp-ask-ai__actions');
        if (actions) {
            host.insertBefore(element, actions);
        } else {
            host.appendChild(element);
        }
    }
    return element;
}


export function mountPointIn(msgId, marker, create, position = 'append') {
    const host = messageElement(msgId);
    if (!host) {
        return null;
    }
    const existing = host.querySelector(`[${marker}]`);
    if (existing) {
        return existing;
    }
    const element = create();
    element.setAttribute(marker, '');
    host[position === 'prepend' ? 'prepend' : 'append'](element);
    return element;
}

