@push('styles')
    <style>
        .select2-container--default .select2-selection--single,
        .select2-selection .select2-selection--single {
            border: 1px solid #d2d6de;
            border-radius: 0;
            padding: 6px 12px;
            height: 34px
        }

        .select2-container--default .select2-selection--single {
            background-color: #fff;
            border: 1px solid #aaa;
            border-radius: 4px
        }

        .select2-container .select2-selection--single {
            box-sizing: border-box;
            cursor: pointer;
            display: block;
            height: 28px;
            user-select: none;
            -webkit-user-select: none
        }

        .select2-container .select2-selection--single .select2-selection__rendered {
            padding-right: 10px
        }

        .select2-container .select2-selection--single .select2-selection__rendered {
            padding-left: 0;
            padding-right: 0;
            height: auto;
            margin-top: -3px
        }

        .select2-container--default .select2-selection--single .select2-selection__rendered {
            color: #444;
            line-height: 28px
        }

        .select2-container--default .select2-selection--single,
        .select2-selection .select2-selection--single {
            border: 1px solid #d2d6de;
            border-radius: 2 !important;
            padding: 6px 12px;
            height: 40px !important
        }

        .select2-container--default .select2-selection--single .select2-selection__arrow {
            height: 26px;
            position: absolute;
            top: 6px !important;
            right: 1px;
            width: 20px
        }
    </style>
