/*
 * MCP Ask AI sidebar — push layout (desktop) and full-screen sheet (mobile).
 */
(function () {
    'use strict';

    const STORAGE_OPEN = 'mcp.askAi.open';
    const STORAGE_WIDTH = 'mcp.askAi.width';
    const WIDTH_MIN = 320;
    const WIDTH_MAX = 640;
    const WIDTH_DEFAULT = 420;
    const FOCUS_SEL = '[data-mcp-ask-ai-focus]';
    const CLOSE_FALLBACK_MS = 200;

    let panelWidth = WIDTH_DEFAULT;
    let lastOpener = null;
    let hooks = { onOpen: null, onClose: null, onClosing: null };
    let state = 'closed';
    let closeTimer = null;
    let closeTransitionHandler = null;

    function available() {
        const root = document.getElementById('mcp-ask-ai-root');
        const value = root?.dataset.askAiAvailable || root?.dataset.ragEnabled;
        return value === '1';
    }

    function isMobile() {
        return window.matchMedia('(max-width: 767.98px)').matches;
    }

    function getShell() {
        return document.getElementById('mcpAskAiSidebar');
    }

    function getBackdrop() {
        return document.querySelector('.mcp-ask-ai-backdrop');
    }

    function clampWidth(w) {
        return Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, w));
    }

    function readStoredWidth() {
        try {
            const raw = parseInt(localStorage.getItem(STORAGE_WIDTH) || '', 10);
            return clampWidth(Number.isFinite(raw) ? raw : WIDTH_DEFAULT);
        } catch (_) {
            return WIDTH_DEFAULT;
        }
    }

    function applyRootWidth() {
        document.documentElement.style.setProperty('--mcp-ask-ai-width', panelWidth + 'px');
    }

    function syncAriaExpanded(expanded) {
        const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform || '');
        const shortcut = isMac ? 'Command+L' : 'Control+L';
        document.querySelectorAll('[data-mcp-ask-ai-toggle]').forEach((btn) => {
            btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            btn.setAttribute(
                'aria-label',
                expanded
                    ? 'Close Ask Assistant panel (' + shortcut + ')'
                    : 'Open Ask Assistant panel (' + shortcut + ')'
            );
            btn.setAttribute('aria-keyshortcuts', shortcut);
        });
    }

    function focusComposer() {
        const shell = getShell();
        const target = shell?.querySelector(FOCUS_SEL);
        if (target) {
            setTimeout(() => target.focus(), 0);
        }
    }

    function isFullyOpen() {
        return getShell()?.classList.contains('is-open') ?? false;
    }

    /** Panel visible or animating (not hidden). Use for composer refocus during streams. */
    function isOpen() {
        const shell = getShell();
        if (!shell || shell.hidden) return false;
        return state === 'open' || state === 'opening' || state === 'closing';
    }

    function cancelPendingClose() {
        const shell = getShell();
        if (closeTimer) {
            clearTimeout(closeTimer);
            closeTimer = null;
        }
        if (closeTransitionHandler && shell) {
            shell.removeEventListener('transitionend', closeTransitionHandler);
            closeTransitionHandler = null;
        }
    }

    function finishClose(shell, { restoreFocus = true } = {}) {
        cancelPendingClose();

        shell.hidden = true;
        shell.setAttribute('role', 'complementary');
        shell.removeAttribute('aria-modal');

        const backdrop = getBackdrop();
        if (backdrop) {
            backdrop.hidden = true;
            backdrop.classList.remove('is-visible');
            backdrop.setAttribute('aria-hidden', 'true');
        }

        document.body.classList.remove('mcp-ask-ai-open', 'mcp-ask-ai-sheet-open');

        try {
            sessionStorage.setItem(STORAGE_OPEN, '0');
        } catch (_) { /* private browsing */ }

        state = available() ? 'closed' : 'unavailable';
        shell.dataset.state = state;
        syncAriaExpanded(false);
        if (hooks.onClose) hooks.onClose(shell);

        if (restoreFocus && lastOpener && typeof lastOpener.focus === 'function') {
            lastOpener.focus();
        }
    }

    function open(openerEl, { focus = true } = {}) {
        const shell = getShell();
        if (!shell || !available()) return;

        if (state === 'closing') {
            cancelPendingClose();
            state = 'open';
            shell.dataset.state = state;
            shell.classList.add('is-open');
            shell.setAttribute('aria-hidden', 'false');
            syncAriaExpanded(true);
            if (hooks.onOpen) hooks.onOpen(shell);
            if (focus) focusComposer();
            return;
        }

        if (isFullyOpen() && state === 'open') {
            if (focus) focusComposer();
            return;
        }

        lastOpener = openerEl || document.activeElement;
        state = 'opening';
        shell.dataset.state = state;
        panelWidth = readStoredWidth();
        applyRootWidth();

        shell.hidden = false;
        shell.classList.remove('is-open');
        shell.setAttribute('aria-hidden', 'false');

        cancelPendingClose();

        const backdrop = getBackdrop();
        const mobile = isMobile();

        if (hooks.onOpen) hooks.onOpen(shell);

        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                shell.classList.add('is-open');

                if (mobile) {
                    shell.setAttribute('role', 'dialog');
                    shell.setAttribute('aria-modal', 'true');
                    if (backdrop) {
                        backdrop.hidden = false;
                        backdrop.classList.add('is-visible');
                        backdrop.setAttribute('aria-hidden', 'false');
                    }
                    document.body.classList.add('mcp-ask-ai-sheet-open');
                } else {
                    shell.setAttribute('role', 'complementary');
                    shell.removeAttribute('aria-modal');
                    document.body.classList.add('mcp-ask-ai-open');
                    try {
                        sessionStorage.setItem(STORAGE_OPEN, '1');
                    } catch (_) { /* private browsing */ }
                }

                state = 'open';
                shell.dataset.state = state;
                syncAriaExpanded(true);
                if (focus) focusComposer();
            });
        });
    }

    function close({ restoreFocus = true } = {}) {
        const shell = getShell();
        if (!shell) return;
        if (state === 'closing') return;
        if (!isFullyOpen() && state !== 'opening') return;

        state = 'closing';
        shell.dataset.state = state;
        shell.classList.remove('is-open');
        shell.setAttribute('aria-hidden', 'true');

        if (hooks.onClosing) hooks.onClosing(shell);

        cancelPendingClose();

        closeTransitionHandler = (event) => {
            if (event.target !== shell || event.propertyName !== 'transform') return;
            finishClose(shell, { restoreFocus });
        };
        shell.addEventListener('transitionend', closeTransitionHandler);
        closeTimer = setTimeout(() => {
            if (state === 'closing') {
                finishClose(shell, { restoreFocus });
            }
        }, CLOSE_FALLBACK_MS);
    }

    function toggle(openerEl) {
        if (isOpen()) {
            close({ restoreFocus: true });
        } else {
            open(openerEl, { focus: true });
        }
    }

    function getWidth() {
        return panelWidth;
    }

    function composeHook(previous, next) {
        if (!previous) {
            return next || null;
        }
        if (!next) {
            return previous;
        }
        return (...args) => {
            previous(...args);
            next(...args);
        };
    }

    function setHooks({ onOpen, onClose, onClosing } = {}) {
        if (onOpen) hooks.onOpen = composeHook(hooks.onOpen, onOpen);
        if (onClose) hooks.onClose = composeHook(hooks.onClose, onClose);
        if (onClosing) hooks.onClosing = composeHook(hooks.onClosing, onClosing);
    }

    function restorePersistedState() {
        if (isMobile()) return;
        try {
            if (available() && sessionStorage.getItem(STORAGE_OPEN) === '1') {
                open(null, { focus: false });
            }
        } catch (_) { /* private browsing */ }
    }

    function syncKbdLabel() {
        const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform || '');
        const shortcut = isMac ? 'Command+L' : 'Control+L';
        document.querySelectorAll('[data-mcp-ask-ai-kbd]').forEach((kbd) => {
            kbd.textContent = isMac ? '⌘L' : 'Ctrl+L';
        });
        document.querySelectorAll('[data-mcp-ask-ai-toggle]').forEach((btn) => {
            btn.setAttribute('aria-keyshortcuts', shortcut);
            if (btn.getAttribute('aria-expanded') !== 'true') {
                btn.setAttribute('aria-label', 'Open Ask Assistant panel (' + shortcut + ')');
            }
        });
    }

    function shouldIgnoreGlobalShortcut() {
        const t = document.activeElement;
        if (!t || !(t instanceof Element)) return false;
        if (t.closest('#mcpAskAiSidebar')) return false;
        const tag = t.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        if (t.isContentEditable) return true;
        return false;
    }

    function onKeydown(e) {
        if (e.key === 'Escape' && isOpen()) {
            e.preventDefault();
            close({ restoreFocus: true });
            return;
        }

        if (e.key === 'Tab' && isOpen() && isMobile()) {
            const focusable = Array.from(getShell().querySelectorAll(
                'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), ' +
                'select:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )).filter((el) => !el.hidden && el.offsetParent !== null);
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
            return;
        }

        const mod = e.metaKey || e.ctrlKey;
        if (!mod || e.key.toLowerCase() !== 'l') return;
        if (!available() || !document.querySelector('[data-mcp-ask-ai-toggle]:not([disabled])')) return;
        if (shouldIgnoreGlobalShortcut()) return;

        e.preventDefault();
        toggle(document.querySelector('[data-mcp-ask-ai-toggle]'));
    }

    function initResize(handle) {
        if (!handle || isMobile()) return;

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
            panelWidth = clampWidth(startWidth + dx);
            applyRootWidth();
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
            try {
                localStorage.setItem(STORAGE_WIDTH, String(panelWidth));
            } catch (_) { /* private browsing */ }
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
        panelWidth = readStoredWidth();
        applyRootWidth();
        syncKbdLabel();

        document.addEventListener('keydown', onKeydown);

        const shell = getShell();
        state = available() ? 'closed' : 'unavailable';
        if (shell) shell.dataset.state = state;
        shell?.querySelector('[data-mcp-ask-ai-close]')?.addEventListener('click', () => close());

        getBackdrop()?.addEventListener('click', () => {
            if (isOpen()) close();
        });

        document.querySelectorAll('[data-mcp-ask-ai-toggle]').forEach((btn) => {
            btn.addEventListener('click', () => toggle(btn));
        });

        initResize(shell?.querySelector('[data-mcp-ask-ai-resize]'));

        const syncChrome = () => {
            const topbar = document.querySelector('.mcp-topbar');
            const rect = topbar?.getBoundingClientRect();
            // --mcp-topbar-h is the topbar's own height (used by .mcp-topbar { height: ... }).
            // Never write getBoundingClientRect().bottom here — that includes offsetTop and
            // feeds a ResizeObserver loop that stretches the bar to viewport height.
            const topbarH = Math.round(rect?.height || 56);
            const panelTop = Math.max(0, Math.round(rect?.height ? rect.bottom : topbarH));
            const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
            const footerRect = document.querySelector('.footer')?.getBoundingClientRect();
            const footerIsVisible = footerRect
                && footerRect.bottom > 0
                && footerRect.top < viewportHeight;
            const visibleFooterHeight = footerIsVisible
                ? Math.max(0, viewportHeight - Math.max(panelTop, footerRect.top))
                : 0;
            const chromeBottom = Math.min(
                Math.max(0, viewportHeight - panelTop),
                Math.round(visibleFooterHeight)
            );
            document.documentElement.style.setProperty('--mcp-topbar-h', topbarH + 'px');
            document.documentElement.style.setProperty('--mcp-ask-ai-top', panelTop + 'px');
            document.documentElement.style.setProperty('--mcp-chrome-bottom', chromeBottom + 'px');
        };
        const scheduleChrome = () => requestAnimationFrame(syncChrome);
        if (typeof ResizeObserver !== 'undefined') {
            const observer = new ResizeObserver(scheduleChrome);
            const topbar = document.querySelector('.mcp-topbar');
            const footer = document.querySelector('.footer');
            if (topbar) observer.observe(topbar);
            if (footer) observer.observe(footer);
        }
        window.addEventListener('resize', scheduleChrome, { passive: true });
        window.addEventListener('scroll', scheduleChrome, { passive: true });
        syncChrome();

        window.addEventListener('mcp:askai:unavailable', () => {
            state = 'unavailable';
            if (shell) shell.dataset.state = state;
            close({ restoreFocus: false });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.McpAskAiSidebar = {
        open,
        close,
        toggle,
        isOpen,
        getWidth,
        setHooks,
        restorePersistedState,
        get state() { return state; },
        /** @internal test helpers */
        _test: {
            CLOSE_FALLBACK_MS,
            finishClose,
            cancelPendingClose,
        },
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = window.McpAskAiSidebar;
    }
})();
