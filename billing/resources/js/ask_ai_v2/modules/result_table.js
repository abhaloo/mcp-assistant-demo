/**
 * Result block: the server's receipt over the typed rows a turn streamed.
 *
 * One block per turn, painted into the message's result slot from that turn's
 * own state. The title and filter facts arrive on table_start; the counts are
 * executor facts from table_end and the sealed envelope. Nothing is parsed
 * from the answer text.
 */

import { activeMessageId, messageElement, mountInSlot, onThreadRender, openInNewTab } from './module_host.js';
import { turnStore } from '../turn_state.js';

const MARKER = 'data-mcp-ask-v2-result';
const FALLBACK_TITLE = 'Results';
const NO_MATCH = 'No matching records';
const NUMBER_TYPES = new Set(['decimal', 'integer', 'currency', 'number', 'percent']);
const ISO_DATE = /^\d{4}-\d{2}-\d{2}(?:[T ]|$)/;
const NUMERIC_STRING = /^-?\d+(\.\d+)?$/;

let bus = null;
let stopRenderWatch = null;

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
const el = (tag, className, text) => {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text instanceof Node) n.appendChild(text);
    else if (text !== undefined) n.textContent = text;
    return n;
};
const groupInt = (n) => Number(n).toLocaleString('en-GB');
const money = (n) => Number(n).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// A stored ratio reads as a percentage with one decimal: 0.0349949 -> 3.5%.
const percent = (n) => `${(Number(n) * 100).toFixed(1)}%`;
const isNumeric = (value) => (typeof value === 'number' ? Number.isFinite(value) : typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value)));
const changeSign = (n) => (Number(n) < 0 ? '−' : Number(n) > 0 ? '+' : '');
const CHANGE_ROLES = new Set(['delta', 'delta_pct']);

function formatDate(value) {
    const custom = window.McpFormat?.date;
    if (typeof custom === 'function') return custom(value);
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(d);
}

function fillTemplate(template, row) {
    const missing = [];
    const href = template.replace(/\{([^}]+)\}/g, (_, key) => {
        if (row[key] == null || row[key] === '') { missing.push(key); return ''; }
        return encodeURIComponent(String(row[key]));
    });
    if (missing.length) return null;
    const safe = window.McpAiCore?.isSameOriginAppPath?.(href);
    return safe ? href : null;
}

// The wire types every cell; a type this table does not know renders raw.
function formatCell(value, column, row = {}) {
    if (value === null || value === undefined) return column?.role ? '—' : '';
    const type = column?.value_type;
    const role = column?.role;
    // A change keeps the base measure's shape: a count moves by whole invoices,
    // an amount by money; the sign is always spelled out.
    if (role === 'delta_pct' && isNumeric(value)) {
        return `${changeSign(value)}${(Math.abs(value) * 100).toFixed(1)}%`;
    }
    if (role === 'delta' && isNumeric(value)) {
        const magnitude = type === 'integer' || type === 'number' ? groupInt(Math.abs(value))
            : type === 'percent' ? percent(Math.abs(value))
                : money(Math.abs(value));
        const suffix = column.currency_key && row[column.currency_key] ? ` ${row[column.currency_key]}` : '';
        return `${changeSign(value)}${magnitude}${suffix}`;
    }
    if (type === 'id') {
        // A declared look rides the row: show it instead of the key's own value.
        const look = column?.display_key ? row[column.display_key] : null;
        return String(look === null || look === undefined || look === '' ? value : look);
    }
    if (type === 'boolean') return value === true || value === 'true' ? 'Yes' : 'No';
    if (type === 'date' || type === 'datetime') return formatDate(value);
    if (!isNumeric(value)) return String(value);
    if (type === 'integer' || type === 'number') return groupInt(value);
    if (type === 'percent') return percent(value);
    if (type === 'decimal') return typeof value === 'number' && Number.isInteger(value) ? groupInt(value) : money(value);
    if (type === 'currency') {
        const code = column.currency_key ? row[column.currency_key] : null;
        return code ? `${money(value)} ${code}` : money(value);
    }
    return String(value);
}

function renderCellContent(value, column, row = {}) {
    const formatted = formatCell(value, column, row);
    if (column?.href_template && row) {
        const href = fillTemplate(column.href_template, row);
        if (href) {
            const a = el('a', 'mcp-ask-v2__record-link', formatted);
            a.setAttribute('href', href);
            openInNewTab(a, formatted);
            return a;
        }
    }
    return formatted;
}

const cellClass = (column) => {
    const classes = [];
    if (NUMBER_TYPES.has(column.value_type)) classes.push('mcp-ask-v2__cell--number');
    else if (column.value_type === 'id') classes.push('mcp-ask-v2__cell--id');
    if (column.role) classes.push(`mcp-ask-v2__cell--${column.role}`);
    return classes.join(' ');
};

