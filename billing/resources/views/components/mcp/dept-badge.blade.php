@props([
    'department' => null,
])

{{-- Inline style is intentional: department palette is runtime-driven across
     ~16 entries, not feasible to enumerate as static CSS classes. --}}
@if ($department)
    @php($palette = $department->getBadgeColor())
    <span class="mcp-dept" style="background: {{ $palette['bg'] }}; color: {{ $palette['text'] }};">
        {{ $department->name }}
    </span>
@else
    <span class="mcp-dept mcp-dept--unassigned">Unassigned</span>
@endif
