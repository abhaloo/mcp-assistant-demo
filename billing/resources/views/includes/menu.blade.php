{{--
    MCP Sidebar — modern light-theme navigation.

    Menu structure and authorization are precomputed once per request by
    App\Actions\Sidebar\BuildSidebarMenu (see AppServiceProvider).
    Behavior controller: public/js/mcp-sidebar.js.
--}}
@php($sidebarMenu = $sidebarMenu ?? ['quickActions' => [], 'groups' => []])

<div class="mcp-sidebar" id="mcpSidebar" data-booting>

    {{-- Workspace pod --}}
    <div class="mcp-workspace">
        <div class="mcp-logo"><img src="{{ asset('img/logo/bloomfield.svg') }}" alt="Bloomfield Print Works"></div>
        <div class="mcp-workspace-text">
            <div class="mcp-workspace-title">{{ auth()->user()->entity->name ?? 'Bloomfield Print Works' }}</div>
        </div>
        {{-- Sidebar toggle. Which arrow shows is CSS (.is-collapsed); the label and
             aria-expanded are kept in step by mcp-sidebar.js. --}}
        <button type="button" class="mcp-collapse-btn" id="mcpCollapseBtn"
                aria-controls="mcpSidebar" aria-expanded="true"
                aria-label="Collapse sidebar" title="Collapse sidebar (Ctrl+B)">
            <i class="fa fa-angles-left" aria-hidden="true"></i>
            <i class="fa fa-angles-right" aria-hidden="true"></i>
        </button>
    </div>

    @if ($sidebarMenu['quickActions'] !== [])
        <div class="mcp-quick">
            @foreach ($sidebarMenu['quickActions'] as $action)
                <a @class(['mcp-cta', 'mcp-cta--primary' => $action['primary']])
                   href="{{ $action['href'] }}"
                   wire:navigate.hover>
                    <i class="fa {{ $action['icon'] }}" aria-hidden="true"></i> {{ $action['label'] }}
                </a>
            @endforeach
        </div>
    @endif

    {{-- Nav --}}
    <nav class="mcp-nav" id="mcpNav">

        {{-- Pinned (rendered by JS from localStorage) --}}
        <div class="mcp-group mcp-group--pinned" data-group="pinned" hidden>
            <div class="mcp-group-head">
                <i class="fa fa-thumbtack" aria-hidden="true"></i>
                <span>Pinned</span>
            </div>
            <div class="mcp-group-body" id="mcpPinned"></div>
        </div>

        @foreach ($sidebarMenu['groups'] as $group)
            <div @class(['mcp-group', 'is-closed' => $group['closed']]) data-group="{{ $group['key'] }}">
                <button type="button" class="mcp-group-head" data-toggle-group>
                    <span>{{ $group['label'] }}</span>
                    <span class="mcp-chev"><i class="fa fa-chevron-down" aria-hidden="true"></i></span>
                </button>
                <div class="mcp-group-body">
                    @foreach ($group['items'] as $item)
                        <a @class(['mcp-row', 'active' => $item['active']])
                           data-id="{{ $item['id'] }}"
                           data-label="{{ $item['label'] }}"
                           data-tip="{{ $item['label'] }}"
                           href="{{ $item['href'] }}"
                           wire:navigate.hover>
                            <span class="mcp-row-icon"><i class="fa {{ $item['icon'] }}" aria-hidden="true"></i></span>
                            <span class="mcp-row-label">{{ $item['label'] }}</span>
                            <button type="button" class="mcp-pin" aria-label="Pin">
                                <i class="fa fa-thumbtack" aria-hidden="true"></i>
                            </button>
                        </a>
                    @endforeach
                </div>
            </div>
        @endforeach

        {{-- Empty state for search --}}
        <div class="mcp-empty" id="mcpEmpty" hidden>No matches</div>
    </nav>
</div>
