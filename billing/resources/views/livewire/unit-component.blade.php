<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Manage <strong>Units</strong></h1>
        </div>
        <div class="col">
            <x-back-link :fallback="route('product.index')" class="btn btn-sm btn-secondary float-end">
                <span class="fa fa-chevron-left me-2"></span> Back
            </x-back-link>
            @can('add unit')
                <a href="#" wire:click="create" class="btn btn-sm btn-primary float-end me-2"
                   data-coreui-toggle="modal" data-coreui-target="#modal-unit">
                    <span class="fa fa-plus"></span> New Unit
                </a>
            @endcan
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
                <th class="border-gray-200">Name</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($units as $unit)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $unit->name }}</td>

                    <td class="text-end">

                        @can('edit unit')
                            <a href="#" wire:click="update({{ $unit->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-unit">
                                <i class="fa fa-edit"></i>
                                Edit</a>
                        @endcan

                        @can('delete unit')
                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$unit->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalunit">
                                <i class="fa fa-trash-alt"></i> Delete
                            </button>
                        @endcan
                    </td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $units->links() }}
        </div>
    </div>

    <!-- Mobile Card Layout -->
    <div class="d-md-none">
        @forelse($units as $unit)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 d-flex justify-content-between align-items-center">
                    <span class="fw-bold">{{ $unit->name }}</span>
                    <div class="d-flex gap-1">
                        @can('edit unit')
                            <a href="#" wire:click="update({{ $unit->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-unit">
                                <i class="fa fa-edit"></i>
                            </a>
                        @endcan
                        @can('delete unit')
                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$unit->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalunit">
                                <i class="fa fa-trash-alt"></i>
                            </button>
                        @endcan
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center py-4 text-muted">No records found.</div>
            </div>
        @endforelse
        <div class="mt-2">{{ $units->links() }}</div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-unit" tabindex="-1" role="dialog" aria-labelledby="modal-default"
         aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif unit</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
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
                        <div class="modal-footer">

                            <button type="button" wire:click.prevent="store" class="btn btn-primary"> @if($update)
                                    Update @else Add @endif</button>
                            <button type="button" class="btn btn-secondary text-gray-600 ms-auto"
                                    data-coreui-dismiss="modal">
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
    <div wire:ignore.self class="modal fade" id="deleteModalunit" data-backdrop="false" tabindex="-1" role="dialog"
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
                    <button type="button" class="btn btn-secondary close-btn" data-coreui-dismiss="modal">Cancel
                    </button>
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
                $('#modal-unit').modal('hide')
            })
        </script>
    @endpush
</div>
