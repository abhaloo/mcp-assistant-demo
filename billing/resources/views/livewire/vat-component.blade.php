<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-4 mb-md-0">
            <h2 class="h4">Manage <strong>VAT</strong></h2>
        </div>
        <div class="btn-toolbar mb-2 mb-md-0">
            <a href="#" wire:click="create" class="btn btn-sm btn-primary"
               data-coreui-toggle="modal" data-coreui-target="#modal-vat">
                <i class="fa fa-plus"></i>
                New VAT
            </a>
        </div>
    </div>
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

    <div class="card card-body border-0 shadow table-wrapper table-responsive d-none d-md-block">
        <table class="table table-hover table-sm">
            <thead>
            <tr>
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">Code</th>
                <th class="border-gray-200">Name</th>
                <th class="border-gray-200">Rate</th>
                <th class="border-gray-200">Control Account</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($vats as $vat)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $vat->code }}</td>
                    <td>{{ $vat->name }}</td>
                    <td>{{ $vat->rate }}</td>
                    <td>{{ $vat->account->name }}</td>

                    <td class="text-end">
                        <a href="#" wire:click="update({{ $vat->id }})" class="btn btn-secondary btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-vat">
                            <i class="fa fa-edit"></i>
                            Edit</a>

                        <button type="button" class="btn btn-danger btn-sm"
                                wire:click="deleteConfirm({{$vat->id}})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModalvat">
                            <i class="fa fa-trash-alt"></i> Delete
                        </button>
                    </td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $vats->links() }}
        </div>
    </div>

    <!-- Mobile Card Layout -->
    <div class="d-md-none">
        @forelse($vats as $vat)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <span class="small fw-semibold text-muted">{{ $vat->code }}</span>
                        <x-badge color="info">{{ $vat->rate }}</x-badge>
                    </div>
                    <div class="fw-bold mb-1">{{ $vat->name }}</div>
                    <div class="text-muted mb-2" style="font-size: 0.8rem;">
                        <i class="fa fa-university me-1"></i>{{ $vat->account->name }}
                    </div>
                    <div class="d-flex gap-1">
                        <a href="#" wire:click="update({{ $vat->id }})" class="btn btn-secondary btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-vat">
                            <i class="fa fa-edit"></i> Edit
                        </a>
                        <button type="button" class="btn btn-danger btn-sm"
                                wire:click="deleteConfirm({{$vat->id}})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModalvat">
                            <i class="fa fa-trash-alt"></i> Delete
                        </button>
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center py-4 text-muted">No records found.</div>
            </div>
        @endforelse
        <div class="mt-2">{{ $vats->links() }}</div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-vat" tabindex="-1" role="dialog" aria-labelledby="modal-default"
         aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif VAT</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <label for="code">Code</label>
                            <select wire:model.live="code"
                                    class="form-control @error('code') is-invalid @enderror"
                                    id="code">
                                <option>--Select--</option>
                                <option value="O">Output</option>
                                <option value="I">Input</option>
                            </select>
                            @error('code')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="mb-4">
                            <label for="name">Name</label>
                            <input type="text" wire:model.live="name"
                                   class="form-control @error('name') is-invalid @enderror" id="name">
                            @error('name')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="rate">Rate</label>
                            <input type="text" wire:model.live="rate"
                                   class="form-control @error('rate') is-invalid @enderror" id="rate">
                            @error('rate')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="account_id">Control Account</label>
                            <select wire:model.live="account_id"
                                    class="form-control @error('account_id') is-invalid @enderror"
                                    id="account_id">
                                <option>--Select--</option>
                                @foreach($accounts as $account)
                                   <option value="{{ $account->id }}"> {{ $account->name }}</option>
                                @endforeach
                            </select>
                            @error('account_id')
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
    <div wire:ignore.self class="modal fade" id="deleteModalvat" data-backdrop="false" tabindex="-1" role="dialog"
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
                $('#modal-vat').modal('hide')
            })
        </script>
    @endpush
</div>
