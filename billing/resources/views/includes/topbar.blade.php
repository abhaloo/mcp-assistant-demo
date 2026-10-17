{{-- MCP Topbar — global chrome included from layouts/app.blade.php.
     Search controller: public/js/mcp-global-search.js
     Sidebar controller: public/js/mcp-sidebar.js --}}
<header class="mcp-topbar" role="banner">
    {{-- Sidebar toggle. Keeps .header-toggler so mcp-sidebar.js
         picks up the click without a JS edit. --}}
    <button class="mcp-topbar__icon-btn mcp-topbar__hamburger header-toggler"
            type="button"
            aria-label="Toggle navigation menu">
        <i class="fa fa-bars" aria-hidden="true"></i>
    </button>

    <span class="mcp-topbar__spacer" aria-hidden="true"></span>

    @auth
        @if (config('services.rag.enabled', false))
            <button type="button"
                    class="mcp-topbar__ask-ai"
                    data-mcp-ask-ai-toggle
                    aria-expanded="false"
                    aria-controls="mcpAskAiSidebar"
                    aria-keyshortcuts="Control+L"
                    aria-label="Open Ask Assistant panel (Control+L)">
                <svg class="mcp-topbar__ask-ai-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                    <path fill="currentColor" d="M12 2.2 14.35 9.65 21.8 12 14.35 14.35 12 21.8 9.65 14.35 2.2 12 9.65 9.65Z"/>
                </svg>
                <span class="mcp-topbar__ask-ai-label">Ask Assistant</span>
                <kbd class="mcp-topbar__ask-ai-kbd" aria-hidden="true" data-mcp-ask-ai-kbd>Ctrl+L</kbd>
            </button>
        @endif

        <div class="mcp-topbar__group">
            {{-- Help icon — placeholder. Functionality lands in a later phase. --}}
            <a href="#"
               class="mcp-topbar__icon-btn"
               role="button"
               aria-label="Help"
               data-help-stub>
                <i class="fa fa-circle-question" aria-hidden="true"></i>
            </a>

            <span class="mcp-topbar__divider" aria-hidden="true"></span>

            {{-- User chip — opens the user-dropdown partial via CoreUI. --}}
            <div class="dropdown">
                @php
                    $chipName = auth()->user()->name ?? 'User';
                    $chipInitials = strtoupper((string) str($chipName)
                        ->explode(' ')
                        ->take(2)
                        ->map(fn ($w) => str($w)->substr(0, 1))
                        ->implode(''));
                    // Prefer Spatie role, else department — never the generic "Member" fallback.
                    $chipRole = optional(auth()->user()->roles->first())->name
                        ?: optional(auth()->user()->department)->name;
                    if (is_string($chipRole)) {
                        $chipRole = trim($chipRole);
                        if ($chipRole === '' || strcasecmp($chipRole, 'Member') === 0) {
                            $chipRole = null;
                        } elseif (stripos($chipName, $chipRole) !== false) {
                            // Name already embeds the label (e.g. "Finance Team") — skip subtitle.
                            $chipRole = null;
                        }
                    } else {
                        $chipRole = null;
                    }
                @endphp
                <button type="button"
                        class="mcp-topbar__user"
                        data-coreui-toggle="dropdown"
                        aria-haspopup="menu"
                        aria-expanded="false">
                    <span class="avatar" aria-hidden="true">{{ $chipInitials }}</span>
                    <span class="meta">
                        <span class="name">{{ $chipName }}</span>
                        @if ($chipRole)
                            <span class="role">{{ $chipRole }}</span>
                        @endif
                    </span>
                    <i class="caret fa fa-chevron-down" aria-hidden="true"></i>
                </button>

                @include('includes.partials.user-dropdown')
            </div>
        </div>
    @endauth
</header>

@once
@push('scripts')
<script>
    (function () {
        document.querySelectorAll('[data-help-stub]').forEach(function (el) {
            el.addEventListener('click', function (e) { e.preventDefault(); });
        });
    })();
</script>
@endpush
@endonce
