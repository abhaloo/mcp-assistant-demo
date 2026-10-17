<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Manage <strong>Specification</strong></h1>
        </div>
        <div class="col">
            <div class="float-end">
                <a href="#" wire:click="create" class="btn btn-sm btn-primary d-inline-flex align-items-center"
                   data-coreui-toggle="modal" data-coreui-target="#modal-specification">
                    <i class="fa fa-plus me-2"></i> New Specification
                </a>
            </div>
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
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($specifications as $specification)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $specification->name }}</td>

                    <td class="text-end">

                        <a href="{{ route('specification-item.index', $specification->id) }}" class="btn btn-info btn-sm">
                            <i class="fa fa-list"></i> Items
                        </a>
                        <a href="#" wire:click="update({{ $specification->id }})" class="btn btn-secondary btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-specification">
                            <i class="fa fa-edit"></i>
                            Edit</a>

                        <button type="button" class="btn btn-danger btn-sm"
                                wire:click="deleteConfirm({{$specification->id}})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModalspecification">
                            <i class="fa fa-trash-alt"></i> Delete
                        </button>
                    </td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $specifications->links() }}
        </div>
    </div>

    <!-- Mobile Card Layout -->
    <div class="d-md-none">
        @forelse($specifications as $specification)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3 d-flex justify-content-between align-items-center">
                    <span class="fw-bold">{{ $specification->name }}</span>
                    <div class="d-flex gap-1">
                        <a href="{{ route('specification-item.index', $specification->id) }}" class="btn btn-info btn-sm">
                            <i class="fa fa-list"></i>
                        </a>
                        <a href="#" wire:click="update({{ $specification->id }})" class="btn btn-secondary btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-specification">
                            <i class="fa fa-edit"></i>
                        </a>
                        <button type="button" class="btn btn-danger btn-sm"
                                wire:click="deleteConfirm({{$specification->id}})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModalspecification">
                            <i class="fa fa-trash-alt"></i>
                        </button>
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center py-4 text-muted">No records found.</div>
            </div>
        @endforelse
        <div class="mt-2">{{ $specifications->links() }}</div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-specification" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif specification</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <label for="name" class="form-label">Name</label>
                            <input type="text" wire:model.live="name"
                                   class="form-control @error('name') is-invalid @enderror" id="name">
                            @error('name')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        @if(!$update)
                        <div class="mb-4">
                            <label for="copy_from_id" class="form-label">Copy From (Optional)</label>
                            <select wire:model.live="copy_from_id" class="form-control" id="copy_from_id">
                                <option value="">-- Create Empty --</option>
                                @foreach($allSpecifications as $spec)
                                    <option value="{{ $spec->id }}">{{ $spec->name }} ({{ $spec->items->count() }} items)</option>
                                @endforeach
                            </select>
                            <small class="form-text text-muted">
                                Copy all items from an existing specification
                            </small>
                        </div>
                        @endif
                        <div class="modal-footer">
                            <button type="button" wire:click.prevent="store" class="btn btn-primary"> @if($update)
                                    Update @else Add @endif</button>
                            <button type="button" class="btn btn-secondary text-gray-600 ms-auto" data-coreui-dismiss="modal">
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
    <div wire:ignore.self class="modal fade" id="deleteModalspecification" data-backdrop="false" tabindex="-1"
         role="dialog" aria-labelledby="deleteModalLabel" aria-hidden="true">
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
                $('#modal-specification').modal('hide')
            })
        </script>
    @endpush
</div>
