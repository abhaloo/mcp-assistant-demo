/*
 * MCP Dock — reusable right-rail panel shell (focus trap, ESC, resize).
 */
(function () {
    'use strict';

    const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

    const docks = new Map();
    let openId = null;
    let lastOpener = null;
    let panelWidth = 420;

    function getShell(id) {
        return document.querySelector('[data-mcp-dock="' + id + '"]');
    }

    function getBackdrop() {
        return document.querySelector('.mcp-dock-backdrop');
    }

    function isFocusable(el) {
        if (el.disabled || el.getAttribute('tabindex') === '-1') return false;
        if (el.hidden) return false;
        const style = window.getComputedStyle(el);
        return style.visibility !== 'hidden' && style.display !== 'none';
    }

    function focusables(root) {
        return Array.from(root.querySelectorAll(FOCUSABLE)).filter(isFocusable);
    }

    function trapFocus(e, shell) {
        if (e.key !== 'Tab' || !shell.classList.contains('is-open')) return;

        const items = focusables(shell);
        if (!items.length) {
            e.preventDefault();
            return;
        }

        e.preventDefault();
        const idx = items.indexOf(document.activeElement);
        if (e.shiftKey) {
            const next = idx <= 0 ? items.length - 1 : idx - 1;
            items[next].focus();
        } else {
            const next = idx === -1 || idx >= items.length - 1 ? 0 : idx + 1;
            items[next].focus();
        }
    }

    function onKeydown(e) {
        if (e.key === 'Escape' && openId) {
            e.preventDefault();
            close(openId);
            return;
        }
        const shell = openId ? getShell(openId) : null;
        if (shell) trapFocus(e, shell);
    }

    function applyWidth(shell) {
        shell.style.setProperty('--mcp-dock-width', panelWidth + 'px');
    }

    function register(id, { title, onOpen, onClose } = {}) {
        docks.set(id, { title: title || id, onOpen, onClose });
    }

    function open(id, openerEl) {
        const shell = getShell(id);
        if (!shell) return;

        if (openId && openId !== id) {
            close(openId, { restoreFocus: false });
        }

        lastOpener = openerEl || document.activeElement;
        openId = id;
        applyWidth(shell);

        shell.hidden = false;
        shell.classList.add('is-open');
        shell.setAttribute('aria-hidden', 'false');

        const backdrop = getBackdrop();
        if (backdrop) {
            backdrop.hidden = false;
            backdrop.classList.add('is-visible');
        }

        document.body.classList.add('mcp-dock-open');

        const cfg = docks.get(id);
        if (cfg?.onOpen) cfg.onOpen(shell);

        const focusTarget = shell.querySelector('[data-mcp-dock-focus]') || focusables(shell)[0];
        if (focusTarget) {
            setTimeout(() => focusTarget.focus(), 0);
        }
    }

    function close(id, opts) {
        const restoreFocus = opts?.restoreFocus !== false;
        const shell = getShell(id);
        if (!shell) return;

        shell.classList.remove('is-open');
        shell.setAttribute('aria-hidden', 'true');
        shell.hidden = true;

        const backdrop = getBackdrop();
        if (backdrop) {
            backdrop.hidden = true;
            backdrop.classList.remove('is-visible');
        }

        if (openId === id) openId = null;
        if (!document.querySelector('.mcp-dock.is-open')) {
            document.body.classList.remove('mcp-dock-open');
        }

        const cfg = docks.get(id);
        if (cfg?.onClose) cfg.onClose(shell);

        if (restoreFocus && lastOpener && typeof lastOpener.focus === 'function') {
            lastOpener.focus();
        }
    }

    function toggle(id, openerEl) {
        const shell = getShell(id);
        if (shell?.classList.contains('is-open')) {
            close(id);
        } else {
            open(id, openerEl);
        }
    }

    function initResize(handle, shell) {
        if (!handle || !shell) return;

        let startX = 0;
        let startWidth = panelWidth;
        let pending = false;
        let lastX = 0;
        let frame = null;

        function applyPending() {
            frame = null;
            if (!pending) return;
            pending = false;
            const dx = startX - lastX;
            panelWidth = Math.min(640, Math.max(320, startWidth + dx));
            applyWidth(shell);
        }

        function onMove(e) {
            pending = true;
            lastX = e.clientX;
            if (frame) return;
            frame = requestAnimationFrame(applyPending);
        }

        function onUp() {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        }

        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            startX = e.clientX;
            startWidth = panelWidth;
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    }

    function boot() {
        document.addEventListener('keydown', onKeydown);

        document.querySelectorAll('[data-mcp-dock]').forEach(shell => {
            const id = shell.getAttribute('data-mcp-dock');
            const closeBtn = shell.querySelector('[data-mcp-dock-close]');
            closeBtn?.addEventListener('click', () => close(id));

            const handle = shell.querySelector('[data-mcp-dock-resize]');
            initResize(handle, shell);
        });

        getBackdrop()?.addEventListener('click', () => {
            if (openId) close(openId);
        });

        document.querySelectorAll('[data-mcp-dock-open]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const id = btn.getAttribute('data-mcp-dock-open');
                if (id) toggle(id, btn);
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.McpDock = { register, open, close, toggle, getPanelWidth: () => panelWidth };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = window.McpDock;
    }
})();
