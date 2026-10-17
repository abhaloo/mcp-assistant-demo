/**
 * Push notification subscription manager.
 * Called from the notification bell prompt, not on page load.
 */
export async function subscribeToPush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        return { ok: false, reason: 'not_supported' };
    }

    if (isIOSSafari() && !window.navigator.standalone) {
        return { ok: false, reason: 'ios_not_installed' };
    }

    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
        return { ok: false, reason: 'permission_denied' };
    }

    try {
        const registration = await navigator.serviceWorker.ready;
        const vapidKey = document.querySelector('meta[name="vapid-public-key"]')?.content;

        if (!vapidKey) {
            return { ok: false, reason: 'no_vapid_key' };
        }

        const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(vapidKey)
        });

        const response = await fetch('/push/subscribe', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content,
                'Accept': 'application/json'
            },
            body: JSON.stringify(subscription)
        });

        if (!response.ok) {
            return { ok: false, reason: 'server_error' };
        }

        return { ok: true };
    } catch (err) {
        console.error('Push subscription failed:', err);
        return { ok: false, reason: 'error' };
    }
}

export function isPushSupported() {
    return 'serviceWorker' in navigator && 'PushManager' in window;
}

export async function isPushSubscribed() {
    if (!isPushSupported()) return false;
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    return subscription !== null;
}

function isIOSSafari() {
    const ua = navigator.userAgent;
    return /iPad|iPhone|iPod/.test(ua) && /WebKit/.test(ua) && !/CriOS/.test(ua);
}

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map(char => char.charCodeAt(0)));
}
