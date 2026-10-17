@php($isPriority = $work_order->priority === 'high' && $work_order->department_id != 12)
<tr wire:key="work-order-{{ $work_order->id }}"
    @class(['mcp-row--priority' => $isPriority])
    @if(in_array($contextVariant ?? 'index', ['index', 'customer', 'bill'], true)) data-mcp-context-job-id="{{ $work_order->id }}" @endif>
    <td>
        <div class="mcp-stack">
            <a href="{{ route('work-order.show', $work_order) }}" class="mcp-id-link">
                #{{ $work_order->work_number ?? $work_order->id }}
            </a>
            @if($isPriority)
                <x-mcp.status kind="danger" size="sm" label="HIGH PRIORITY" />
            @endif
        </div>
    </td>
    <td>
        @if($work_order->customerOrder)
            <x-mcp.order-chip :order="$work_order->customerOrder" />
        @else
            <span class="mcp-id-none">None</span>
        @endif
    </td>
    <td>
        @if($work_order->bill)
            <x-mcp.bill-chip :bill="$work_order->bill" :href="auth()->user()?->canany(['view invoice', 'view quotation']) ? route('bill.show', $work_order->bill) : null" />
        @else
            <span class="mcp-id-none">None</span>
        @endif
    </td>
    @if($bill ?? false)
        <td class="mcp-cell-strong"><div class="mcp-truncate" title="{{ $work_order->title }}">{{ $work_order->title }}</div></td>
    @else
        <td class="mcp-cell-strong"><div class="mcp-truncate" title="{{ $work_order->customer->name ?? '-' }}">{{ $work_order->customer->name ?? '-' }}</div></td>
        <td><div class="mcp-truncate mcp-td--mute" title="{{ $work_order->title }}">{{ $work_order->title }}</div></td>
    @endif
    <td>
        <x-mcp.status :kind="$work_order->statusKind()" size="sm" :label="$work_order->statusLabel()" />
    </td>
    <td><x-mcp.dept-badge :department="$work_order->department" /></td>
    <td class="col-created mcp-td--mono mcp-td--secondary">{{ $work_order->created_at->format('d M, y') }}</td>
    <td class="col-updated mcp-td--mono mcp-td--secondary">{{ $work_order->updated_at->format('d M, y') }}</td>
    <td class="col-creator mcp-td--mute">{{ $work_order->user?->short_name ?? '—' }}</td>
    <td class="mcp-td--right">
        <div class="mcp-row-actions">
            <x-mcp.icon-btn variant="primary" icon="fa-eye" :href="route('work-order.show', $work_order)" title="View" label="View" />

            @can('move job')
                @if($work_order->status == 'PENDING MOVE APPROVAL')
                    <x-mcp.icon-btn variant="danger" icon="fa-times" title="Cancel Move Request" label="Cancel"
                        data-coreui-toggle="modal" :data-coreui-target="'#modal-cancel-move-'.$work_order->id" />
                    <x-modal.confirm id="modal-cancel-move-{{ $work_order->id }}"
                        title="Cancel Move Request" method="cancelMoveRequest({{ $work_order->id }})"
                        confirmClass="btn-danger" confirmIcon="fa-times" confirmText="Cancel Request">
                        Are you sure you want to cancel the move request for job
                        <strong>#{{ $work_order->work_number ?? $work_order->id }}</strong>?
                    </x-modal.confirm>
                @else
                    <x-mcp.icon-btn icon="fa-edit" title="Move / edit" label="Move"
                        wire:click="editJob({{ $work_order->id }})"
                        data-coreui-toggle="modal" data-coreui-target="#modal-work_order" />
                @endif
            @endcan

            @can('add job')
                <x-mcp.icon-btn variant="primary" icon="fa-copy" title="Copy Job" label="Copy"
                    wire:click="openCopyModal({{ $work_order->id }})"
                    data-coreui-toggle="modal" data-coreui-target="#modal-copy-job" />
            @endcan

            @can('delete job')
                <x-mcp.icon-btn variant="danger" icon="fa-trash" title="Delete Job" label="Delete"
                    wire:click="deleteConfirm({{ $work_order->id }})"
                    data-coreui-toggle="modal" data-coreui-target="#modal-delete-job" />
            @endcan
        </div>
    </td>
</tr>
