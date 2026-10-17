import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createLoadingController, shouldTrackLink, shouldTrackForm, shouldTrackLivewireRequest } from './loading-indicator';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function make(overrides = {}) {
    const calls = { show: 0, hide: 0 };
    const controller = createLoadingController({
        delayMs: 250,
        minVisibleMs: 300,
        now: () => Date.now(),
        setTimer: (fn, ms) => setTimeout(fn, ms),
        clearTimer: (id) => clearTimeout(id),
        onShow: () => { calls.show += 1; },
        onHide: () => { calls.hide += 1; },
        ...overrides,
    });
    return { controller, calls };
}

function makeClickEvent(overrides = {}) {
    return {
        button: 0,
        defaultPrevented: false,
        metaKey: false,
        ctrlKey: false,
        shiftKey: false,
        altKey: false,
        ...overrides,
    };
}

describe('createLoadingController', () => {
    it('does not show before the delay threshold', () => {
        const { controller, calls } = make();
        controller.start();
        vi.advanceTimersByTime(249);
        expect(calls.show).toBe(0);
    });

    it('shows after the delay while a request is active', () => {
        const { controller, calls } = make();
        controller.start();
        vi.advanceTimersByTime(250);
        expect(calls.show).toBe(1);
        expect(controller.isVisible()).toBe(true);
    });

    it('shows nothing for a request that resolves before the delay', () => {
        const { controller, calls } = make();
        controller.start();
        vi.advanceTimersByTime(100);
        controller.stop();
        vi.advanceTimersByTime(1000);
        expect(calls.show).toBe(0);
        expect(calls.hide).toBe(0);
    });

    it('stays visible until all overlapping requests finish', () => {
        const { controller, calls } = make();
        controller.start();
        controller.start();
        vi.advanceTimersByTime(250);
        expect(calls.show).toBe(1);

        controller.stop();
        vi.advanceTimersByTime(500);
        expect(calls.hide).toBe(0); // one request still active

        controller.stop();
        vi.advanceTimersByTime(300); // min-visible window
        expect(calls.hide).toBe(1);
    });

    it('honors the minimum visible window', () => {
        const { controller, calls } = make();
        controller.start();
        vi.advanceTimersByTime(250); // shown at t=250
        controller.stop();           // elapsed 0 -> wait 300
        vi.advanceTimersByTime(299);
        expect(calls.hide).toBe(0);
        vi.advanceTimersByTime(1);
        expect(calls.hide).toBe(1);
    });

    it('hides immediately on reset', () => {
        const { controller, calls } = make();
        controller.start();
        vi.advanceTimersByTime(250);
        controller.reset();
        expect(calls.hide).toBe(1);
        expect(controller.isVisible()).toBe(false);
        expect(controller.activeCount()).toBe(0);
    });
});

describe('shouldTrackLink', () => {
    it('ignores Livewire action links and hash-only anchors', () => {
        const wireLink = document.createElement('a');
        wireLink.setAttribute('href', '#');
        wireLink.setAttribute('wire:click', 'doThing');
        expect(shouldTrackLink(wireLink, makeClickEvent())).toBe(false);

        const hashLink = document.createElement('a');
        hashLink.setAttribute('href', '#section');
        expect(shouldTrackLink(hashLink, makeClickEvent())).toBe(false);
    });

    it('tracks same-origin route navigation', () => {
        const link = document.createElement('a');
        link.href = `${window.location.origin}/bills`;
        expect(shouldTrackLink(link, makeClickEvent())).toBe(true);
    });

    it('ignores data-no-progress and off-origin links', () => {
        const skip = document.createElement('a');
        skip.href = `${window.location.origin}/export`;
        skip.setAttribute('data-no-progress', '');
        expect(shouldTrackLink(skip, makeClickEvent())).toBe(false);

        const external = document.createElement('a');
        external.href = 'https://example.com/page';
        expect(shouldTrackLink(external, makeClickEvent())).toBe(false);
    });
});

describe('shouldTrackLivewireRequest', () => {
    it('ignores components marked with data-no-progress', () => {
        const el = document.createElement('div');
        el.setAttribute('data-no-progress', '');

        expect(shouldTrackLivewireRequest({ el })).toBe(false);
        expect(shouldTrackLivewireRequest({ el: document.createElement('div') })).toBe(true);
        expect(shouldTrackLivewireRequest(null)).toBe(true);
    });

    it('ignores components nested under data-no-progress', () => {
        const root = document.createElement('div');
        root.setAttribute('data-no-progress', '');
        const child = document.createElement('div');
        root.appendChild(child);

        expect(shouldTrackLivewireRequest({ el: child })).toBe(false);
    });

    it('ignores the notification bell component by name', () => {
        expect(shouldTrackLivewireRequest({ name: 'notification-bell-component', el: document.createElement('div') })).toBe(false);
    });
});

describe('shouldTrackForm', () => {
    it('ignores defaultPrevented and data-no-progress forms', () => {
        const form = document.createElement('form');
        expect(shouldTrackForm(form, { defaultPrevented: true })).toBe(false);

        form.setAttribute('data-no-progress', '');
        expect(shouldTrackForm(form, { defaultPrevented: false })).toBe(false);
    });

    it('ignores Livewire submit forms', () => {
        const form = document.createElement('form');
        form.setAttribute('wire:submit', 'save');
        expect(shouldTrackForm(form, { defaultPrevented: false })).toBe(false);
    });

    it('tracks same-origin form submits', () => {
        const form = document.createElement('form');
        form.setAttribute('action', `${window.location.origin}/customers`);
        expect(shouldTrackForm(form, { defaultPrevented: false })).toBe(true);
    });

    it('ignores cross-origin form actions', () => {
        const form = document.createElement('form');
        form.setAttribute('action', 'https://evil.example/steal');
        expect(shouldTrackForm(form, { defaultPrevented: false })).toBe(false);
    });
});

describe('global loading bar markup', () => {
    function loadBarElement() {
        const blade = readFileSync(
            resolve(process.cwd(), 'resources/views/layouts/app.blade.php'),
            'utf8',
        );
        const match = blade.match(/<div class="mcp-loading-bar"[^>]*>[\s\S]*?<\/div>/);
        expect(match).not.toBeNull();

        const container = document.createElement('div');
        container.innerHTML = match[0];
        return container.querySelector('[data-loading-bar]');
    }

    it('mounts four process-ink plates in press order inside [data-loading-bar]', () => {
        const bar = loadBarElement();
        expect(bar).not.toBeNull();
        expect(bar.getAttribute('aria-hidden')).toBe('true');

        const plates = [...bar.querySelectorAll('.mcp-loading-bar__plate')];
        const modifiers = plates.map((plate) =>
            [...plate.classList].find((name) => name.startsWith('mcp-loading-bar__plate--')),
        );

        expect(modifiers).toEqual([
            'mcp-loading-bar__plate--c',
            'mcp-loading-bar__plate--m',
            'mcp-loading-bar__plate--y',
            'mcp-loading-bar__plate--k',
        ]);
    });
});
