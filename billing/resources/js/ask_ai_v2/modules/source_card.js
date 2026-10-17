/**
 * Source card module.
 *
 * A card shows the details of one document source beside its title: the
 * document title, the heading inside the document, and the passage text. The
 * viewer already holds these on the wire; the card fetches nothing.
 *
 * The card is a tooltip of the title (aria-describedby). It opens on hover,
 * on keyboard focus and on tap. Escape, a second tap, or a tap elsewhere
 * closes it. One card is open at a time.
 *
 * Every string is corpus text, so it goes in as a text node, never as markup.
 */

// Ingestion joins a chunk's headings into its section, the document's H1 first.
const SECTION_SEPARATOR = ' > ';
const CARD_BELOW = 'mcp-ask-v2__source-card--below';
const SCROLL_AREA = '.mcp-ask-ai__thread';

let cardCount = 0;
let openCard = null;
let documentListenersOn = false;

/** The heading path inside the document: the section without the document title. */
export function sectionHeading(section, title) {
    if (typeof section !== 'string') return '';
    const parts = section.split(SECTION_SEPARATOR).map((part) => part.trim()).filter(Boolean);
    if (parts[0] === title) parts.shift();
    return parts.join(SECTION_SEPARATOR);
}

function textNode(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
}

function buildCard({ title, heading, passage }) {
    cardCount += 1;
    const card = document.createElement('div');
    card.className = 'mcp-ask-v2__source-card';
    card.id = `mcp-ask-v2-source-card-${cardCount}`;
    card.setAttribute('role', 'tooltip');
    card.hidden = true;
    card.appendChild(textNode('strong', 'mcp-ask-v2__source-card-title', title));
    if (heading) card.appendChild(textNode('span', 'mcp-ask-v2__source-card-heading', heading));
    if (passage) card.appendChild(textNode('p', 'mcp-ask-v2__source-card-passage', passage));
    return card;
}

// The card opens on the side with more room inside the visible scroll area,
// and caps its height to that room when taller so the passage scrolls.
function place(item, card) {
    card.classList.remove(CARD_BELOW);
    card.style.maxHeight = '';
    card.style.overflowY = '';

    const area = item.closest(SCROLL_AREA);
    const areaRect = area ? area.getBoundingClientRect() : null;
    const itemRect = item.getBoundingClientRect();

    const areaTop = areaRect ? Math.max(areaRect.top, 0) : 0;
    const areaBottom = areaRect ? areaRect.bottom : (typeof window !== 'undefined' ? window.innerHeight : 0);

    const roomAbove = Math.max(0, itemRect.top - areaTop);
    const roomBelow = Math.max(0, areaBottom - itemRect.bottom);

    const openBelow = roomBelow > roomAbove;
    if (openBelow) {
        card.classList.add(CARD_BELOW);
    }

    const room = openBelow ? roomBelow : roomAbove;
    const cardHeight = card.getBoundingClientRect().height;
    if (cardHeight > room) {
        card.style.maxHeight = `${room}px`;
        card.style.overflowY = 'auto';
    }
}

// A thread render replaces the Sources list, so an open card can leave the
// page without a close; it then no longer counts as open.
function visibleOpenCard() {
    if (openCard && !openCard.item.isConnected) openCard = null;
    return openCard;
}

/**
 * Closes the open card when its row sits inside the given element. A section
 * that collapses uses this so a card opened inside it does not stay open.
 *
 * @param {Element} element the element that contains the row
 */
export function closeCardWithin(element) {
    if (!element) return;
    const current = visibleOpenCard();
    if (current && element.contains(current.item)) current.close();
}

// Escape and a tap elsewhere act on the open card wherever focus is, so a
// card opened by hover can be closed from the keyboard too. Escape stops at
// the card: it must not also close the panel.
function listenOnDocument() {
    if (documentListenersOn) return;
    documentListenersOn = true;
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !visibleOpenCard()) return;
        event.preventDefault();
        event.stopPropagation();
        openCard.close();
    }, true);
    document.addEventListener('pointerdown', (event) => {
        if (visibleOpenCard() && !openCard.item.contains(event.target)) openCard.close();
    }, true);
}

/**
 * Gives one source row its card.
 *
 * @param {HTMLElement} item the row; hovering anywhere on it keeps the card open
 * @param {HTMLElement} trigger the focusable title the card describes
 * @param {{title: string, heading: string, passage: string}} details
 * @returns {HTMLElement} the card
 */
export function attachSourceCard(item, trigger, details) {
    listenOnDocument();
    const card = buildCard(details);
    let pinned = false;
    const entry = { item, close };

    function close() {
        pinned = false;
        card.hidden = true;
        card.style.maxHeight = '';
        card.style.overflowY = '';
        if (openCard === entry) openCard = null;
    }

    function open() {
        if (openCard && openCard !== entry) openCard.close();
        openCard = entry;
        card.hidden = false;
        place(item, card);
    }

    // A touch device sends pointer enter and leave around every tap; the tap
    // itself arrives as a click.
    item.addEventListener('pointerenter', (event) => {
        if (event.pointerType !== 'touch') open();
    });
    item.addEventListener('pointerleave', (event) => {
        if (event.pointerType === 'touch' || pinned || document.activeElement === trigger) return;
        close();
    });
    trigger.addEventListener('focus', open);
    trigger.addEventListener('blur', () => {
        if (!pinned) close();
    });
    trigger.addEventListener('click', () => {
        if (pinned) {
            close();
            return;
        }
        open();
        pinned = true;
    });

    trigger.setAttribute('aria-describedby', card.id);
    item.appendChild(card);
    return card;
}
