export const FRIENDLY_ERROR_MESSAGE =
    'Something went wrong. Please try again or contact support.';

export function parseErrorRef(message) {
    if (message == null || message === '') {
        return null;
    }

    try {
        const data = typeof message === 'string' ? JSON.parse(message) : message;

        return data?.ref ?? null;
    } catch {
        return null;
    }
}

export function buildFriendlyErrorText(message) {
    const ref = parseErrorRef(message);

    return ref
        ? `${FRIENDLY_ERROR_MESSAGE} Reference: ${ref}`
        : FRIENDLY_ERROR_MESSAGE;
}

export function handleLivewireError(message, statusCode) {
    if (statusCode < 500) {
        return null;
    }

    return {
        suppress: true,
        detail: {
            title: 'error',
            text: buildFriendlyErrorText(message),
        },
    };
}

export function registerLivewireErrorHandler(onToast = null) {
    const dispatch = onToast ?? ((detail) => {
        window.dispatchEvent(new CustomEvent('swal:info', { detail }));
    });

    const handler = (message, statusCode) => {
        const result = handleLivewireError(message, statusCode);

        if (!result) {
            return undefined;
        }

        dispatch(result.detail);

        return result.suppress ? false : undefined;
    };

    const register = () => {
        if (!window.Livewire) {
            return;
        }

        if (typeof window.Livewire.interceptRequest === 'function') {
            window.Livewire.interceptRequest(({ onError }) => {
                onError(({ response, responseBody, preventDefault }) => {
                    const status = response?.status ?? 500;
                    const result = handleLivewireError(responseBody, status);

                    if (!result) {
                        return;
                    }

                    dispatch(result.detail);

                    if (result.suppress) {
                        preventDefault();
                    }
                });
            });
            return;
        }

        if (window.Livewire.onError) {
            window.Livewire.onError(handler);
        }
    };

    register();
    document.addEventListener('livewire:init', register);

    return handler;
}
