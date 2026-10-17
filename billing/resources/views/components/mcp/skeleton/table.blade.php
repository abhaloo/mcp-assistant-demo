@props([
    'rows' => 5,
    'cols' => 4,
    'widths' => [],
])

@php
    $rows = max(1, (int) $rows);
    $cols = max(1, (int) $cols);
    $widths = is_array($widths) ? $widths : [];
@endphp

<div {{ $attributes->class('mcp-skeleton-table') }} style="--mcp-skeleton-cols: {{ $cols }};" aria-hidden="true">
    <div class="mcp-skeleton-table__head">
        <div class="mcp-skeleton-table__row">
            @for ($col = 0; $col < $cols; $col++)
                @php
                    $width = $widths[$col] ?? 'w75';
                @endphp
                <div class="mcp-skeleton-table__cell mcp-skeleton-table__cell--head">
                    <x-mcp.skeleton.line :width="$width" />
                </div>
            @endfor
        </div>
    </div>

    <div class="mcp-skeleton-table__body">
        @for ($row = 0; $row < $rows; $row++)
            <div class="mcp-skeleton-table__row">
                @for ($col = 0; $col < $cols; $col++)
                    @php
                        $width = $widths[$col] ?? ($col === 0 ? 'w50' : 'w75');
                    @endphp
                    <div class="mcp-skeleton-table__cell">
                        <x-mcp.skeleton.line :width="$width" />
                    </div>
                @endfor
            </div>
        @endfor
    </div>
</div>