// A change cell says which way it moved so the stylesheet can colour it.
function changeClass(value, column) {
    if (!CHANGE_ROLES.has(column?.role) || !isNumeric(value)) return '';
    const sign = changeSign(value);
    return sign === '+' ? 'mcp-ask-v2__cell--up' : sign === '−' ? 'mcp-ask-v2__cell--down' : '';
}

function receiptLine(result) {
    const parts = [];
    if (!result.aggregate && result.returned !== null) {
        if (result.returned === 0) parts.push(NO_MATCH);
        else if (result.total !== null && result.total > result.returned) parts.push(`${groupInt(result.returned)} of ${groupInt(result.total)} shown`);
        else parts.push(`${groupInt(result.returned)} shown`);
    }
    parts.push(...(result.presentation?.applied_filters || []));
    return parts.join(' · ');
}

function receipt(result) {
    const p = el('p', 'mcp-ask-v2__receipt');
    p.append(el('strong', 'mcp-ask-v2__receipt-title', result.presentation?.title || FALLBACK_TITLE));
    // A derived-set restriction (ADR 0073) or a comparison's omission notice
    // renders here, under the title: the prose frame carrying the same sentence
    // is dropped as table_fallback once a table paints, so the receipt is the
    // only place left to show it.
    if (result.presentation?.scope) p.append(el('span', 'mcp-ask-v2__receipt-scope', result.presentation.scope));
    const line = receiptLine(result);
    if (line) p.append(el('span', 'mcp-ask-v2__receipt-line', line));
    if (result.presentation?.summary) p.append(el('span', 'mcp-ask-v2__receipt-summary', result.presentation.summary));
    return p;
}

// A column projected only to serve another column's look is never painted.
const paintedColumns = (result) => result.columns.filter((c) => c.role !== 'display');

function table(result) {
    const wrap = el('div', 'mcp-ask-v2__table-wrap');
    const t = el('table', 'mcp-ask-v2__table');
    const head = el('thead'); const hr = el('tr');
    for (const column of paintedColumns(result)) { const th = el('th', cellClass(column), column.label); th.scope = 'col'; hr.appendChild(th); }
    head.appendChild(hr);
    const body = el('tbody');
    const animate = !reducedMotion();
    // Only rows that arrived with the current batch animate in; the renderer
    // reads that boundary and never writes it, so a repaint is byte-identical.
    result.rows.forEach((row, index) => {
        const tr = el('tr', animate && index >= result.enterFrom ? 'mcp-ask-v2__row--enter' : '');
        for (const column of paintedColumns(result)) tr.appendChild(el('td', [cellClass(column), changeClass(row[column.key], column)].filter(Boolean).join(' '), renderCellContent(row[column.key], column, row)));
        body.appendChild(tr);
    });
    if (result.streaming) {
        const skeleton = el('tr', 'mcp-ask-v2__skeleton-row'); skeleton.setAttribute('aria-hidden', 'true');
        paintedColumns(result).forEach(() => skeleton.appendChild(el('td')));
        body.appendChild(skeleton);
    }
    t.append(head, body); wrap.appendChild(t);
    return wrap;
}

// One row, one numeric column, and every other column is that column's
// currency code: the result is a key figure and the code folds into it.
function keyFigureColumn(result) {
    if (result.streaming || result.rows.length !== 1) return null;
    if (result.columns.length === 1) return result.columns[0];
    const numeric = result.columns.filter((c) => NUMBER_TYPES.has(c.value_type));
    if (numeric.length !== 1) return null;
    const [figure] = numeric;
    const others = result.columns.filter((c) => c !== figure);
    return others.every((c) => c.key === figure.currency_key) ? figure : null;
}

function aggregate(result, column) {
    const p = el('p', 'mcp-ask-v2__aggregate');
    p.appendChild(el('strong', 'mcp-ask-v2__aggregate-value', renderCellContent(result.rows[0][column.key], column, result.rows[0])));
    return p;
}

function resultMap(turn) {
    if (!(turn.result instanceof Map)) turn.result = new Map();
    return turn.result;
}

function sliceFor(turn, d) {
    const map = turn.result;
    if (!(map instanceof Map)) return null;
    if (typeof d.table_id === 'string' && d.table_id) return map.get(d.table_id) || null;
    if (map.size === 1) return map.values().next().value;
    return null;
}

function sectionNodes(result) {
    const figure = keyFigureColumn(result);
    result.aggregate = figure !== null;
    const painted = [receipt(result)];
    if (figure) painted.push(aggregate(result, figure));
    else if (result.rows.length || result.streaming) painted.push(table(result));
    return painted;
}

function paintTurn(turn) {
    if (cacheReadsBlocked()) return;
    const map = turn.result;
    if (!(map instanceof Map) || map.size === 0) {
        const existing = messageElement(turn.msgId)?.querySelector(`[${MARKER}]`);
        if (existing) existing.replaceChildren();
        return;
    }
    const host = mountInSlot(turn.msgId, 'result', MARKER, () => el('div', 'mcp-ask-v2__result'));
    if (!host) return;
    const painted = [];
    for (const result of map.values()) painted.push(...sectionNodes(result));
    host.replaceChildren(...painted);
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
}

