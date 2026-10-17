import './bootstrap';
import { subscribeToPush, isPushSupported, isPushSubscribed } from './push-notifications';
import { initLoadingIndicator } from './loading-indicator';
import { registerLivewireErrorHandler } from './livewire-error-handler';

window.PushNotifications = { subscribe: subscribeToPush, isPushSupported, isPushSubscribed };

let echoInitialized = false;

async function initEcho(userId) {
    const [{ default: Echo }, { default: Pusher }] = await Promise.all([
        import('laravel-echo'),
        import('pusher-js'),
    ]);

    window.Pusher = Pusher;
    window.Echo = new Echo({
        broadcaster: 'pusher',
        key: import.meta.env.VITE_PUSHER_APP_KEY,
        cluster: import.meta.env.VITE_PUSHER_APP_CLUSTER || 'mt1',
        wsHost: import.meta.env.VITE_PUSHER_HOST || `ws-${import.meta.env.VITE_PUSHER_APP_CLUSTER}.pusher.com`,
        wsPort: import.meta.env.VITE_PUSHER_PORT || 80,
        wssPort: import.meta.env.VITE_PUSHER_PORT || 443,
        forceTLS: (import.meta.env.VITE_PUSHER_SCHEME || 'https') === 'https',
        enabledTransports: ['ws', 'wss'],
    });

    window.Echo.private('App.Models.User.' + userId)
        .notification(function (notification) {
            window.dispatchEvent(new CustomEvent('notification:toast', {
                detail: {
                    title: notification.title || 'Notification',
                    message: notification.message || '',
                    url: notification.url || null,
                    actor: notification.actor || null
                }
            }));
            if (window.Livewire) {
                window.Livewire.dispatch('notificationReceived');
            }
        });
}

function maybeInitEcho() {
    const userId = document.querySelector('meta[name="user-id"]')?.content;
    if (!userId || echoInitialized) {
        return;
    }

    echoInitialized = true;
    initEcho(userId).catch(err => console.warn('Echo init failed:', err));
}

document.addEventListener('livewire:init', function () {
    registerLivewireErrorHandler();
});

document.addEventListener('DOMContentLoaded', function () {
    initLoadingIndicator();

    const trigger = document.querySelector('[data-enable-realtime-notifications]');
    if (!trigger) {
        return;
    }

    trigger.addEventListener('shown.coreui.dropdown', maybeInitEcho);
});
