<div @class(['nav-item dropdown', 'show' => $open])
     data-no-progress
     wire:poll.{{ $pollSeconds }}s="refreshCount">
    {{-- Bell trigger — CoreUI dropdown is driven manually so the panel opens only
         after openPanel() returns notifications (avoids push-prompt / empty-state flash).
         The inner <span class="notification-bell-active"> is the Dusk test hook. --}}
    <a @class(['mcp-bell', 'show' => $open])
       data-coreui-toggle="dropdown"
       data-coreui-display="static"
       data-coreui-placement="bottom-end"
       data-enable-realtime-notifications
       data-no-progress
       href="#"
       role="button"
       aria-haspopup="true"
       aria-expanded="{{ $open ? 'true' : 'false' }}"
       aria-label="Notifications">
        <span class="mcp-bell__icon {{ $unreadCount > 0 ? 'notification-bell-active' : '' }}">
            <i class="fa fa-bell" aria-hidden="true"></i>
        </span>
        @if($unreadCount > 0)
            <span class="mcp-bell__count">{{ $unreadCount > 9 ? '9+' : $unreadCount }}</span>
        @endif
    </a>

    {{-- Dropdown card — `mcp` class is added so children scoped under .mcp
         (icon-btn §5, empty §13) resolve when CoreUI mounts the menu in place. --}}
    <div @class(['dropdown-menu dropdown-menu-end mcp mcp-notif-card', 'show' => $open])>
        <div class="mcp-notif-header">
            <span class="mcp-notif-header__title">Notifications</span>
            <div class="mcp-notif-header__actions">
                @if($unreadCount > 0)
                    <x-mcp.icon-btn icon="fa-check-double"
                                    title="Mark all read"
                                    wire:click.stop="markAllAsRead" />
                @endif
                @if($this->notifications->count() > 0)
                    <x-mcp.icon-btn icon="fa-trash"
                                    variant="danger"
                                    title="Clear all"
                                    wire:click.stop="clearAll" />
                @endif
            </div>
        </div>

        <div class="mcp-notif-list">
            @forelse($this->notifications as $notification)
                @php
                    $notifTitle = $notification->data['title'] ?? 'Notification';
                    $notifBadgeColors = match(true) {
                        str_contains($notifTitle, 'Approval Required') => \App\Helpers\BadgeHelper::palette('warning'),
                        str_contains($notifTitle, 'Approved')          => \App\Helpers\BadgeHelper::palette('success'),
                        str_contains($notifTitle, 'Rejected')          => \App\Helpers\BadgeHelper::palette('danger'),
                        str_contains($notifTitle, 'Moved')             => \App\Helpers\BadgeHelper::palette('indigo'),
                        str_contains($notifTitle, 'Invoice')           => \App\Helpers\BadgeHelper::palette('info'),
                        default                                        => \App\Helpers\BadgeHelper::palette('light'),
                    };
                    $newBadgeColors  = \App\Helpers\BadgeHelper::palette('info');
                    $readBadgeColors = \App\Helpers\BadgeHelper::palette('light');
                @endphp

                <div @class([
                    'mcp-notif-item',
                    'is-unread' => is_null($notification->read_at),
                ])>
                    <button wire:click.stop.prevent="dismiss('{{ $notification->id }}')"
                            class="mcp-notif-item__dismiss"
                            type="button"
                            aria-label="Dismiss notification">
                        <i class="fa fa-times" aria-hidden="true"></i>
                    </button>
                    <a href="{{ route('notifications.redirect', $notification->id) }}"
                       class="mcp-notif-item__link">
                        <div class="mcp-notif-item__row1">
                            <span class="notif-badge"
                                  style="background-color: {{ $notifBadgeColors['bg'] }}; color: {{ $notifBadgeColors['text'] }};">
                                {{ $notifTitle }}
                            </span>
                            @if(is_null($notification->read_at))
                                <span class="notif-badge"
                                      style="background-color: {{ $newBadgeColors['bg'] }}; color: {{ $newBadgeColors['text'] }};">New</span>
                            @else
                                <span class="notif-badge"
                                      style="background-color: {{ $readBadgeColors['bg'] }}; color: {{ $readBadgeColors['text'] }};">Read</span>
                            @endif
                        </div>
                        <div class="mcp-notif-item__message">{{ $notification->data['message'] ?? '' }}</div>
                        <div class="mcp-notif-item__meta">
                            {{ $notification->created_at->diffForHumans() }}
                            @if($actor = $notification->data['actor'] ?? null)
                                · by {{ $actor['name'] }}
                            @endif
                        </div>
                    </a>
                </div>
            @empty
                <x-mcp.empty title="All caught up"
                             hint="No new activity to review."
                             icon="fa-bell-slash" />
            @endforelse
        </div>

        {{-- Push prompt is revealed only after the panel opens (see @push script). --}}
        @if($open)
        <div id="push-prompt" class="mcp-notif-footer d-none" wire:ignore.self>
            <small class="mcp-notif-footer__hint">Get real-time push notifications</small>
            <button onclick="enablePush(this)" class="btn btn-sm btn-primary">Enable</button>
            <button onclick="dismissPushPrompt()" class="btn btn-sm btn-link text-muted">Not now</button>
        </div>
        @endif
    </div>

    @once
    @push('scripts')
    <script>
        (function () {
            var bell = document.querySelector('[data-enable-realtime-notifications]');
            if (!bell || bell.dataset.notifPanelBound) {
                return;
            }
            bell.dataset.notifPanelBound = '1';

            var dropdown = null;

            function getDropdown() {
                if (!dropdown && window.coreui) {
                    dropdown = window.coreui.Dropdown.getOrCreateInstance(bell, { display: 'static' });
                }
                return dropdown;
            }

            function maybeShowPushPrompt() {
                if (!window.PushNotifications?.isPushSupported()) return;
                if (localStorage.getItem('push_prompt_dismissed')) return;

                window.PushNotifications.isPushSubscribed().then(function (subscribed) {
                    if (!subscribed) {
                        var el = document.getElementById('push-prompt');
                        if (el) el.classList.remove('d-none');
                    }
                });
            }

            bell.addEventListener('click', function (e) {
                // Keep CoreUI's outside-click dismiss (clearMenus scans
                // [data-coreui-toggle="dropdown"]) but defer open until
                // notifications are loaded.
                e.preventDefault();
                e.stopPropagation();

                var instance = getDropdown();
                if (!instance) return;

                if (bell.classList.contains('show')) {
                    instance.hide();
                    return;
                }

                @this.call('openPanel').then(function () {
                    instance.show();
                    maybeShowPushPrompt();
                });
            }, true);

            bell.addEventListener('hidden.coreui.dropdown', function () {
                @this.call('closePanel');
            });
        })();

        function enablePush(btn) {
            btn.disabled = true;
            btn.textContent = 'Enabling...';
            window.PushNotifications.subscribe().then(function (result) {
                if (result.ok) {
                    document.getElementById('push-prompt').classList.add('d-none');
                } else {
                    btn.disabled = false;
                    btn.textContent = 'Enable';
                    if (result.reason === 'permission_denied') {
                        btn.parentElement.querySelector('small').textContent = 'Permission denied. Check browser settings.';
                    }
                }
            });
        }

        function dismissPushPrompt() {
            document.getElementById('push-prompt').classList.add('d-none');
            localStorage.setItem('push_prompt_dismissed', '1');
        }
    </script>
    @endpush
    @endonce
</div>
