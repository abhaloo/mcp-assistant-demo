<div>
    <div class="table-settings ">
        <div class="row mb-2">
            <div class="col"> <form class="float-start">
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
                               placeholder="Search by description">
                    </div>
                </form></div>
            <div class="col"> <a href="#" wire:click="create" class="btn btn-primary float-end"
                                 data-coreui-toggle="modal" data-coreui-target="#modal-work_order_attachment">
                    New Attachment
                </a></div>
        </div>
    </div>
        <table class="table table-hover table-bordered">
            <thead>
            <tr class="bg-light">
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">Description</th>
                <th class="border-gray-200">Size</th>
                <th class="border-gray-200">Format</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($work_order_attachments as $work_order_attachment)
            <tr>
                <td>{{ $loop->index + 1 }}</td>
                <td>{{ $work_order_attachment->description }}</td>
                <td>{{ round($work_order_attachment->size/ 1048576, 2) }} MB</td>
                <td>{{ $work_order_attachment->extension }}</td>
                <td class="text-end">
                    <a class="btn btn-warning btn-sm ms-2"
                       wire:click="download({{ $work_order_attachment->id }})"
                       wire:loading.attr="disabled"
                       wire:target="download">
                        <span wire:loading.remove wire:target="download"><i class="fa fa-download"></i> Download</span>
                        <span wire:loading wire:target="download"><i class="fa fa-spinner fa-spin"></i> Downloading...</span>
                    </a>
                    <button type="button" class="btn btn-danger btn-sm"
                            wire:click="deleteConfirm({{$work_order_attachment->id}})"
                            data-coreui-toggle="modal" data-coreui-target="#deleteModalwork-order-attachment">
                        <i class="fa fa-trash-alt"></i> Delete
                    </button>
                </td>
            </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $work_order_attachments->links() }}
        </div>


    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-work_order_attachment" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">New Attachment</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <label for="description">Description</label>
                            <input type="text" wire:model.live="description"
                                   class="form-control @error('description') is-invalid @enderror" id="description">
                            @error('description')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="mb-4">
                            <label for="file">File</label>
                            <input type="file" wire:model.live="file"
                                   class="form-control @error('file') is-invalid @enderror" id="file">
                            @error('file')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="modal-footer">

                            <button type="button"
                                    wire:click.prevent="store"
                                    wire:loading.attr="disabled"
                                    wire:target="store,file"
                                    class="btn btn-secondary">
                                <span wire:loading.remove wire:target="store,file">Attach</span>
                                <span wire:loading wire:target="store,file"><i class="fa fa-spinner fa-spin"></i> Uploading...</span>
                            </button>
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
    <div wire:ignore.self class="modal fade" id="deleteModalwork-order-attachment" data-backdrop="false" tabindex="-1"
         role="dialog" aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Delete Confirm </h5>
                </div>
                <div class="modal-body">
                    <p>Are you sure want to delete <strong>{{ $delete_confirm_description }}</strong> ?
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
            $('#modal-work_order_attachment').modal('hide')
        })
    </script>
    @endpush
</div>
