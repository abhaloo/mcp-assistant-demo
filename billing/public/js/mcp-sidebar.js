/*
 * MCP Sidebar controller — vanilla IIFE, no build step required.
 * ─────────────────────────────────────────────────────────────────────────────
 * Owns: collapse persistence, group toggle persistence, pinning (localStorage),
 *       live search filter, keyboard shortcuts (Cmd/Ctrl+K, Cmd/Ctrl+B),
 *       mobile open/close (replaces CoreUI's Sidebar instance).
 *
 * Future mcp-* component controllers should follow the same pattern:
 * single file at public/js/mcp-{name}.js, IIFE-scoped, attaches to DOM by id/data.
 */
(function () {
    const sidebar = document.getElementById('mcpSidebar');
    if (!sidebar) return;

    const LS_COLLAPSED = 'mcp.sidebar.collapsed';
    const LS_PINNED    = 'mcp.sidebar.pinned';
    const LS_GROUPS    = 'mcp.sidebar.groupsClosed';

    /**
     * Keep the toggle's accessible name and expanded state in step with the
     * sidebar. Which arrow is painted is CSS's job (.is-collapsed); this owns
     * the semantics so the control never announces the wrong state.
     */
    function syncCollapseBtn(collapsed) {
        const btn = document.getElementById('mcpCollapseBtn');
        if (!btn) return;
        // On mobile the sidebar is a sheet, so the control closes it rather than
        // collapsing to a rail — and a sheet that is on screen is never collapsed.
        const mobile = window.matchMedia('(max-width: 767.98px)').matches;
        const label = mobile ? 'Close menu' : (collapsed ? 'Expand sidebar' : 'Collapse sidebar');
        btn.setAttribute('aria-expanded', collapsed && !mobile ? 'false' : 'true');
        btn.setAttribute('aria-label', label);
        btn.setAttribute('title', mobile ? label : label + ' (Ctrl+B)');
    }

    // wire:navigate re-executes layout scripts; @persist keeps the same sidebar DOM.
    // Re-binding would stack click handlers and make Collapse toggle twice (no-op).
    function syncBodyOffsetFromSidebar() {
        const collapsed = sidebar.classList.contains('is-collapsed')
            || localStorage.getItem(LS_COLLAPSED) === '1';
        document.body.classList.add('mcp-shifted');
        document.body.classList.toggle('mcp-sidebar-collapsed', collapsed);
        sidebar.classList.toggle('is-collapsed', collapsed);
        syncCollapseBtn(collapsed);
    }
    if (sidebar.dataset.mcpController === '1') {
        syncBodyOffsetFromSidebar();
        return;
    }

    function parseStoredJsonArray(key) {
        try {
            const parsed = JSON.parse(localStorage.getItem(key) || '[]');
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }

    try {
        const search       = document.getElementById('mcpSearch');
        const nav          = document.getElementById('mcpNav');
        const empty        = document.getElementById('mcpEmpty');
        const collapseBtn  = document.getElementById('mcpCollapseBtn');
        const pinnedHost   = document.getElementById('mcpPinned');
        const pinnedGroup  = nav.querySelector('[data-group="pinned"]');

        // Inject backdrop element once for mobile overlay
        const backdrop = document.createElement('div');
        backdrop.className = 'mcp-backdrop';
        document.body.appendChild(backdrop);

        // ── Collapsed state (desktop full ↔ rail) ───────────────────────────────
        function applyCollapsed(c) {
            sidebar.classList.toggle('is-collapsed', c);
            document.body.classList.toggle('mcp-sidebar-collapsed', c);
            document.body.classList.add('mcp-shifted');
            syncCollapseBtn(c);
        }
        applyCollapsed(localStorage.getItem(LS_COLLAPSED) === '1');
        collapseBtn.addEventListener('click', () => {
            // On mobile the sidebar is a sheet; "Collapse" closes the sheet.
            // (The desktop collapse-to-rail concept doesn't apply here.)
            if (window.matchMedia('(max-width: 767.98px)').matches) {
                sidebar.classList.remove('is-open');
                backdrop.classList.remove('is-visible');
                return;
            }
            const next = !sidebar.classList.contains('is-collapsed');
            localStorage.setItem(LS_COLLAPSED, next ? '1' : '0');
            applyCollapsed(next);
        });
        // Crossing the mobile breakpoint changes what the control does.
        window.addEventListener('resize', () => {
            syncCollapseBtn(sidebar.classList.contains('is-collapsed'));
        });

        // ── Mobile open/close (replaces CoreUI's Sidebar instance) ──────────────
        const isMobile = () => window.matchMedia('(max-width: 767.98px)').matches;
        function setMobileOpen(open) {
            sidebar.classList.toggle('is-open', open);
            backdrop.classList.toggle('is-visible', open);
        }
        document.querySelector('.header-toggler')?.addEventListener('click', e => {
            e.preventDefault();
            if (isMobile()) {
                setMobileOpen(!sidebar.classList.contains('is-open'));
            } else {
                collapseBtn.click();
            }
        });
        backdrop.addEventListener('click', () => setMobileOpen(false));
        // Close mobile drawer when navigating (full reload or Livewire wire:navigate)
        nav.addEventListener('click', e => {
            if (isMobile() && e.target.closest('.mcp-row') && !e.target.closest('.mcp-pin')) {
                setMobileOpen(false);
            }
        });
        document.addEventListener('livewire:navigated', () => {
            // Layout morph can drop body offset classes; persisted sidebar state is source of truth.
            syncBodyOffsetFromSidebar();
            if (isMobile()) {
                setMobileOpen(false);
            }
        });

        // ── Group collapse persistence ──────────────────────────────────────────
        let groupsClosed = parseStoredJsonArray(LS_GROUPS);
        nav.querySelectorAll('.mcp-group:not(.mcp-group--pinned)').forEach(g => {
            const key = g.dataset.group;
            if (groupsClosed.includes(key)) g.classList.add('is-closed');
        });
        nav.addEventListener('click', e => {
            const head = e.target.closest('[data-toggle-group]');
            if (!head) return;
            const group = head.closest('.mcp-group');
            group.classList.toggle('is-closed');
            const closedNow = nav.querySelectorAll('.mcp-group.is-closed:not(.mcp-group--pinned)');
            groupsClosed = Array.from(closedNow).map(g => g.dataset.group);
            localStorage.setItem(LS_GROUPS, JSON.stringify(groupsClosed));
        });

        // ── Pinned ──────────────────────────────────────────────────────────────
        let pinned = parseStoredJsonArray(LS_PINNED);

        function rerenderPinned() {
            pinnedHost.innerHTML = '';
            pinnedGroup.hidden = !pinned.length;

            pinned.forEach(id => {
                const original = nav.querySelector('.mcp-row[data-id="' + id + '"]:not([data-pin-clone])');
                if (!original) return;
                const clone = original.cloneNode(true);
                clone.setAttribute('data-pin-clone', '1');
                clone.style.display = '';
                clone.querySelector('.mcp-pin')?.classList.add('is-pinned');
                pinnedHost.appendChild(clone);
            });

            // Hide pinned items from their original group (they're shown in Pinned instead);
            // restore visibility on unpin. Also sync the pin-icon state on each original.
            // NOTE: when the search feature is re-enabled, its filter must respect this state
            // (don't re-show pinned originals when clearing the query).
            nav.querySelectorAll('.mcp-row:not([data-pin-clone])').forEach(row => {
                const id = row.dataset.id;
                const isPinned = pinned.includes(id);
                row.style.display = isPinned ? 'none' : '';
                const btn = row.querySelector('.mcp-pin');
                if (btn) btn.classList.toggle('is-pinned', isPinned);
            });
        }
        function togglePin(id) {
            pinned = pinned.includes(id) ? pinned.filter(x => x !== id) : [...pinned, id];
            localStorage.setItem(LS_PINNED, JSON.stringify(pinned));
            rerenderPinned();
        }
        nav.addEventListener('click', e => {
            const pinBtn = e.target.closest('.mcp-pin');
            if (!pinBtn) return;
            e.preventDefault();
            e.stopPropagation();
            const row = pinBtn.closest('.mcp-row');
            if (row) togglePin(row.dataset.id);
        });
        rerenderPinned();

        // ── Search (only when the search input is present in the DOM) ───────────
        if (search) {
            function applySearch(q) {
                q = q.trim().toLowerCase();
                const groups = nav.querySelectorAll('.mcp-group:not(.mcp-group--pinned)');
                if (!q) {
                    groups.forEach(g => { g.style.display = ''; });
                    if (pinnedGroup) pinnedGroup.style.display = '';
                    nav.querySelectorAll('.mcp-row').forEach(r => r.style.display = '');
                    if (empty) empty.hidden = true;
                    return;
                }
                if (pinnedGroup) pinnedGroup.style.display = 'none';
                let anyMatch = false;
                groups.forEach(g => {
                    let groupHasMatch = false;
                    g.querySelectorAll('.mcp-row').forEach(row => {
                        const match = row.dataset.label?.toLowerCase().includes(q);
                        row.style.display = match ? '' : 'none';
                        if (match) { groupHasMatch = true; anyMatch = true; }
                    });
                    g.style.display = groupHasMatch ? '' : 'none';
                    if (groupHasMatch) g.classList.remove('is-closed');
                });
                if (empty) empty.hidden = anyMatch;
            }
            search.addEventListener('input', e => applySearch(e.target.value));
        }

        // ── Keyboard shortcuts ──────────────────────────────────────────────────
        document.addEventListener('keydown', e => {
            const mod = e.metaKey || e.ctrlKey;
            if (mod && e.key.toLowerCase() === 'k' && search && !document.getElementById('mcp-global-search')) {
                e.preventDefault();
                search.focus();
                search.select();
            }
            if (mod && e.key.toLowerCase() === 'b') {
                e.preventDefault();
                collapseBtn.click();
            }
            if (e.key === 'Escape' && search && document.activeElement === search) {
                search.value = '';
                search.dispatchEvent(new Event('input'));
                search.blur();
            }
        });

        // Reveal once boot is done
        sidebar.removeAttribute('data-booting');

        // ── Active nav sync (persist-safe; replaces wire:current) ───────────────
        // @persist reuses sidebar DOM across wire:navigate, so Blade `active` only
        // applies on full load. Server emits #mcp-sidebar-active-ids outside persist.
        function syncSidebarActiveFromBeacon() {
            const beacon = document.getElementById('mcp-sidebar-active-ids');
            if (!beacon) {
                return;
            }
            let ids = [];
            try {
                ids = JSON.parse(beacon.textContent || '[]');
            } catch (_) {
                ids = [];
            }
            if (!Array.isArray(ids)) {
                ids = [];
            }
            nav.querySelectorAll('.mcp-row').forEach(row => {
                const active = ids.includes(row.dataset.id);
                row.classList.toggle('active', active);
                if (active) {
                    row.setAttribute('aria-current', 'page');
                } else {
                    row.removeAttribute('aria-current');
                }
            });
        }
        syncSidebarActiveFromBeacon();
        document.addEventListener('livewire:navigated', () => syncSidebarActiveFromBeacon());

        // Mark initialized only after the full controller is wired so a mid-init
        // throw can retry on the next layout script run (wire:navigate).
        sidebar.dataset.mcpController = '1';
    } catch (err) {
        delete sidebar.dataset.mcpController;
        console.error('mcp-sidebar init failed', err);
    }
})();
