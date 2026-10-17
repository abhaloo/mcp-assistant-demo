<div>
    @include('livewire.supplier-bill-show-mobile.header')
    @include('livewire.supplier-bill-show-mobile.bill-info')

    {{-- Actions --}}
    <div class="d-flex flex-wrap gap-2 mb-3">
        @can('add journal entry')
            @if($inventory->status == 'APPROVED' && $balance > 0)
                <a href="#" class="btn btn-sm btn-secondary flex-fill"
                   data-coreui-toggle="modal" data-coreui-target="#modal-payment">
                    <i class="fa fa-coins me-1"></i> Payment
                </a>
            @endif
        @endcan
    </div>

    @if($inventory->is_goods_costing)
        @include('livewire.supplier-bill-show-mobile.goods-costing-items')
        @include('livewire.supplier-bill-show-mobile.costing-details')
    @else
        @include('livewire.supplier-bill-show-mobile.expense-items')
    @endif

    @include('livewire.supplier-bill-show-mobile.payments')
    @include('livewire.supplier-bill-show-mobile.edit-history')
    @include('livewire.supplier-bill-show-mobile.modals')

    @push('scripts')
        <script>
            Livewire.on('closeModal', () => {
                const modal = document.getElementById('modal-payment');
                const modalInstance = coreui.Modal.getInstance(modal);
                if (modalInstance) {
                    modalInstance.hide();
                }
            });
        </script>
    @endpush
</div>
