<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-4 mb-md-0">
            <h2 class="h4 text-capitalize">Manage {{ $product->name }} Image</h2>
        </div>
        <div class="btn-toolbar mb-2 mb-md-0">
            <a href="#" wire:click="create" class="btn btn-sm btn-primary d-inline-flex align-items-center"
               data-coreui-toggle="modal" data-coreui-target="#modal-productImage">
                <svg class="icon icon-xs me-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"
                     xmlns="http://www.w3.org/2000/svg">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                          d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>
                </svg>
                New Image
            </a>
        </div>
    </div>
    <div class="table-settings mb-4">
        <div class="row align-items-center justify-content-between mb-2">
            <form class="col col-md-6 col-lg-3 col-xl-4">
                <div class="input-group me-2 me-lg-3 fmxw-400">
                            <span class="input-group-text">
                                <svg class="icon icon-xs" x-description="Heroicon name: solid/search"
                                     xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"
                                     aria-hidden="true">
                                    <path fill-rule="evenodd"
                                          d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z"
                                          clip-rule="evenodd"></path>
                                </svg>
                            </span>
                    <input type="text" wire:model.live="search_keyword" class="form-control"
                           placeholder="Search by anything">
                </div>
            </form>
        </div>
    </div>
    <div class="card card-body border-0 shadow table-wrapper table-responsive">
        <table class="table table-hover table-sm">
            <thead>
            <tr>
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">Image</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($productImages as $productImage)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $productImage->url }}</td>

                    <td class="text-end">
                        <a href="#" wire:click="update({{ $productImage->id }})" class="btn btn-secondary btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-productImage">
                            <i class="fa fa-edit"></i>
                            Edit</a>

                        <button type="button" class="btn btn-danger btn-sm"
                                wire:click="deleteConfirm({{$productImage->id}})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModalproductImage">
                            <i class="fa fa-trash-alt"></i> Delete
                        </button>
                    </td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $productImages->links() }}
        </div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-productImage" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif productImage</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <label for="url">Image</label>
                            <input type="file" wire:model.live="url"
                                   class="form-control @error('url') is-invalid @enderror" id="url">
                            @error('url')
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
    <div wire:ignore.self class="modal fade" id="deleteModalproductImage" data-backdrop="false" tabindex="-1"
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
                $('#modal-productImage').modal('hide')
            })
        </script>
    @endpush
</div>