@endpush
@include('livewire.partials.select2-assets')
<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center">
        <div class="d-block mb-4 mb-md-0">
            <h1 class="h3 mb-3">Manage <strong>{{ $specification->name }}</strong> Specification</h1>
        </div>
        <div class="btn-toolbar mb-2 mb-md-0">
            <a href="#" class="btn btn-sm btn-info d-inline-flex align-items-center me-2"
               data-coreui-toggle="modal" data-coreui-target="#modal-preview">
                <i class="fa fa-eye me-2"></i>
                Preview Form
            </a>
            <a href="#" wire:click="create" class="btn btn-sm btn-primary d-inline-flex align-items-center me-2"
               data-coreui-toggle="modal" data-coreui-target="#modal-specification_item">
                <i class="fa fa-plus me-2"></i>
                New Specification Item
            </a>
            <a href="{{ route('specification.index') }}" class="btn btn-sm btn-secondary d-inline-flex align-items-center">
                <i class="fa fa-arrow-left me-1"></i> Back
            </a>
        </div>
    </div>
    <div class="table-settings mb-4">
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
                <th class="border-gray-200">Type</th>
                <th class="border-gray-200">Group</th>
                <th class="border-gray-200 text-center">Sort Order</th>
                <th class="border-gray-200 text-center">Columns</th>
                <th class="border-gray-200">Value</th>
                <th class="border-gray-200 text-center"></th>
            </tr>
            </thead>
            <tbody>
            @forelse($specification_items as $specification_item)
                <tr>
                    <td>{{ ($specification_items->currentPage() - 1) * $specification_items->perPage() + $loop->index + 1 }}</td>
                    <td>{{ $specification_item->name }}</td>
                    <td>{{ $specification_item->type }}</td>
                    <td>
                        @if($specification_item->group_name)
                            <x-badge color="secondary">{{ $specification_item->group_name }}</x-badge>
                        @else
                            <span class="text-muted">—</span>
                        @endif
                    </td>
                    <td class="text-center">{{ $specification_item->sort_order }}</td>
                    <td class="text-center">{{ $specification_item->columns }}</td>
                    <td>
                        @foreach((array) json_decode($specification_item->value) as $val)
                            <x-badge color="info">{{$val}}</x-badge>
                        @endforeach
                    </td>

                    <td class="text-end">
                        <a href="#" wire:click="update({{ $specification_item->id }})" class="btn btn-secondary btn-sm"
                           data-coreui-toggle="modal" data-coreui-target="#modal-specification_item">
                            <i class="fa fa-edit"></i>
                            Edit</a>

                        <button type="button" class="btn btn-danger btn-sm"
                                wire:click="deleteConfirm({{$specification_item->id}})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModalspecification-item">
                            <i class="fa fa-trash-alt"></i> Delete
                        </button>
                    </td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $specification_items->links() }}
        </div>
    </div>

    {{-- Mobile card layout --}}
    <div class="d-md-none">
        @forelse($specification_items as $specification_item)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <span class="fw-bold">{{ $specification_item->name }}</span>
                        <x-badge color="info">{{ $specification_item->type }}</x-badge>
                    </div>
                    <div class="d-flex flex-wrap gap-3 text-muted" style="font-size: 0.8rem">
                        @if($specification_item->group_name)
                            <span><i class="fa fa-layer-group me-1"></i>{{ $specification_item->group_name }}</span>
                        @endif
                        <span><i class="fa fa-sort me-1"></i>Order: {{ $specification_item->sort_order }}</span>
                        <span><i class="fa fa-columns me-1"></i>Cols: {{ $specification_item->columns }}</span>
                    </div>
                    @if(!empty(json_decode($specification_item->value)))
                        <div class="mt-1">
                            @foreach((array) json_decode($specification_item->value) as $val)
                                <x-badge color="info">{{ $val }}</x-badge>
                            @endforeach
                        </div>
                    @endif
                    <div class="d-flex flex-wrap gap-1 pt-2 border-top mt-2">
                        <a href="#" wire:click="update({{ $specification_item->id }})" class="btn btn-secondary btn-sm flex-fill"
                           data-coreui-toggle="modal" data-coreui-target="#modal-specification_item">
                            <i class="fa fa-edit"></i> Edit
                        </a>
                        <button type="button" class="btn btn-danger btn-sm flex-fill"
                                wire:click="deleteConfirm({{ $specification_item->id }})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModalspecification-item">
                            <i class="fa fa-trash-alt"></i> Delete
                        </button>
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body text-center py-4 text-muted">
                    No specification items found.
                </div>
            </div>
        @endforelse

        <div class="mt-2">
            {{ $specification_items->links() }}
        </div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-specification_item" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif specification-item</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <div>
                                <label for="name" class="form-label">Name</label>
                                <input type="text" wire:model.live="name"
                                       class="form-control @error('name') is-invalid @enderror" id="name">
                                @error('name')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            </div>
                            <div class="my-2">
                                <label for="type" class="form-label">Type</label>
                                <select wire:model.live="type" class="form-control @error('type') is-invalid @enderror"  id="type">
                                    <option value="">Select ...</option>
                                    <option value="Options">Options</option>
                                    <option value="Strings">Strings</option>
                                    <option value="Numbers">Numbers</option>
                                    <option value="Box">Box</option>
                                    <option value="Soft List">Soft List</option>
                                    <option value="Hard List">Hard List</option>
                                    <option value="Description">Description</option>
                                </select>
                                @error('type')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            </div>
                            @if($type == "Options")
                                <div>
                                    <label for="value" class="form-label">Options</label>
                                    <div class="input-group me-2 me-lg-3 fmxw-400">
                                        <input type="text" wire:model.live="value"
                                               wire:keydown.enter.prevent="addValues"
                                               class="form-control @error('value') is-invalid @enderror" 
                                               id="value"
                                               placeholder="Type option and press Enter or click +">
                                        <button wire:click.prevent="addValues" type="button" class="btn btn-outline-primary">
                                            <i class="fa fa-plus"></i> Add
                                        </button>
                                        @error('value')
                                        <div class="invalid-feedback">
                                            {{ $message }}
                                        </div>
                                        @enderror
                                    </div>
                                    <small class="text-muted">Add options that users can select from for this field.</small>
                                    
                                    @if(count($values) > 0)
                                        <div class="mt-3">
                                            <label class="form-label text-muted mb-2">Current Options ({{ count($values) }})</label>
                                            <div class="d-flex flex-column gap-2">
                                                @foreach($values as $index => $_value)
                                                    <div class="card border-primary" style="border-width: 2px;">
                                                        <div class="card-body p-2 d-flex justify-content-between align-items-center">
                                                            <span class="text-dark">{{ $_value }}</span>
                                                            <button type="button" 
                                                                    wire:click="removeValue({{ $index }})" 
                                                                    class="btn btn-sm btn-outline-danger d-inline-flex align-items-center"
                                                                    aria-label="Remove {{ $_value }}">
                                                                <i class="fa fa-times"></i>
                                                            </button>
                                                        </div>
                                                    </div>
                                                @endforeach
                                            </div>
                                        </div>
                                    @else
                                        <div class="mt-3 text-muted">
                                            <i class="fa fa-info-circle me-1"></i>No options added yet.
                                        </div>
                                    @endif
                                </div>
                            @endif
                        </div>
                        <div>
                            <label for="columns" class="form-label">Columns</label>
                            <input type="number" wire:model.live="columns"
                                   class="form-control @error('columns') is-invalid @enderror" id="columns">
                            @error('columns')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="my-2">
                            <label for="department_id" class="form-label">Restrict to Department (Optional)</label>
                            <select wire:model.live="department_id" class="form-control @error('department_id') is-invalid @enderror" id="department_id">
                                <option value="">None</option>
                                @foreach($departments as $dept)
                                    <option value="{{ $dept->id }}">{{ $dept->name }}</option>
                                @endforeach
                            </select>
                            <small class="form-text text-muted">
                                If set, only users in this department can edit this field in work orders.
                            </small>
                            @error('department_id')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="my-2">
                            <label for="group_name" class="form-label">Group (Optional)</label>
                            <input type="text" wire:model.live="group_name" list="existingGroups"
                                   class="form-control @error('group_name') is-invalid @enderror" id="group_name"
                                   placeholder="e.g. OFFSET/INDIGO, LARGE FORMAT">
                            <datalist id="existingGroups">
                                @foreach($existingGroups as $group)
                                    <option value="{{ $group }}">
                                @endforeach
                            </datalist>
                            <small class="form-text text-muted">
                                Fields with the same group name will be grouped together under a section header.
                            </small>
                            @error('group_name')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="my-2">
                            <label for="sort_order" class="form-label">Sort Order</label>
                            <input type="number" wire:model.live="sort_order" min="0"
                                   class="form-control @error('sort_order') is-invalid @enderror" id="sort_order"
                                   placeholder="0">
                            <small class="form-text text-muted">
                                Lower numbers appear first. Leave at 0 for default ordering.
                            </small>
                            @error('sort_order')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
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
    <div wire:ignore.self class="modal fade" id="deleteModalspecification-item" data-backdrop="false" tabindex="-1"
         role="dialog" aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Delete Confirm </h5>
                </div>
                <div class="modal-body">
                    <p>
                        Are you sure want to delete <strong>{{ $delete_confirm? $delete_confirm->name: ''  }}</strong> ?
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
                $('#modal-specification_item').modal('hide')
            })
            document.addEventListener("livewire:init", () => {
                Livewire.hook('morph.updated', () => {
                    $('#select_employee').select2()

                }); });

            document.addEventListener('DOMContentLoaded', function () {
                $('#select_employee').select2({
                    placeholder: 'Select an option',
                    allowClear: true
                });

                $('#select_employee').on('change', function (e) {
                    var data = $('#select_employee').select2("val");
                    @this.set('employee', data);
                });
            });
        </script>
    @endpush

    <!-- Preview Modal -->
    <div class="modal fade" id="modal-preview" tabindex="-1" role="dialog" aria-labelledby="modal-preview-label" aria-hidden="true">
        <div class="modal-dialog modal-xl modal-dialog-scrollable" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="modal-preview-label">
                        <i class="fa fa-eye me-2"></i>Preview: {{ $specification->name }}
                    </h5>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="alert alert-info mb-3">
                        <i class="fa fa-info-circle me-1"></i>
                        This is how the specification form will appear on job orders.
                    </div>
                    <div class="row">
                        @php
                            $sortedItems = $specification->items->sortBy('sort_order');
                            $grouped = $sortedItems->groupBy(function($item) {
                                return $item->group_name ?? '';
                            });
                            $ungrouped = $grouped->get('', collect());
                            $grouped = $grouped->forget('');
                        @endphp
                        
                        {{-- Render ungrouped items first --}}
                        @foreach($ungrouped as $item)
                            <div class="col-{{ $item->columns }} my-2">
                                @include('livewire.partials.specification-item-preview', ['item' => $item])
                            </div>
                        @endforeach
                        
                        {{-- Render grouped items with section headers --}}
                        @foreach($grouped as $groupName => $items)
                            <div class="col-12 mt-3 mb-1">
                                <strong class="text-uppercase" style="border-bottom: 2px solid #333; padding-bottom: 2px; display: inline-block;">
                                    {{ $groupName }}
                                </strong>
                            </div>
                            @foreach($items as $item)
                                <div class="col-{{ $item->columns }} my-2">
                                    @include('livewire.partials.specification-item-preview', ['item' => $item])
                                </div>
                            @endforeach
                        @endforeach
                    </div>
                </div>
                <div class="modal-footer">
                    <span class="text-muted me-auto">{{ $specification->items->count() }} items across 12 columns</span>
                    <button type="button" class="btn btn-secondary" data-coreui-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    </div>
</div>