function resultOf(d) {
    const turn = turnStore.resolve(d.run_id, activeMessageId());
    return turn ? turn : null;
}

function onTableStart(event) {
    const d = event.detail || {};
    const turn = resultOf(d);
    if (!turn) return;
    const id = typeof d.table_id === 'string' && d.table_id ? d.table_id : '__default__';
    const map = resultMap(turn);
    if (map.has(id)) return;
    map.set(id, {
        columns: Array.isArray(d.columns) ? d.columns : [],
        presentation: d.presentation && typeof d.presentation.title === 'string' ? d.presentation : null,
        rows: [], returned: null, total: null, streaming: true, enterFrom: 0, aggregate: false,
    });
    paintTurn(turn);
}

function onTableRows(event) {
    const d = event.detail || {};
    const turn = resultOf(d);
    if (!turn) return;
    const r = sliceFor(turn, d);
    if (!r) return;
    r.enterFrom = r.rows.length;
    r.rows = r.rows.concat(Array.isArray(d.rows) ? d.rows : []);
    paintTurn(turn);
    r.enterFrom = r.rows.length;
}

function onTableEnd(event) {
    const d = event.detail || {};
    const turn = resultOf(d);
    if (!turn) return;
    const r = sliceFor(turn, d);
    if (!r) return;
    r.streaming = false;
    r.returned = Number.isInteger(d.row_count) ? d.row_count : r.rows.length;
    if (Number.isInteger(d.total_row_count)) r.total = d.total_row_count;
    paintTurn(turn);
    turnStore.save();
}

function onOutcome(event) {
    const d = event.detail || {};
    const turn = resultOf(d);
    if (turn && d.restore_ref) turn.restore_ref = d.restore_ref;
    if (d.outcome_type !== 'answered') return;
    if (!turn) return;
    const r = sliceFor(turn, d);
    if (!r) return;
    const envelope = d.business_query?.envelope;
    if (envelope) {
        if (r.returned === null && Number.isInteger(envelope.returned_row_count)) r.returned = envelope.returned_row_count;
        if (r.total === null && Number.isInteger(envelope.total_row_count)) r.total = envelope.total_row_count;
    }
    r.streaming = false;
    paintTurn(turn);
    turnStore.save();
}

function copySection(result) {
    const lines = [result.presentation?.title || FALLBACK_TITLE];
    if (result.presentation?.scope) lines.push(result.presentation.scope);
    const line = receiptLine(result);
    if (line) lines.push(line);
    const figure = keyFigureColumn(result);
    if (figure) lines.push(formatCell(result.rows[0][figure.key], figure, result.rows[0]));
    else if (result.rows.length) {
        lines.push(paintedColumns(result).map((c) => c.label).join('\t'));
        for (const row of result.rows) lines.push(paintedColumns(result).map((c) => formatCell(row[c.key], c, row)).join('\t'));
    }
    return lines.join('\n');
}

/** The readable result for the clipboard: title, line, header, rows. */
export function copyTextFor(msgId) {
    if (typeof window !== 'undefined' && window.McpAskAiRestoreGate?.state?.() === 'checking') return null;
    const map = turnStore.byMessage(msgId)?.result;
    if (!(map instanceof Map) || map.size === 0) return null;
    const sections = [];
    for (const result of map.values()) sections.push(copySection(result));
    return sections.join('\n\n');
}

function onTerminal(event) {
    const d = event.detail || {};
    const turn = resultOf(d);
    if (!turn || !(turn.result instanceof Map)) return;
    let changed = false;
    for (const r of turn.result.values()) {
        if (r.streaming) {
            r.streaming = false;
            changed = true;
        }
    }
    if (changed) {
        paintTurn(turn);
        turnStore.save();
    }
}

export const resultTableModule = {
    name: 'result_table',
    mount(eventBus) {
        bus = eventBus;
        bus.addEventListener('table_start', onTableStart);
        bus.addEventListener('table_rows', onTableRows);
        bus.addEventListener('table_end', onTableEnd);
        bus.addEventListener('turn_outcome', onOutcome);
        bus.addEventListener('turn_terminal', onTerminal);
        stopRenderWatch = onThreadRender(repaint);
        repaint();
    },
    unmount() {
        bus?.removeEventListener('table_start', onTableStart);
        bus?.removeEventListener('table_rows', onTableRows);
        bus?.removeEventListener('table_end', onTableEnd);
        bus?.removeEventListener('turn_outcome', onOutcome);
        bus?.removeEventListener('turn_terminal', onTerminal);
        stopRenderWatch?.(); stopRenderWatch = null;
        bus = null;
    },
};

