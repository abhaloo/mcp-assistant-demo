import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function mountTwo() {
    document.body.innerHTML = `
        <div id="root">
          <div data-mcp-dropdown data-mcp-ask-scope>
            <button type="button" data-mcp-dropdown-trigger
                    aria-haspopup="menu" aria-expanded="false" aria-controls="scope-menu"
                    aria-label="Select context">
              <span class="mcp-dropdown__label">All Records</span>
            </button>
            <div id="scope-menu" role="menu" hidden data-mcp-dropdown-menu>
              <button type="button" role="menuitemradio" aria-checked="true">All Records</button>
              <button type="button" role="menuitemradio" aria-checked="false">Current page: Jobs</button>
            </div>
          </div>
          <div data-mcp-dropdown data-mcp-ask-mode>
            <button type="button" data-mcp-dropdown-trigger
                    aria-haspopup="menu" aria-expanded="false" aria-controls="mode-menu"
                    aria-label="Mode">Ask</button>
            <div id="mode-menu" role="menu" hidden data-mcp-dropdown-menu>
              <button type="button" role="menuitemradio" aria-checked="true" data-mcp-ask-mode-option="ask">Ask</button>
              <button type="button" role="menuitemradio" aria-checked="false" disabled aria-disabled="true" data-mcp-ask-mode-option="act">Act — Coming soon</button>
              <button type="button" role="menuitemradio" aria-checked="false" data-mcp-ask-mode-option="plan">Plan</button>
            </div>
          </div>
          <button type="button" id="after-mode">After</button>
        </div>`;
}

async function loadDropdown() {
    vi.resetModules();
    await import('../../public/js/mcp-dropdown.js');
}

