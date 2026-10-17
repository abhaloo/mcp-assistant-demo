import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const composerCss = fs.readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../public/css/mcp-ask-ai-composer.css'),
    'utf8'
);

function mount() {
    document.body.innerHTML = `
        <div id="mcp-ask-ai-root">
          <div class="mcp-ask-ai__meta">
            <div class="mcp-ask-ai__scope" data-mcp-dropdown data-mcp-ask-scope>
              <button type="button" class="mcp-btn mcp-btn--secondary mcp-btn--xs" data-mcp-dropdown-trigger aria-haspopup="menu" aria-expanded="false" aria-controls="mcp-ask-ai-scope-menu" aria-label="Select context">
                <span class="mcp-dropdown__label">All records</span></button>
              <div id="mcp-ask-ai-scope-menu" role="menu" aria-label="Select context" hidden data-mcp-dropdown-menu></div>
            </div>
            <div class="mcp-ask-ai__mode" data-mcp-dropdown data-mcp-ask-mode>
              <button type="button" data-mcp-dropdown-trigger aria-haspopup="menu" aria-expanded="false" aria-controls="mcp-ask-ai-mode-menu" aria-label="Mode">Ask</button>
              <div id="mcp-ask-ai-mode-menu" role="menu" aria-label="Mode" hidden data-mcp-dropdown-menu>
                <button type="button" role="menuitemradio" aria-checked="true" data-mcp-ask-mode-option="ask">Ask</button>
                <button type="button" role="menuitemradio" aria-checked="false" disabled aria-disabled="true" data-mcp-ask-mode-option="act">Act — Coming soon</button>
              </div>
            </div>
          </div>
        </div>`;
}
const jobs = { version: 1, context_id: 'jobs:index', route: 'work-order.index', kind: 'list', resource_type: 'work_order', record_ids: ['2', '1'] };
const job = { version: 1, context_id: 'jobs:show:24694', route: 'work-order.show', kind: 'record', resource_type: 'work_order', record_ids: ['24694'] };
let snapshot; let subscribers;

