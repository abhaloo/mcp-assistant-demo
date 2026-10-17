import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

function mountSidebarHtml() {
    document.body.className = 'mcp-shifted';
    document.body.innerHTML = `
        <button id="toggle" data-mcp-ask-ai-toggle data-ask-ai-available="1" aria-expanded="false">Ask AI</button>
        <div class="mcp-ask-ai-backdrop" hidden></div>
        <aside class="mcp-ask-ai-sidebar" id="mcpAskAiSidebar" hidden aria-hidden="true">
            <div id="mcp-ask-ai-root" data-ask-ai-available="1"></div>
            <button data-mcp-ask-ai-close>Close</button>
            <textarea data-mcp-ask-ai-focus id="composer"></textarea>
            <div class="mcp-ask-ai-sidebar__resize" data-mcp-ask-ai-resize></div>
        </aside>
        <div class="wrapper">
            <header class="mcp-topbar"></header>
            <button id="page-btn">Page action</button>
            <footer class="footer"></footer>
        </div>
    `;
}

function mockDesktop() {
    window.matchMedia = vi.fn((query) => ({
        matches: query.includes('max-width') ? false : query.includes('min-width'),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
    }));
}

function dispatchTransformTransitionEnd(shell) {
    const evt = new Event('transitionend', { bubbles: true });
    Object.defineProperty(evt, 'propertyName', { value: 'transform' });
    shell.dispatchEvent(evt);
}

async function waitForCloseSettle() {
    const ms = window.McpAskAiSidebar._test.CLOSE_FALLBACK_MS + 20;
    await new Promise((r) => setTimeout(r, ms));
}

async function waitForOpenSettle() {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}

