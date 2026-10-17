<div>
    <div class="mcp">
        @include('livewire.show-bill-mobile.header')
        @include('livewire.show-bill-mobile.bill-info')
        @include('livewire.show-bill-mobile.actions')
        @include('livewire.show-bill-mobile.line-items')

        {{-- Jobs associated with this bill --}}
        <div class="mb-3"
            data-mcp-page-context-owner="jobs"
            data-mcp-context-route="bill.show"
            data-mcp-context-kind="embedded_list"
            data-mcp-context-resource="work_order"
            data-mcp-context-id="jobs:bill:{{ $bill->id }}"
            data-mcp-context-scope-id="{{ $bill->id }}"
        >
            <h6 class="fw-bold text-success mb-2">Jobs</h6>
            @livewire('work-order-component', ['bill' => $bill, 'contextVariant' => 'bill'])
        </div>

        @include('livewire.show-bill-mobile.payments')
    </div>

    @include('livewire.show-bill-mobile.modals')

    @script
        <script>
            $wire.on('closeModal', () => {
                $('#modal-payment').modal('hide')
            })
        </script>
    @endscript
</div>
