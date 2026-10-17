{{-- Sticky bottom action bar --}}
<div class="d-flex flex-wrap gap-2 py-3">
    {{-- Task P7 — "Ask about this bill" (mobile parity with the desktop
         action bar): identifiers only, in escaped data attributes. Delegated
         click handling lives in public/js/mcp-ask-ai.js; no inline JS here. --}}
    @if($ragEnabled && $recordContextEnabled && $recordResourceType)
        @php
            $askBillLabel = ($bill->invoice_number ?: '#'.$bill->id).' ('.$bill->type.')';
        @endphp
        <x-mcp.btn variant="secondary" size="sm" icon="fa-wand-magic-sparkles" class="flex-fill"
            data-mcp-ask-ai-record-trigger
            data-record-resource-type="{{ $recordResourceType }}"
            data-record-id="{{ $bill->id }}"
            data-record-label="{{ $askBillLabel }}"
            title="Ask about this bill's reference, customer, or notes.">
            Ask about this bill
        </x-mcp.btn>
    @endif

    @if($bill->balance() > 0 && $bill->type == 'Invoice')
        @can('add journal entry')
            <x-mcp.btn variant="secondary" size="sm" icon="fa-coins" class="flex-fill"
                data-coreui-toggle="modal" data-coreui-target="#modal-payment">
                Payment
            </x-mcp.btn>
        @endcan
    @endif

    @if($bill->type == 'Invoice')
        @can('edit invoice')
            <x-mcp.btn variant="secondary" size="sm" icon="fa-edit" class="flex-fill"
                :href="route('bill.create', ['bill' => $bill, 'type' => 'Invoice'])">
                Edit
            </x-mcp.btn>
        @endcan
    @else
        @can('edit quotation')
            <x-mcp.btn variant="secondary" size="sm" icon="fa-edit" class="flex-fill"
                :href="route('bill.create', ['bill' => $bill, 'type' => $bill->type])">
                Edit
            </x-mcp.btn>
        @endcan
    @endif

    <x-mcp.btn variant="success" size="sm" icon="fa-print" class="flex-fill"
        :href="route('bill.print', $bill)" target="_blank">
        Print
    </x-mcp.btn>
</div>