describe('composer scope and mode', () => {
    beforeEach(async () => {
        snapshot = jobs; subscribers = [];
        window.McpPageContext = { PageContextRegistry: { captureForSubmit: () => snapshot, subscribe: (fn) => { subscribers.push(fn); return () => {}; } } };
        mount(); vi.resetModules();
        await import('../../public/js/mcp-dropdown.js');
        await import('../../public/js/mcp-ask-ai-composer.js');
        window.McpAskAiComposer.init(document.getElementById('mcp-ask-ai-root'), { contextSubject: (s) => (s?.kind === 'record' ? 'Job on this page' : 'Jobs') });
    });
    afterEach(() => { document.body.innerHTML = ''; });

    const label = () => document.querySelector('[data-mcp-ask-scope] .mcp-dropdown__label').textContent;
    const items = () => [...document.querySelectorAll('[data-mcp-ask-scope] [data-mcp-dropdown-menu] [role="menuitemradio"]')];
    const trigger = () => document.querySelector('[data-mcp-ask-scope] [data-mcp-dropdown-trigger]');

    it('defaults to All records and sends no context', () => {
        expect(label()).toBe('All records');
        expect(window.McpAskAiComposer.contextForSend()).toEqual({ pageContext: null, recordContext: null });
        expect(document.querySelector('.mcp-ask-ai__context-card')).toBeNull();
    });

    it('offers the current page with its title and sends the existing page context', () => {
        trigger().click();
        expect(trigger().getAttribute('aria-expanded')).toBe('true');
        expect(items().map((i) => i.textContent.trim())).toEqual(['All records', 'Current page: Jobs']);
        items()[1].click();
        expect(label()).toBe('Current page: Jobs');
        expect(document.querySelector('[data-mcp-ask-scope] [data-mcp-dropdown-menu]').hidden).toBe(true);
        expect(window.McpAskAiComposer.contextForSend()).toEqual({ pageContext: jobs, recordContext: null });
        expect(window.McpAskAiComposer.contextForSend().pageContext).toEqual(jobs); // persists for the conversation
    });

    it('titles a single-record page by its record', () => {
        snapshot = job; subscribers.forEach((fn) => fn());
        trigger().click();
        expect(items()[1].textContent.trim()).toBe('Current page: Job 24694');
    });

    it('understands the v2 page-context shape, which has no context_id or route', () => {
        const v2 = { version: 2, profile: 'jobs.show.v2', records: [{ id: '24694', resource_type: 'job' }], detail_contract: 'x' };
        snapshot = v2; subscribers.forEach((fn) => fn());
        trigger().click();
        expect(items()[1].textContent.trim()).toBe('Current page: Job 24694');
        items()[1].click();
        expect(window.McpAskAiComposer.contextForSend()).toEqual({ pageContext: v2, recordContext: null });
        snapshot = { ...v2, records: [{ id: '24700', resource_type: 'job' }] }; subscribers.forEach((fn) => fn());
        expect(label()).toBe('All records');
    });

    it('resets to All records when the route changes and never sends stale context', () => {
        trigger().click(); items()[1].click();
        snapshot = job; subscribers.forEach((fn) => fn());
        expect(label()).toBe('All records');
        expect(window.McpAskAiComposer.contextForSend()).toEqual({ pageContext: null, recordContext: null });
    });

    it('disables the current-page option with a reason when the route has no safe context', () => {
        snapshot = null; subscribers.forEach((fn) => fn());
        trigger().click();
        expect(items()[1].disabled).toBe(true);
        expect(items()[1].getAttribute('aria-disabled')).toBe('true');
        expect(items()[1].title).toBe('No page context on this page');
    });

    it('stages a selected record for one turn and suppresses page context', () => {
        const rc = { version: 1, origin: 'explicit_selection', query: 'job #24694', references: [{ resource_type: 'job', record_id: '24694' }] };
        window.McpAskAiComposer.stageRecord(rc, 'Job 24694');
        expect(label()).toBe('Selected record: Job 24694');
        expect(window.McpAskAiComposer.contextForSend()).toEqual({ pageContext: null, recordContext: rc });
        expect(label()).toBe('All records');
        expect(window.McpAskAiComposer.contextForSend()).toEqual({ pageContext: null, recordContext: null });
    });

    it('supports the keyboard contract', () => {
        trigger().focus();
        trigger().click();
        expect(trigger().getAttribute('aria-expanded')).toBe('true');
        expect(document.activeElement).toBe(items()[0]);
        items()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
        expect(document.activeElement).toBe(items()[1]);
        items()[1].click();
        expect(label()).toBe('Current page: Jobs');
        trigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
        expect(trigger().getAttribute('aria-expanded')).toBe('true');
        document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        expect(trigger().getAttribute('aria-expanded')).toBe('false');
        expect(document.activeElement).toBe(trigger());
    });

    it('keeps Ask selected and Act disabled, and exposes no mode on the wire', () => {
        const act = document.querySelector('[data-mcp-ask-mode-option="act"]');
        act.click();
        expect(document.querySelector('[data-mcp-ask-mode-option="ask"]').getAttribute('aria-checked')).toBe('true');
        expect(act.disabled).toBe(true);
        expect(window.McpAskAiComposer.mode()).toBe('ask');
        expect(window.McpAskAiComposer.contextForSend()).not.toHaveProperty('mode');
    });

    it('fails closed when McpDropdown is missing', async () => {
        vi.resetModules();
        delete window.McpDropdown;
        await import('../../public/js/mcp-ask-ai-composer.js');
        expect(() => window.McpAskAiComposer.init(document.getElementById('mcp-ask-ai-root'))).toThrow(/McpDropdown/);
    });

    it('fills scope options through McpDropdown.option, not a local factory', () => {
        trigger().click();
        expect(items().every((el) => el.classList.contains('mcp-dropdown__option'))).toBe(true);
        expect(typeof window.McpDropdown.option).toBe('function');
    });
});

describe('composer disclaimer layout', () => {
    it('wraps the access note only at the 480px breakpoint', () => {
        const beforeMedia = composerCss.split('@media')[0];
        expect(beforeMedia).not.toMatch(/\.mcp-ask-ai__mode-label[\s\S]{0,120}flex-basis:\s*100%/);
        expect(composerCss).toMatch(
            /@media \(max-width: 479\.98px\)[\s\S]*\.mcp-ask-ai__mode-label\s*\{[^}]*flex-basis:\s*100%/
        );
    });
});
