export function createLoadingController({
    onShow,
    onHide,
    delayMs = 250,
    minVisibleMs = 300,
    now = () => Date.now(),
    setTimer = (fn, ms) => window.setTimeout(fn, ms),
    clearTimer = (id) => window.clearTimeout(id),
} = {}) {
    let active = 0;
    let showTimer = null;
    let visible = false;
    let visibleSince = 0;

    function show() {
        showTimer = null;
        visible = true;
        visibleSince = now();
        onShow();
    }

    function reallyHide() {
        visible = false;
        visibleSince = 0;
        onHide();
    }

    function start() {
        active += 1;
        if (!visible && showTimer === null) {
            showTimer = setTimer(show, delayMs);
        }
    }

    function stop() {
        active = Math.max(0, active - 1);
        if (active > 0) {
            return;
        }

        if (showTimer !== null) {
            clearTimer(showTimer);
            showTimer = null;
            return;
        }

        if (!visible) {
            return;
        }

        const elapsed = now() - visibleSince;
        const wait = elapsed < minVisibleMs ? minVisibleMs - elapsed : 0;

        if (wait === 0) {
            reallyHide();
        } else {
            setTimer(() => {
                if (active === 0) {
                    reallyHide();
                }
            }, wait);
        }
    }

    function reset() {
        active = 0;
        if (showTimer !== null) {
            clearTimer(showTimer);
            showTimer = null;
        }
        if (visible) {
            reallyHide();
        }
    }

    return {
        start,
        stop,
        reset,
        isVisible: () => visible,
        activeCount: () => active,
    };
}

export function shouldTrackLink(anchor, event) {
    if (event.defaultPrevented) return false;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (!anchor) return false;

    const href = anchor.getAttribute('href');
    if (!href) return false;

    // Livewire action links (e.g. <a href="#" wire:click="...">) are not full-page navigation.
    if ([...anchor.attributes].some((attr) => attr.name.startsWith('wire:'))) return false;

    // Hash-only anchors do not navigate to a new document.
    if (href === '#' || href.startsWith('#')) return false;

    const target = anchor.getAttribute('target');
    if (target && target !== '' && target !== '_self') return false;
    if (anchor.hasAttribute('download')) return false;
    if (anchor.hasAttribute('data-no-progress')) return false;

    let url;
    try {
        url = new URL(anchor.href, window.location.href);
    } catch (e) {
        return false;
    }

    if (url.protocol !== 'http:' && url.protocol !== 'https:') return false; // mailto:, tel:, javascript:
    if (url.origin !== window.location.origin) return false;
    if (url.href === window.location.href) return false; // same full URL (e.g. duplicate link)

    return true;
}

export function shouldTrackLivewireRequest(component) {
    if (!component) {
        return true;
    }

    if (component.name === 'notification-bell-component') {
        return false;
    }

    const el = component.el;
    if (!el) {
        return true;
    }

    if (el.hasAttribute('data-no-progress') || el.closest('[data-no-progress]')) {
        return false;
    }

    if (el.querySelector('[data-enable-realtime-notifications]') || el.closest('[data-enable-realtime-notifications]')) {
        return false;
    }

    return true;
}

export function shouldTrackForm(form, event) {
    if (event.defaultPrevented) return false;
    if (!form || form.hasAttribute('data-no-progress')) return false;
    // Livewire forms do not navigate; the Livewire hooks cover them.
    if (form.hasAttribute('wire:submit') || form.querySelector('[wire\\:submit], [wire\\:submit\\.prevent]')) return false;

    const action = form.getAttribute('action');
    if (action) {
        try {
            const url = new URL(action, window.location.href);
            if (url.origin !== window.location.origin) return false;
        } catch (e) {
            return false;
        }
    }
    return true;
}

export function initLoadingIndicator(options = {}) {
    const bar = document.querySelector('[data-loading-bar]');
    if (!bar || bar.dataset.initialized === 'true') {
        return null;
    }
    bar.dataset.initialized = 'true';

    const main = document.querySelector('main[role="main"]');

    const controller = createLoadingController({
        delayMs: options.delayMs ?? 250,
        minVisibleMs: options.minVisibleMs ?? 300,
        onShow() {
            bar.classList.add('is-active');
            if (main) main.setAttribute('aria-busy', 'true');
        },
        onHide() {
            bar.classList.remove('is-active');
            if (main) main.removeAttribute('aria-busy');
        },
    });

    let livewireHooksRegistered = false;
    const registerLivewireHooks = () => {
        if (livewireHooksRegistered || !window.Livewire) {
            return;
        }

        const hookCommit = () => {
            if (typeof window.Livewire.hook !== 'function') {
                return false;
            }

            window.Livewire.hook('commit', ({ component, succeed, fail }) => {
                if (shouldTrackLivewireRequest(component)) {
                    controller.start();
                }

                succeed(() => {
                    if (shouldTrackLivewireRequest(component)) {
                        controller.stop();
                    }
                });

                fail(() => {
                    if (shouldTrackLivewireRequest(component)) {
                        controller.stop();
                    }
                });
            });

            return true;
        };

        if (hookCommit()) {
            livewireHooksRegistered = true;
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', registerLivewireHooks);
    } else {
        registerLivewireHooks();
    }

    document.addEventListener('livewire:init', registerLivewireHooks);

    let navWatchdog = null;
    const clearWatchdog = () => {
        if (navWatchdog !== null) {
            window.clearTimeout(navWatchdog);
            navWatchdog = null;
        }
    };
    const startNavigation = () => {
        controller.start();
        if (navWatchdog === null) {
            navWatchdog = window.setTimeout(() => {
                navWatchdog = null;
                controller.reset();
            }, options.navTimeoutMs ?? 10000);
        }
    };

    document.addEventListener('click', (event) => {
        const anchor = event.target.closest ? event.target.closest('a[href]') : null;
        if (anchor && shouldTrackLink(anchor, event)) {
            startNavigation();
            return;
        }
    });

    document.addEventListener('submit', (event) => {
        if (shouldTrackForm(event.target, event)) {
            startNavigation();
        }
    });

    // Back/forward and bfcache restores: ensure a clean, hidden bar.
    window.addEventListener('pageshow', () => {
        clearWatchdog();
        controller.reset();
    });

    return controller;
}
