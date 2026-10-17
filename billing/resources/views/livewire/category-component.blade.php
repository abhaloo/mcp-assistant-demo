<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Manage <strong>Categories</strong></h1>
        </div>
        <div class="col">
            <div class="float-end">
                @can('add category')
                    <a href="#" wire:click="create" class="btn btn-sm btn-primary"
                       data-coreui-toggle="modal" data-coreui-target="#modal-category">
                        <span class="fa fa-plus"></span> New Category
                    </a>
                @endcan
                <x-back-link :fallback="route('product.index')"
                   class="ms-2 btn btn-sm btn-secondary d-inline-flex align-items-center">
                    <i class="fa fa-chevron-left me-2"></i>
                    Back
                </x-back-link>
            </div>
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
                <th class="border-gray-200">Unit Hierarchy</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($categorys as $category)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $category->name }}</td>
                    <td>
                        @if($category->hasUnitHierarchy())
                            <x-badge color="success">
                                <i class="fa fa-check me-1"></i>
                                {{ count($category->getCountLevels()) }} levels
                            </x-badge>
                            @if(count($category->getCategoryAttributes()) > 0)
                                <x-badge color="info" class="ms-1">
                                    {{ count($category->getCategoryAttributes()) }} attrs
                                </x-badge>
                            @endif
                        @else
                            <x-badge color="secondary">Not configured</x-badge>
                        @endif
                    </td>

                    <td class="text-end">
                        @can('edit category')
                            <a href="#" wire:click="update({{ $category->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-category">
                                <i class="fa fa-edit"></i>
                                Edit</a>
                        @endcan

                        @can('delete category')
                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$category->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalcategory">
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
            {{ $categorys->links() }}
        </div>
    </div>

    <!-- Mobile Card Layout -->
    <div class="d-md-none">
        @forelse($categorys as $category)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <span class="fw-bold">{{ $category->name }}</span>
                        <div>
                            @if($category->hasUnitHierarchy())
                                <x-badge color="success">
                                    <i class="fa fa-check me-1"></i>
                                    {{ count($category->getCountLevels()) }} levels
                                </x-badge>
                                @if(count($category->getCategoryAttributes()) > 0)
                                    <x-badge color="info" class="ms-1">
                                        {{ count($category->getCategoryAttributes()) }} attrs
                                    </x-badge>
                                @endif
                            @else
                                <x-badge color="secondary">Not configured</x-badge>
                            @endif
                        </div>
                    </div>
                    <div class="d-flex gap-1">
                        @can('edit category')
                            <a href="#" wire:click="update({{ $category->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-category">
                                <i class="fa fa-edit"></i> Edit
                            </a>
                        @endcan
                        @can('delete category')
                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$category->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalcategory">
                                <i class="fa fa-trash-alt"></i> Delete
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
        <div class="mt-2">{{ $categorys->links() }}</div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-category" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered @if($update) modal-xl @endif" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif Category</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    {{-- Category Name --}}
                    <div class="mb-4">
                        <label for="name" class="form-label fw-medium">Category Name <span class="text-danger">*</span></label>
                        <input type="text" wire:model.live="name"
                               class="form-control @error('name') is-invalid @enderror" id="name"
                               placeholder="e.g., Papers, Inks, Chemicals">
                        @error('name')
                        <div class="invalid-feedback">{{ $message }}</div>
                        @enderror
                    </div>

                    {{-- Units Section (only shown when editing) --}}
                    @if($update && $category_id)
                    <hr class="my-4">
                    <h6 class="text-uppercase text-muted mb-3">Units</h6>
                    @livewire('category-unit-config-component', ['category_id' => $category_id], key('unit-config-'.$category_id))
                    @endif
                </div>
                <div class="modal-footer d-flex justify-content-between">
                    <button type="button" wire:click.prevent="store" class="btn btn-primary">
                        <i class="fa fa-save me-1"></i>
                        @if($update) Update @else Add @endif
                    </button>
                    <button type="button" class="btn btn-secondary" data-coreui-dismiss="modal">
                        Close
                    </button>
                </div>
            </div>
        </div>
    </div>
    <!-- End of Modal Content -->

    <!-- Delete Modal -->
    <div wire:ignore.self class="modal fade" id="deleteModalcategory" data-backdrop="false" tabindex="-1" role="dialog"
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
                $('#modal-category').modal('hide')
            })
        </script>
    @endpush
</div>
