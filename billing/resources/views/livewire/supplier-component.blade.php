<div>
    <div class="mcp">
        <x-mcp.page-header>
            <x-slot:titleSlot>
                <span class="mcp-page-header__title-soft">Manage</span> Suppliers
            </x-slot:titleSlot>
            <x-slot:actions>
                @can('add supplier')
                    <x-mcp.btn variant="primary" icon="fa-plus" href="#"
                        wire:click="create"
                        data-coreui-toggle="modal" data-coreui-target="#modal-supplier">
                        New Supplier
                    </x-mcp.btn>
                @endcan
            </x-slot:actions>
        </x-mcp.page-header>

        <x-mcp.toolbar>
            <x-mcp.search wireModel="search_keyword" placeholder="Search by anything" />
        </x-mcp.toolbar>

        {{-- ==================== DESKTOP TABLE ==================== --}}
        <div class="d-none d-md-block">
            <x-mcp.card flush>
                <div class="mcp-table-wrap" wire:loading.class="mcp-skeleton-refreshing" wire:loading.attr="aria-busy">
                    <table class="mcp-table mcp-table--suppliers">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Supplier Id</th>
                                <th>Type</th>
                                <th>Name</th>
                                <th>Currency</th>
                                <th>Phone</th>
                                <th>Email</th>
                                <th class="mcp-td--right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            @forelse($suppliers as $supplier)
                                @include('livewire.partials.mcp-suppliers-table-row', ['supplier' => $supplier, 'index' => $loop->index + 1])
                            @empty
                                <tr>
                                    <td colspan="8">
                                        <x-mcp.empty title="No records found." />
                                    </td>
                                </tr>
                            @endforelse
                        </tbody>
                    </table>
                </div>
                {{ $suppliers->links() }}
            </x-mcp.card>
        </div>

        {{-- ==================== MOBILE CARDS ==================== --}}
        <div class="d-md-none">
            @forelse($suppliers as $supplier)
                <x-mcp.mobile-card>
                    <div class="mcp-mobile-card__head">
                        <x-mcp.status kind="info" size="sm" :label="$supplier->type" />
                        <x-mcp.status kind="neutral" size="sm" :label="$supplier->currency->currency_code" />
                    </div>

                    <div class="mcp-mobile-card__title">{{ $supplier->name }}</div>

                    <div class="mcp-mobile-card__meta">
                        <span><i class="fa fa-hashtag"></i> {{ $supplier->id }}</span>
                        @if($supplier->phone)
                            <span><i class="fa fa-phone"></i> {{ $supplier->phone }}</span>
                        @endif
                        @if($supplier->email)
                            <span><i class="fa fa-envelope"></i> {{ $supplier->email }}</span>
                        @endif
                    </div>

                    @if($supplier->id != 1)
                        <div class="mcp-mobile-card__actions">
                            @can('view supplier')
                                <x-mcp.btn variant="primary" size="sm" icon="fa-eye" :href="route('supplier.show', $supplier->id)">
                                    Show
                                </x-mcp.btn>
                            @endcan
                            @can('edit supplier')
                                <x-mcp.btn variant="secondary" size="sm" icon="fa-edit"
                                    wire:click="update({{ $supplier->id }})"
                                    data-coreui-toggle="modal" data-coreui-target="#modal-supplier">
                                    Edit
                                </x-mcp.btn>
                            @endcan
                            @can('delete supplier')
                                <x-mcp.btn variant="danger" size="sm" icon="fa-trash-alt"
                                    wire:click="deleteConfirm({{ $supplier->id }})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalsupplier">
                                    Delete
                                </x-mcp.btn>
                            @endcan
                        </div>
                    @endif
                </x-mcp.mobile-card>
            @empty
                <x-mcp.empty title="No records found." />
            @endforelse

            <div class="mt-3">{{ $suppliers->links() }}</div>
        </div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-supplier" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif supplier</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <label for="type">Type <span class="text-danger">*</span></label>
                            <select wire:model.live="type" class="form-control @error('type') is-invalid @enderror"
                                    id="type">
                                <option value="">Choose..</option>
                                <option value="Individual">Individual</option>
                                <option value="Company">Company</option>
                                <option value="Government Sector">Government Sector</option>
                            </select>
                            @error('type')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="currency">Currency <span class="text-danger">*</span></label>
                            @if($update)
                                <input type="text" disabled
                                       value="{{ \App\Models\Currency::find($currency)?->name ?? 'N/A' }}"
                                       class="form-control">
                            @else
                                <select wire:model.live="currency"
                                        class="form-control @error('currency') is-invalid @enderror" id="currency">
                                    <option value="">Choose..</option>
                                    @forelse($currencies as $currency)
                                        <option value="{{ $currency->id }}">{{ $currency->name }}
                                            ({{ $currency->currency_code }})
                                        </option>
                                    @empty
                                    @endforelse
                                </select>
                                @error('currency')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            @endif
                        </div>

                        <div class="mb-4">
                            <label for="name">Name <span class="text-danger">*</span></label>
                            <input type="text" wire:model.live="name"
                                   class="form-control @error('name') is-invalid @enderror" id="name">
                            @error('name')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="address">Address</label>
                            <input type="text" wire:model.live="address"
                                   class="form-control @error('address') is-invalid @enderror" id="address">
                            @error('address')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="phone">Phone</label>
                            <input type="text" wire:model.live="phone"
                                   class="form-control @error('phone') is-invalid @enderror" id="phone">
                            @error('phone')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4">
                            <label for="email">Email</label>
                            <input type="email" wire:model.live="email"
                                   class="form-control @error('email') is-invalid @enderror" id="email">
                            @error('email')
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
    <div wire:ignore.self class="modal fade" id="deleteModalsupplier" data-backdrop="false" tabindex="-1" role="dialog"
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

    @script
        <script>
            $wire.on('closeModal', () => {
                $('#modal-supplier').modal('hide');
            });
        </script>
    @endscript
</div>