describe('McpDropdown', () => {
    beforeEach(async () => {
        mountTwo();
        await loadDropdown();
        window.McpDropdown.bind(document.getElementById('root'));
    });
    afterEach(() => { document.body.innerHTML = ''; });

    const scope = () => document.querySelector('[data-mcp-ask-scope]');
    const scopeTrigger = () => document.querySelector('[data-mcp-ask-scope] [data-mcp-dropdown-trigger]');
    const modeTrigger = () => document.querySelector('[data-mcp-ask-mode] [data-mcp-dropdown-trigger]');
    const scopeMenu = () => document.getElementById('scope-menu');
    const modeMenu = () => document.getElementById('mode-menu');

    it('opens All Records and focuses the first enabled option', () => {
        scopeTrigger().click();
        expect(scopeMenu().hidden).toBe(false);
        expect(scopeTrigger().getAttribute('aria-expanded')).toBe('true');
        expect(document.activeElement).toBe(scopeMenu().querySelector('[role="menuitemradio"]'));
    });

    it('closes the open menu on a second trigger click', () => {
        scopeTrigger().click();
        scopeTrigger().click();
        expect(scopeMenu().hidden).toBe(true);
        expect(scopeTrigger().getAttribute('aria-expanded')).toBe('false');
    });

    it('closes the other menu so only one dropdown is open', () => {
        scopeTrigger().click();
        modeTrigger().click();
        expect(scopeMenu().hidden).toBe(true);
        expect(modeMenu().hidden).toBe(false);
        expect(scopeTrigger().getAttribute('aria-expanded')).toBe('false');
        expect(modeTrigger().getAttribute('aria-expanded')).toBe('true');
    });

    it('skips a disabled option and lands on the next enabled item on ArrowDown', () => {
        modeTrigger().click();
        const ask = modeMenu().querySelector('[data-mcp-ask-mode-option="ask"]');
        const act = modeMenu().querySelector('[data-mcp-ask-mode-option="act"]');
        const plan = modeMenu().querySelector('[data-mcp-ask-mode-option="plan"]');
        expect(document.activeElement).toBe(ask);
        ask.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
        expect(document.activeElement).toBe(plan);
        expect(document.activeElement).not.toBe(act);
    });

    it('moves Home to the first enabled item and End to the last', () => {
        scopeTrigger().click();
        const items = [...scopeMenu().querySelectorAll('[role="menuitemradio"]')];
        document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
        expect(document.activeElement).toBe(items[1]);
        document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
        expect(document.activeElement).toBe(items[0]);
    });

    it('opens from ArrowUp on the trigger and focuses the last enabled item', () => {
        scopeTrigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
        const items = [...scopeMenu().querySelectorAll('[role="menuitemradio"]')];
        expect(scopeMenu().hidden).toBe(false);
        expect(document.activeElement).toBe(items[1]);
    });

    it('closes on Escape and returns focus to the trigger', () => {
        scopeTrigger().click();
        document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        expect(scopeMenu().hidden).toBe(true);
        expect(document.activeElement).toBe(scopeTrigger());
    });

    it('stops Escape from bubbling so a parent panel does not also close', () => {
        const seen = [];
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') seen.push('document');
        });
        scopeTrigger().click();
        document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        expect(scopeMenu().hidden).toBe(true);
        expect(seen).toEqual([]);
    });

    it('closes on an outside click and does not dump focus to body', () => {
        const outside = document.getElementById('after-mode');
        scopeTrigger().click();
        expect(document.activeElement).not.toBe(document.body);
        outside.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        expect(scopeMenu().hidden).toBe(true);
        expect(scopeTrigger().getAttribute('aria-expanded')).toBe('false');
        expect(document.activeElement).not.toBe(document.body);
    });

    it('on Tab from an open menu, closes and moves focus to the next control', () => {
        scopeTrigger().click();
        const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
        document.activeElement.dispatchEvent(event);
        expect(event.defaultPrevented).toBe(true);
        expect(scopeMenu().hidden).toBe(true);
        expect(document.activeElement).toBe(modeTrigger());
        expect(document.activeElement).not.toBe(document.body);
    });

    it('on Shift+Tab from an open menu, closes and keeps focus on a control', () => {
        modeTrigger().click();
        const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true, shiftKey: true });
        document.activeElement.dispatchEvent(event);
        expect(event.defaultPrevented).toBe(true);
        expect(modeMenu().hidden).toBe(true);
        expect(document.activeElement).toBe(scopeTrigger());
        expect(document.activeElement).not.toBe(document.body);
    });

    it('opens from ArrowDown on the trigger', () => {
        scopeTrigger().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
        expect(scopeMenu().hidden).toBe(false);
    });

    it('does not bind twice on the same root', () => {
        window.McpDropdown.bind(document.getElementById('root'));
        scopeTrigger().click();
        expect(scopeMenu().hidden).toBe(false);
        scopeTrigger().click();
        expect(scopeMenu().hidden).toBe(true);
    });

    it('closes when an enabled option is chosen and stays open on disabled Act', () => {
        modeTrigger().click();
        modeMenu().querySelector('[data-mcp-ask-mode-option="ask"]').click();
        expect(modeMenu().hidden).toBe(true);
        modeTrigger().click();
        modeMenu().querySelector('[data-mcp-ask-mode-option="act"]').click();
        expect(modeMenu().hidden).toBe(false);
    });

    it('notifies listeners before the menu is shown so they can fill options', () => {
        const seen = [];
        scope().addEventListener('mcp-dropdown:beforeopen', () => {
            seen.push(scopeMenu().hidden);
        });
        scopeTrigger().click();
        expect(seen).toEqual([true]);
        expect(scopeMenu().hidden).toBe(false);
    });

    it('builds a menu option with the shared chrome', () => {
        const onChoose = vi.fn();
        const el = window.McpDropdown.option({
            text: 'All Records',
            checked: true,
            onChoose,
        });
        expect(el.className).toBe('mcp-dropdown__option');
        expect(el.getAttribute('role')).toBe('menuitemradio');
        expect(el.getAttribute('aria-checked')).toBe('true');
        el.click();
        expect(onChoose).toHaveBeenCalledTimes(1);
    });
});

describe('McpDropdown bind completeness', () => {
    afterEach(() => { document.body.innerHTML = ''; });

    it('does not mark incomplete markup as bound so a later bind can attach', async () => {
        document.body.innerHTML = `
            <div id="root">
              <div data-mcp-dropdown>
                <button type="button" data-mcp-dropdown-trigger>Open</button>
              </div>
            </div>`;
        await loadDropdown();
        window.McpDropdown.bind(document.getElementById('root'));
        const dropdown = document.querySelector('[data-mcp-dropdown]');
        expect(dropdown.dataset.mcpDropdownBound).not.toBe('1');
        dropdown.insertAdjacentHTML(
            'beforeend',
            '<div role="menu" hidden data-mcp-dropdown-menu><button type="button" role="menuitem">One</button></div>'
        );
        window.McpDropdown.bind(document.getElementById('root'));
        dropdown.querySelector('[data-mcp-dropdown-trigger]').click();
        expect(dropdown.querySelector('[data-mcp-dropdown-menu]').hidden).toBe(false);
    });

    it('binds on import when the document is already ready', async () => {
        mountTwo();
        await loadDropdown();
        document.querySelector('[data-mcp-ask-scope] [data-mcp-dropdown-trigger]').click();
        expect(document.getElementById('scope-menu').hidden).toBe(false);
    });
});