describe('McpAskAiSidebar (desktop)', () => {
    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();
        mountSidebarHtml();
        mockDesktop();
        vi.resetModules();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
    });

    afterEach(() => {
        document.body.innerHTML = '';
        document.body.className = '';
        document.documentElement.style.removeProperty('--mcp-ask-ai-width');
        document.documentElement.style.removeProperty('--mcp-topbar-h');
        document.documentElement.style.removeProperty('--mcp-ask-ai-top');
        document.documentElement.style.removeProperty('--mcp-chrome-bottom');
    });

    it('composes onClosing hooks so panel and module handlers both run', async () => {
        const panelClosing = vi.fn();
        const moduleClosing = vi.fn();
        window.McpAskAiSidebar.setHooks({ onClosing: panelClosing });
        window.McpAskAiSidebar.setHooks({ onClosing: moduleClosing });

        window.McpAskAiSidebar.open(document.getElementById('toggle'));
        await waitForOpenSettle();
        window.McpAskAiSidebar.close({ restoreFocus: false });
        dispatchTransformTransitionEnd(document.getElementById('mcpAskAiSidebar'));

        expect(panelClosing).toHaveBeenCalledOnce();
        expect(moduleClosing).toHaveBeenCalledOnce();
    });

    it('open adds mcp-ask-ai-open on body and aria-expanded on toggle', async () => {
        const toggle = document.getElementById('toggle');
        window.McpAskAiSidebar.open(toggle);
        await waitForOpenSettle();

        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(true);
        expect(document.body.classList.contains('mcp-ask-ai-sheet-open')).toBe(false);
        expect(toggle.getAttribute('aria-expanded')).toBe('true');
        expect(document.getElementById('mcpAskAiSidebar').classList.contains('is-open')).toBe(true);
        // CSS highlight (mcp-ask-ai-sidebar.css) keys off aria-expanded="true".
        expect(toggle.getAttribute('aria-label')).toMatch(/Close Ask Assistant panel/);
    });

    it('bounds the panel below the topbar and above a visible app footer', async () => {
        const topbar = document.querySelector('.mcp-topbar');
        const footer = document.querySelector('.footer');
        topbar.getBoundingClientRect = () => ({
            top: 24, bottom: 80, left: 0, right: 1200, width: 1200, height: 56,
        });
        footer.getBoundingClientRect = () => ({
            top: 752, bottom: 800, left: 0, right: 1200, width: 1200, height: 48,
        });
        Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });

        window.dispatchEvent(new Event('resize'));
        await new Promise((r) => requestAnimationFrame(r));

        expect(document.documentElement.style.getPropertyValue('--mcp-ask-ai-top')).toBe('80px');
        expect(document.documentElement.style.getPropertyValue('--mcp-chrome-bottom')).toBe('48px');
    });

    it('Esc closes and restores focus to opener', async () => {
        const toggle = document.getElementById('toggle');
        toggle.focus();
        window.McpAskAiSidebar.open(toggle, { focus: false });
        await waitForOpenSettle();
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await waitForCloseSettle();
        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(false);
        expect(toggle.getAttribute('aria-expanded')).toBe('false');
        expect(document.activeElement).toBe(toggle);
    });

    it('Ctrl+L toggles the panel when not focused in an input', async () => {
        document.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'l',
            ctrlKey: true,
            bubbles: true,
            cancelable: true,
        }));
        await waitForOpenSettle();
        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(true);

        document.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'l',
            ctrlKey: true,
            bubbles: true,
            cancelable: true,
        }));
        await waitForCloseSettle();
        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(false);
    });

    it('Ctrl+L is ignored while typing in an input', async () => {
        const input = document.createElement('input');
        document.body.appendChild(input);
        input.focus();
        document.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'l',
            ctrlKey: true,
            bubbles: true,
            cancelable: true,
        }));
        await new Promise((r) => setTimeout(r, 0));
        expect(window.McpAskAiSidebar.isOpen()).toBe(false);
        input.remove();
    });

    it('desktop open does not trap Tab (no preventDefault)', async () => {
        window.McpAskAiSidebar.open(document.getElementById('toggle'));
        await new Promise((r) => setTimeout(r, 0));

        const ev = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
        const prevented = !document.dispatchEvent(ev) || ev.defaultPrevented;
        expect(prevented).toBe(false);

        const panel = document.getElementById('mcpAskAiSidebar');
        const wrapper = document.querySelector('.wrapper');
        expect(panel.compareDocumentPosition(wrapper) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('desktop open/close writes mcp.askAi.open to session storage', async () => {
        window.McpAskAiSidebar.open(document.getElementById('toggle'));
        await waitForOpenSettle();
        expect(sessionStorage.getItem('mcp.askAi.open')).toBe('1');
        window.McpAskAiSidebar.close({ restoreFocus: false });
        await waitForCloseSettle();
        expect(sessionStorage.getItem('mcp.askAi.open')).toBe('0');
    });

    it('close defers hidden until transition settles', async () => {
        const shell = document.getElementById('mcpAskAiSidebar');
        window.McpAskAiSidebar.open(document.getElementById('toggle'));
        await waitForOpenSettle();
        expect(shell.hidden).toBe(false);
        expect(shell.classList.contains('is-open')).toBe(true);

        window.McpAskAiSidebar.close({ restoreFocus: false });
        expect(shell.classList.contains('is-open')).toBe(false);
        expect(shell.hidden).toBe(false);
        expect(window.McpAskAiSidebar.isOpen()).toBe(true);
        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(true);

        await waitForCloseSettle();
        expect(shell.hidden).toBe(true);
        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(false);
    });

    it('close finishes immediately on transitionend when transform fires', async () => {
        const shell = document.getElementById('mcpAskAiSidebar');
        window.McpAskAiSidebar.open(document.getElementById('toggle'));
        await waitForOpenSettle();

        window.McpAskAiSidebar.close({ restoreFocus: false });
        expect(shell.hidden).toBe(false);

        dispatchTransformTransitionEnd(shell);
        expect(shell.hidden).toBe(true);
        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(false);
    });

    it('restorePersistedState opens without focusing composer', async () => {
        sessionStorage.setItem('mcp.askAi.open', '1');
        window.McpAskAiSidebar.restorePersistedState();
        await waitForOpenSettle();
        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(true);
        expect(document.activeElement.id).not.toBe('composer');
    });

    it('syncChrome keeps sidebar height after measuring topbar', async () => {
        const topbar = document.createElement('header');
        topbar.className = 'mcp-topbar';
        topbar.getBoundingClientRect = () => ({
            bottom: 72,
            top: 16,
            left: 0,
            right: 1200,
            width: 1200,
            height: 56,
            x: 0,
            y: 16,
            toJSON() { return {}; },
        });
        document.body.prepend(topbar);
        Object.defineProperty(window, 'innerHeight', { configurable: true, value: 900 });

        vi.resetModules();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
        await new Promise((r) => requestAnimationFrame(r));

        const shell = document.getElementById('mcpAskAiSidebar');
        window.McpAskAiSidebar.open(document.getElementById('toggle'));
        await waitForOpenSettle();

        expect(document.documentElement.style.getPropertyValue('--mcp-topbar-h')).toBe('56px');
        expect(document.documentElement.style.getPropertyValue('--mcp-chrome-bottom')).toBe('0px');
        expect(shell.classList.contains('is-open')).toBe(true);
        expect(shell.hidden).toBe(false);
    });
});

describe('McpAskAiSidebar (mobile)', () => {
    beforeEach(async () => {
        localStorage.clear();
        sessionStorage.clear();
        mountSidebarHtml();
        window.matchMedia = vi.fn((query) => ({
            matches: query.includes('max-width'),
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }));
        vi.resetModules();
        await import('../../public/js/mcp-ask-ai-sidebar.js');
    });

    afterEach(() => {
        document.body.innerHTML = '';
        document.body.className = '';
    });

    it('open uses sheet mode without push class', async () => {
        window.McpAskAiSidebar.open(document.getElementById('toggle'));
        await waitForOpenSettle();
        expect(document.body.classList.contains('mcp-ask-ai-open')).toBe(false);
        expect(document.body.classList.contains('mcp-ask-ai-sheet-open')).toBe(true);
        expect(document.querySelector('.mcp-ask-ai-backdrop').classList.contains('is-visible')).toBe(true);
        expect(localStorage.getItem('mcp.askAi.open')).not.toBe('1');
    });

    it('does not restore persisted open on mobile', async () => {
        sessionStorage.setItem('mcp.askAi.open', '1');
        window.McpAskAiSidebar.restorePersistedState();
        await new Promise((r) => setTimeout(r, 0));
        expect(window.McpAskAiSidebar.isOpen()).toBe(false);
    });
});
