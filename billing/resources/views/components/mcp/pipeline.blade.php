@props(['works'])

@php
    $total = $works->sum('count');
    $terminalStatuses = ['FINISHED', 'CANCELLED'];

    $segmentClass = fn (string $status) => match (\App\Models\WorkOrder::statusKindFor($status)) {
        'success' => 'mcp-pipe__seg--success',
        'danger' => 'mcp-pipe__seg--danger',
        'warning' => 'mcp-pipe__seg--warning',
        'info' => 'mcp-pipe__seg--info',
        'sky' => 'mcp-pipe__seg--sky',
        'violet' => 'mcp-pipe__seg--violet',
        'primary' => 'mcp-pipe__seg--primary',
        default => 'mcp-pipe__seg--neutral',
    };
@endphp

@if($total > 0)
    <div class="mcp-pipe" aria-label="Jobs by production status">
        @foreach($works as $work)
            @if($work->count > 0)
                @php
                    $statusLabel = \App\Models\WorkOrder::statusLabelFor($work->status);
                    $isTerminal = in_array($work->status, $terminalStatuses, true);
                @endphp
                <a href="{{ route('work-order.index', ['filter_status' => $work->status]) }}"
                   class="mcp-pipe__seg {{ $segmentClass($work->status) }} @if($isTerminal) mcp-pipe__seg--terminal @endif text-decoration-none"
                   style="--mcp-segment-flex: {{ $work->count }}"
                   aria-label="{{ $statusLabel }}: {{ $work->count }} {{ $work->count === 1 ? 'job' : 'jobs' }}"
                   title="{{ $statusLabel }}">
                    <span class="mcp-pipe__seg-count" aria-hidden="true">{{ $work->count }}</span>
                    <span class="mcp-pipe__seg-label">{{ $statusLabel }}</span>
                </a>
            @endif
        @endforeach
    </div>
    <div class="mcp-pipe-legend" aria-hidden="true">
        @foreach($works as $work)
            @if($work->count > 0)
                @php
                    $statusLabel = \App\Models\WorkOrder::statusLabelFor($work->status);
                    $isTerminal = in_array($work->status, $terminalStatuses, true);
                @endphp
                <span class="@if($isTerminal) mcp-pipe-legend__item--terminal @endif">
                    <i class="{{ $segmentClass($work->status) }}"></i>{{ $statusLabel }}
                </span>
            @endif
        @endforeach
    </div>
@else
    <x-mcp.empty title="No jobs yet"
        hint="Production jobs will appear here grouped by status.">
        <x-slot:action>
            <x-mcp.btn variant="primary" size="sm" icon="fa-clock" :href="route('work-order.index')">View jobs</x-mcp.btn>
        </x-slot:action>
    </x-mcp.empty>
@endif
