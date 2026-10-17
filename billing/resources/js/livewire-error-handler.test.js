import { describe, it, expect, vi } from 'vitest';
import {
    FRIENDLY_ERROR_MESSAGE,
    parseErrorRef,
    buildFriendlyErrorText,
    handleLivewireError,
    registerLivewireErrorHandler,
} from './livewire-error-handler';

describe('parseErrorRef', () => {
    it('returns ref from JSON string body', () => {
        expect(parseErrorRef(JSON.stringify({ message: 'fail', ref: 'ABC123' }))).toBe('ABC123');
    });

    it('returns ref from object body', () => {
        expect(parseErrorRef({ ref: 'XYZ789' })).toBe('XYZ789');
    });

    it('returns null for invalid JSON', () => {
        expect(parseErrorRef('not-json')).toBeNull();
    });
});

describe('buildFriendlyErrorText', () => {
    it('includes reference when present', () => {
        const text = buildFriendlyErrorText(JSON.stringify({ ref: 'REF001' }));

        expect(text).toBe(`${FRIENDLY_ERROR_MESSAGE} Reference: REF001`);
    });

    it('falls back to generic message without ref', () => {
        expect(buildFriendlyErrorText('{}')).toBe(FRIENDLY_ERROR_MESSAGE);
    });
});

describe('handleLivewireError', () => {
    it('returns suppress payload for 500 errors', () => {
        const result = handleLivewireError(JSON.stringify({ ref: 'ERR500' }), 500);

        expect(result?.suppress).toBe(true);
        expect(result?.detail.title).toBe('error');
        expect(result?.detail.text).toContain('Reference: ERR500');
    });

    it('returns null for 419 session expiry', () => {
        expect(handleLivewireError('Session expired', 419)).toBeNull();
    });

    it('returns null for 404 not found', () => {
        expect(handleLivewireError('Not found', 404)).toBeNull();
    });
});

describe('registerLivewireErrorHandler', () => {
    it('dispatches toast and returns false for 500 errors', () => {
        const onToast = vi.fn();
        window.Livewire = { onError: vi.fn() };

        registerLivewireErrorHandler(onToast);

        const handler = window.Livewire.onError.mock.calls[0][0];
        const result = handler(JSON.stringify({ ref: 'LW500' }), 500);

        expect(onToast).toHaveBeenCalledWith({
            title: 'error',
            text: `${FRIENDLY_ERROR_MESSAGE} Reference: LW500`,
        });
        expect(result).toBe(false);
    });

    it('does not dispatch toast for non-500 errors', () => {
        const onToast = vi.fn();
        window.Livewire = { onError: vi.fn() };

        registerLivewireErrorHandler(onToast);

        const handler = window.Livewire.onError.mock.calls[0][0];
        const result = handler('Not found', 404);

        expect(onToast).not.toHaveBeenCalled();
        expect(result).toBeUndefined();
    });
});
