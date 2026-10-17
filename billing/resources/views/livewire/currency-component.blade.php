<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-4 mb-md-0">
            <h2 class="h4">Manage Currencies</h2>
        </div>
        <div class="btn-toolbar mb-2 mb-md-0">
            <a href="#" wire:click="create" class="btn btn-sm btn-primary d-inline-flex align-items-center"
               data-coreui-toggle="modal" data-coreui-target="#modal-currency">
               <i class="fa fa-plus"></i>
                New Currency
            </a>
        </div>
    </div>
    <div class="table-settings">
        <div class="row align-items-center justify-content-between mb-2">
            <form class="col col-md-6 col-lg-3 col-xl-4">
                <div class="input-group me-2 me-lg-3 fmxw-400">
                            <span class="input-group-text">
                               <i class="fa fa-search"></i>
                            </span>
                    <input type="text" wire:model.live="search_keyword" class="form-control"
                           placeholder="Search by anything">
                </div>
            </form>
        </div>
    </div>
    <div class="card card-body border-0 shadow table-wrapper table-responsive d-none d-md-block">
        <table class="table table-hover table-sm">
            <thead>
            <tr>
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">Name</th>
                <th class="border-gray-200">Currency Code</th>
                <th class="border-gray-200">Exchange Rate</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($currencies as $currency)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $currency->name }}</td>
                    <td>{{ $currency->currency_code }}</td>
                    <td>{{ $currency->rate }}</td>

                    <td class="text-end">
                        @if($currency->id != 1)
                            <a href="#" wire:click="update({{ $currency->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-currency">
                                <i class="fa fa-edit"></i>
                                Edit</a>

                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$currency->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalcurrency">
                                <i class="fa fa-trash-alt"></i> Delete
                            </button>
                        @endif
                    </td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $currencies->links() }}
        </div>
    </div>

    <!-- Mobile Card Layout -->
    <div class="d-md-none">
        @forelse($currencies as $currency)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <x-badge color="secondary">{{ $currency->currency_code }}</x-badge>
                        <span class="fw-bold">{{ $currency->rate }}</span>
                    </div>
                    <div class="fw-bold mb-1">{{ $currency->name }}</div>
                    @if($currency->id != 1)
                        <div class="d-flex gap-1">
                            <a href="#" wire:click="update({{ $currency->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-currency">
                                <i class="fa fa-edit"></i> Edit
                            </a>
                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$currency->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalcurrency">
                                <i class="fa fa-trash-alt"></i> Delete
                            </button>
                        </div>
                    @endif
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center py-4 text-muted">No records found.</div>
            </div>
        @endforelse
        <div class="mt-2">{{ $currencies->links() }}</div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-currency" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif currency</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <label for="currency">Currency</label>
                            @if($update)
                                <input class="form-control" disabled value="{{$currency->name}}">
                            @else
                                <select id="currency" wire:model.live="currency" class="form-control">
                                    <option>Select currency</option>
                                    <option value="USD_$_USD - US Dollar">USD - US Dollar</option>
                                    <option value="EUR_€_EUR - Euro">EUR - Euro</option>
                                    <option value="GBP_£_GBP - British Pound Sterling">GBP - British Pound Sterling</option>
                                </select>
                                @error('currency')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                           @endif

                        </div>


                        <div class="mb-4">
                            <label for="name">Exchange Rate</label>
                            <input type="number" step="0.01" wire:model.live="rate"
                                   class="form-control @error('rate') is-invalid @enderror" id="rate">
                            @error('rate')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="modal-footer">

                            <button type="button" wire:click.prevent="store" class="btn btn-secondary"> @if($update)
                                    Update @else Add @endif</button>
                            <button type="button" class="btn btn-link text-gray-600 ms-auto" data-coreui-dismiss="modal">
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!-- End of Modal Content -->

    <!-- Delete Modal -->
    <div wire:ignore.self class="modal fade" id="deleteModalcurrency" data-backdrop="false" tabindex="-1" role="dialog"
         aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Delete Confirm </h5>
                </div>
                <div class="modal-body">
                    <p>Are you sure want to delete <strong>{{ $delete_confirm? $delete_confirm->name: ''  }}</strong> ?
                    </p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary close-btn" data-coreui-dismiss="modal">Cancel</button>
                    <button type="button" wire:click.prevent="destroy()" class="btn btn-danger close-modal"
                            data-coreui-dismiss="modal">Yes, Delete
                    </button>
                </div>
            </div>
        </div>
    </div>

    @push('scripts')
        <script>
            Livewire.on('closeModal', () => {
                $('#modal-currency').modal('hide')
            })
        </script>
    @endpush
</div>
