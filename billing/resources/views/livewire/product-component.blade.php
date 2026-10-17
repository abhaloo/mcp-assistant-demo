<div>
    <div class="d-flex flex-column flex-md-row align-items-md-center justify-content-between mb-3">
        <h1 class="h3 mb-2 mb-md-0">Manage <strong>Products / Materials</strong></h1>
        <div class="d-flex flex-wrap gap-2">
            @can('add product')
                <a href="#" wire:click="create" class="btn btn-sm btn-primary" data-coreui-toggle="modal"
                   data-coreui-target="#modal-product">
                    <i class="fa fa-plus"></i>
                    New Product
                </a>
            @endcan
            @can('add unit')
                <a href="{{ route('unit.index') }}" class="btn btn-sm btn-secondary">
                    <i class="fa fa-list me-1"></i> Units
                </a>
            @endcan
            @can('add category')
                <a href="{{ route('category.index') }}" class="btn btn-sm btn-secondary">
                    <i class="fa fa-list me-1"></i> Categories
                </a>
            @endcan
        </div>
    </div>

    <div class="row mb-2">
        <div class="col-12 col-md-auto">
            <div class="d-flex flex-wrap flex-md-nowrap gap-2">
                {{-- Search --}}
                <div class="input-group input-group-sm flex-grow-1" style="min-width: 200px;">
                    <span class="input-group-text">
                        <i class="fa fa-search"></i>
                    </span>
                    <input type="text" wire:model.live="search_keyword" class="form-control form-control-sm"
                           placeholder="Search by anything">
                </div>

                {{-- Filter Dropdown --}}
                <div class="dropdown">
                    <button class="btn btn-sm btn-outline-secondary dropdown-toggle" type="button"
                            data-coreui-toggle="dropdown" data-coreui-auto-close="outside">
                        <i class="fa fa-filter"></i>
                        @if($filter_type || $filter_category)
                            <x-badge color="primary-dark" class="ms-2">Filtered</x-badge>
                        @endif
                    </button>
                    <div class="dropdown-menu p-3" style="min-width: 280px;">
                        {{-- Type Filter --}}
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Type</label>
                            <select wire:model.live="filter_type" class="form-select form-select-sm">
                                <option value="">All Types</option>
                                <option value="Material">Material</option>
                                <option value="Product">Product</option>
                                <option value="Service">Service</option>
                            </select>
                        </div>

                        {{-- Category Filter --}}
                        <div class="mb-3">
                            <label class="form-label small fw-bold">Category</label>
                            <select wire:model.live="filter_category" class="form-select form-select-sm">
                                <option value="">All Categories</option>
                                @foreach($categories as $cat)
                                    <option value="{{ $cat->id }}">{{ $cat->name }}</option>
                                @endforeach
                            </select>
                        </div>

                        {{-- Filter Actions --}}
                        <div class="d-flex justify-content-end gap-2">
                            @if($filter_type || $filter_category)
                                <button type="button" wire:click="clearFilters" class="btn btn-sm btn-outline-secondary">
                                    Clear
                                </button>
                            @endif
                            <button type="button" wire:click="$refresh" class="btn btn-sm btn-primary">
                                <i class="fa fa-check"></i> Filter
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    {{-- ==================== DESKTOP TABLE (hidden on mobile) ==================== --}}
    <div class="card card-body border-0 shadow table-wrapper table-responsive d-none d-md-block">
        <table class="table table-hover table-sm">
            <thead>
            <tr>
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">SKU</th>
                <th class="border-gray-200">Type</th>
                <th class="border-gray-200">Name</th>
                <th class="border-gray-200">GSM</th>
                <th class="border-gray-200">Size</th>
                <th class="border-gray-200">Category</th>
                <th class="border-gray-200">Unit</th>
                <th class="border-gray-200 text-center">Reorder</th>
                <th class="border-gray-200 text-end">Price</th>
                <th class="border-gray-200">Inventory</th>
                <th class="border-gray-200">In Hands</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($products as $product)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $product->sku }}</td>
                    <td>{{ $product->type }}</td>
                    <td>{{ $product->name }}</td>
                    <td>{{ $product->gsm ?? '-' }}</td>
                    <td>{{ $product->size ?? '-' }}</td>
                    <td>{{ $product->category->name }}</td>
                    <td>{{ $product->unit->name ?? 'None' }}</td>
                    <td class="text-center">{{ $product->reorder }}</td>
                    <td class="text-end">{{ $product->price }}</td>
                    <td>{{ $product->inventory == 1 ? 'Yes': 'No' }}</td>
                    @if(!$product->inventory)
                        <td>-</td>
                    @else
                        <td>{{ number_format($stockBalances[$product->id] ?? 0) }}</td>
                    @endif

                    <td class="text-end">
                        @can('view inventory')
                            <a href="{{ route('product.history', $product->id) }}" class="btn btn-info btn-sm">
                                <i class="fa fa-clock"></i> History
                            </a>
                        @endcan

                        @can('edit product')
                            <a href="#" wire:click="update({{ $product->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-product">
                                <i class="fa fa-edit"></i>
                                Edit</a>
                        @endcan

                        @can('delete product')
                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$product->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalProduct">
                                <i class="fa fa-trash-alt"></i> Delete
                            </button>
                        @endcan

                    </td>
                </tr>
            @empty
                <tr>
                    <td colspan="13" class="text-center py-4 text-muted">
                        No products found.
                    </td>
                </tr>
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $products->links() }}
        </div>
    </div>

    {{-- ==================== MOBILE CARD LIST (visible only on mobile) ==================== --}}
    <div class="d-md-none">
        @forelse($products as $product)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    {{-- Top row: type + category --}}
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <x-badge color="secondary">{{ $product->type }}</x-badge>
                        <small class="text-muted fw-semibold">{{ $product->category->name }}</small>
                    </div>

                    {{-- Product name --}}
                    <div class="fw-bold mb-1" style="font-size: 0.95rem;">
                        {{ $product->name }}
                    </div>

                    {{-- Meta: SKU, unit, GSM, size --}}
                    <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem;">
                        @if($product->sku)
                            <span><i class="fa fa-barcode me-1"></i>{{ $product->sku }}</span>
                        @endif
                        <span><i class="fa fa-ruler me-1"></i>{{ $product->unit->name ?? 'None' }}</span>
                        @if($product->gsm)
                            <span>GSM: {{ $product->gsm }}</span>
                        @endif
                        @if($product->size)
                            <span>Size: {{ $product->size }}</span>
                        @endif
                    </div>

                    {{-- Price / Reorder / Stock --}}
                    <div class="d-flex justify-content-between align-items-center py-2 border-top" style="font-size: 0.85rem;">
                        <div>
                            <span class="text-muted">Price</span>
                            <span class="fw-bold ms-1">{{ $product->price }}</span>
                        </div>
                        <div>
                            <span class="text-muted">Reorder</span>
                            <span class="fw-bold ms-1">{{ $product->reorder }}</span>
                        </div>
                        <div>
                            <span class="text-muted">In Hand</span>
                            @if($product->inventory)
                                <span class="fw-bold ms-1">{{ number_format($stockBalances[$product->id] ?? 0) }}</span>
                            @else
                                <span class="text-muted ms-1">-</span>
                            @endif
                        </div>
                    </div>

                    {{-- Actions --}}
                    <div class="d-flex flex-wrap gap-1 pt-2 border-top">
                        @can('view inventory')
                            <a href="{{ route('product.history', $product->id) }}" class="btn btn-info btn-sm flex-fill">
                                <i class="fa fa-clock"></i> History
                            </a>
                        @endcan
                        @can('edit product')
                            <a href="#" wire:click="update({{ $product->id }})" class="btn btn-secondary btn-sm flex-fill"
                               data-coreui-toggle="modal" data-coreui-target="#modal-product">
                                <i class="fa fa-edit"></i> Edit
                            </a>
                        @endcan
                        @can('delete product')
                            <button type="button" class="btn btn-danger btn-sm flex-fill"
                                    wire:click="deleteConfirm({{$product->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalProduct">
                                <i class="fa fa-trash-alt"></i> Delete
                            </button>
                        @endcan
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center py-4 text-muted">
                    No products found.
                </div>
            </div>
        @endforelse

        <div class="mt-2">
            {{ $products->links() }}
        </div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-product" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered modal-lg" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif Product/ Service</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <div class="row gx-2">
                                <div class="col">
                                    <label for="type">Type <span class="text-danger">*</span></label>
                                    @if(!$update)
                                        <select wire:model.live="type"
                                                class="form-select @error('type') is-invalid @enderror"
                                                id="type">
                                            <option value="">Choose..</option>
                                            <option value="Material">Material</option>
                                            <option value="Product">Product</option>
                                            <option value="Service">Service</option>
                                        </select>
                                        @error('type')
                                        <div class="invalid-feedback">
                                            {{ $message }}
                                        </div>
                                        @enderror
                                    @else
                                        <input type="text" disabled value="{{ $type }}" class="form-control">
                                    @endif
                                </div>
                                <div class="col">
                                    <label for="category">Category <span class="text-danger">*</span></label>

                                    <select wire:model.live="category"
                                            class="form-select @error('category') is-invalid @enderror" id="category">
                                        <option value="">Choose..</option>
                                        @foreach($categories as $category)
                                            <option value="{{ $category->id }}">{{ $category->name }}</option>
                                        @endforeach
                                    </select>
                                    @error('category')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                            </div>
                        </div>

                        <div class="mb-4">
                            <div class="row gx-2">
                                <div class="col">
                                    <label for="unit">Unit <span class="text-danger">*</span></label>
                                    <select wire:model.live="unit" class="form-select @error('unit') is-invalid @enderror"
                                            id="unit">
                                        <option value="">Choose..</option>
                                        @foreach($units as $unit)
                                            <option value="{{ $unit->id }}">{{ $unit->name }}</option>
                                        @endforeach
                                    </select>
                                    @error('unit')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                                <div class="col">
                                    <label for="name">Name <span class="text-danger">*</span></label>
                                    <input type="text" wire:model.live="name"
                                           class="form-control @error('name') is-invalid @enderror" id="name">
                                    @error('name')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                            </div>
                        </div>

                        @if($type == 'Material')
                        <div class="mb-4">
                            <div class="row gx-2">
                                <div class="col">
                                    <label for="gsm">GSM</label>
                                    <input type="text" wire:model.live="gsm"
                                           class="form-control @error('gsm') is-invalid @enderror" id="gsm"
                                           placeholder="e.g., 80, 100, 120">
                                    @error('gsm')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                                <div class="col">
                                    <label for="size">Size</label>
                                    <input type="text" wire:model.live="size"
                                           class="form-control @error('size') is-invalid @enderror" id="size"
                                           placeholder="e.g., A4, 61x86cm">
                                    @error('size')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                            </div>
                        </div>
                        @endif

                        <div class="mb-4">
                            <div class="row gx-2">
                                <div class="col">
                                    <label for="price">Price @if($type == 'Product') <span
                                            class="text-danger">*</span> @endif</label>
                                    <input type="number" wire:model.live="price" step="0.01"
                                           class="form-control @error('price') is-invalid @enderror" id="price">
                                    @error('price')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                                <div class="col">
                                    <label for="reorder">Reorder @if($type == 'Material') <span
                                            class="text-danger">*</span> @endif</label>
                                    <input type="number" wire:model.live="reorder" step="0.01"
                                           class="form-control @error('reorder') is-invalid @enderror" id="reorder">
                                    @error('reorder')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                            </div>
                        </div>

                        <div class="mb-4">
                            <div class="row gx-2">
                                <div class="col">
                                    <label for="price">SKU Number</label>
                                    <input type="text" wire:model.live="sku"
                                           class="form-control @error('sku') is-invalid @enderror" id="sku">
                                    @error('sku')
                                    <div class="invalid-feedback">
                                        {{ $message }}
                                    </div>
                                    @enderror
                                </div>
                                <div class="col">
                                    <label for="inventory">Manage Inventory</label>
                                    @if(!$update)
                                        <select wire:model.live="inventory"
                                                class="form-control @error('inventory') is-invalid @enderror"
                                                id="inventory">
                                            <option value="0">No</option>
                                            <option value="1">Yes</option>
                                        </select>
                                        @error('inventory')
                                        <div class="invalid-feedback">
                                            {{ $message }}
                                        </div>
                                        @enderror
                                    @else
                                        <input type="text" disabled value="{{ $inventory? 'Yes':'No' }}"
                                               class="form-control">
                                    @endif
                                </div>
                            </div>

                        </div>

                        <div class="mb-4">
                            <div class="row gx-2">

                                @if($type != 'Material')
                                    @if($type == 'Product')
                                    <div class="col">
                                        <label for="sell_account">Cost of Goods Account <span class="text-danger">*</span></label>
                                        <select wire:model.live="sell_account" class="form-select @error('sell_account') is-invalid @enderror" id="sell_account">
                                            <option value="">Choose...</option>
                                            @foreach($sell_accounts as $account)
                                                <option value="{{ $account->id }}">{{ $account->name }} ({{ $account->code }})</option>
                                            @endforeach
                                        </select>
                                        @error('sell_account')
                                        <div class="invalid-feedback">{{ $message }}</div>
                                        @enderror
                                    </div>
                                    <div class="col">
                                        <label for="buy_account">Inventory Account <span class="text-danger">*</span></label>
                                        <select wire:model.live="buy_account" class="form-select @error('buy_account') is-invalid @enderror" id="buy_account">
                                            <option value="">Choose...</option>
                                            @foreach($buy_accounts as $account)
                                                <option value="{{ $account->id }}">{{ $account->name }} ({{ $account->code }})</option>
                                            @endforeach
                                        </select>
                                        @error('buy_account')
                                        <div class="invalid-feedback">{{ $message }}</div>
                                        @enderror
                                    </div>
                                    @endif
                                    <div class="col">
                                        <label for="revenue_account">Revenue Account @if($type != 'Material')<span class="text-danger">*</span>@endif</label>
                                        <select wire:model.live="revenue_account" class="form-select @error('revenue_account') is-invalid @enderror" id="revenue_account">
                                            <option value="">Choose...</option>
                                            @foreach($revenue_accounts as $account)
                                                <option value="{{ $account->id }}">{{ $account->name }} ({{ $account->code }})</option>
                                            @endforeach
                                        </select>
                                        @error('revenue_account')
                                        <div class="invalid-feedback">{{ $message }}</div>
                                        @enderror
                                    </div>
                                @else
                                    <div class="col">
                                        <label for="sell_account">Cost of Goods Account @if($type == 'Material')<span class="text-danger">*</span>@endif</label>
                                        <select wire:model.live="sell_account" class="form-select @error('sell_account') is-invalid @enderror" id="sell_account">
                                            <option value="">Choose...</option>
                                            @foreach($sell_accounts as $account)
                                                <option value="{{ $account->id }}">{{ $account->name }} ({{ $account->code }})</option>
                                            @endforeach
                                        </select>
                                        @error('sell_account')
                                        <div class="invalid-feedback">{{ $message }}</div>
                                        @enderror
                                    </div>
                                    <div class="col">
                                        <label for="buy_account">Inventory Account @if($type == 'Material')<span class="text-danger">*</span>@endif</label>
                                        <select wire:model.live="buy_account" class="form-select @error('buy_account') is-invalid @enderror" id="buy_account">
                                            <option value="">Choose...</option>
                                            @foreach($buy_accounts as $account)
                                                <option value="{{ $account->id }}">{{ $account->name }} ({{ $account->code }})</option>
                                            @endforeach
                                        </select>
                                        @error('buy_account')
                                        <div class="invalid-feedback">{{ $message }}</div>
                                        @enderror
                                    </div>
                                @endif

                            </div>

                        </div>
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

    <!-- Delete Modal -->
    <div wire:ignore.self class="modal fade" id="deleteModalProduct" data-backdrop="false" tabindex="-1" role="dialog"
         aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Delete Confirm </h5>
                </div>
                <div class="modal-body">
                    <p>Are you sure want to delete <strong>{{ $delete_confirm? $delete_confirm->name: ''  }}</strong> ?</p>
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
</div>
<!-- End of Modal Content -->

@push('scripts')
    <script>
        Livewire.on('closeModal', () => {
            $('#modal-product').modal('hide')
        })
    </script>
@endpush
