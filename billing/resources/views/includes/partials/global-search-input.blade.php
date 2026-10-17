{{-- Global search — STYLED SHELL ONLY (superseded).
     Replaced by @livewire('global-search') in topbar.blade.php.
     Kept for reference; CSS hooks (.mcp-topbar__search*) remain in mcp-system.css. --}}
<div class="mcp-topbar__search">
    {{-- Mobile-only icon trigger; CSS hides on ≥768px. JS toggles .is-open.
         Two icons swap based on .is-open: search when collapsed, × when open. --}}
    <button type="button"
            class="mcp-topbar__search-trigger"
            aria-label="Open search"
            aria-expanded="false">
        <i class="fa fa-search mcp-topbar__search-trigger-icon mcp-topbar__search-trigger-icon--open" aria-hidden="true"></i>
        <i class="fa fa-times mcp-topbar__search-trigger-icon mcp-topbar__search-trigger-icon--close" aria-hidden="true"></i>
    </button>

    <label for="mcp-global-search" class="visually-hidden">Global search</label>
    <i class="fa fa-search mcp-topbar__search-icon" aria-hidden="true"></i>
    <input id="mcp-global-search"
           type="search"
           class="mcp-topbar__search-input"
           placeholder="Search jobs, orders, customers…"
           autocomplete="off"
           aria-describedby="mcp-search-hint">
    <span id="mcp-search-hint" class="visually-hidden">Search is in development — results coming soon.</span>
    <button type="button"
            class="mcp-topbar__search-clear"
            aria-label="Clear search"
            tabindex="-1">
        <i class="fa fa-times" aria-hidden="true"></i>
    </button>
</div>
