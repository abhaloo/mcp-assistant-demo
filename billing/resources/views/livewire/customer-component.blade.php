<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Manage <strong>Customers</strong></h1>
        </div>
        <div class="col">
            @can('add customer')
                <a href="#" wire:click="create" class="btn btn-sm btn-primary float-end"
                   data-coreui-toggle="modal" data-coreui-target="#modal-customer">
                    <span class="fa fa-plus"></span> New Customer
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
                <th class="border-gray-200">Customer Id</th>
                <th class="border-gray-200">Type</th>
                <th class="border-gray-200">Name</th>
                <th class="border-gray-200">Currency</th>
                <th class="border-gray-200">Phone</th>
                <th class="border-gray-200">Email</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($customers as $customer)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $customer->id }}</td>
                    <td>{{ $customer->type }}</td>
                    <td>{{ $customer->name }}</td>
                    <td>{{ $customer->currency->name }}</td>
                    <td>{{ $customer->phone }}</td>
                    <td>{{ $customer->email }}</td>

                    <td class="text-end">
                        <a href="{{ route('customer.show', $customer) }}" class="btn btn-primary btn-sm">
                            <i class="fa fa-eye"></i> Show
                        </a>

                        @can('edit customer')
                            <a href="#" wire:click="update({{ $customer->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-customer">
                                <i class="fa fa-edit"></i>
                                Edit</a>
                        @endcan

                        @can('delete customer')
                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$customer->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalcustomer">
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
            {{ $customers->links() }}
        </div>
    </div>

    <!-- Mobile Card View -->
    <div class="d-md-none">
        @forelse($customers as $customer)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <x-badge color="info">{{ $customer->type }}</x-badge>
                        <x-badge color="secondary">{{ $customer->currency->currency_code }}</x-badge>
                    </div>
                    <div class="fw-bold" style="font-size: 0.95rem">{{ $customer->name }}</div>
                    <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem">
                        <span><i class="fa fa-hashtag"></i> {{ $customer->id }}</span>
                        @if($customer->phone)
                            <span><i class="fa fa-phone"></i> {{ $customer->phone }}</span>
                        @endif
                        @if($customer->email)
                            <span><i class="fa fa-envelope"></i> {{ $customer->email }}</span>
                        @endif
                    </div>
                    <div class="d-flex flex-wrap gap-1 pt-2 border-top">
                        <a href="{{ route('customer.show', $customer) }}" class="btn btn-primary btn-sm flex-fill">
                            <i class="fa fa-eye"></i> Show
                        </a>
                        @can('edit customer')
                            <a href="#" wire:click="update({{ $customer->id }})" class="btn btn-secondary btn-sm flex-fill"
                               data-coreui-toggle="modal" data-coreui-target="#modal-customer">
                                <i class="fa fa-edit"></i> Edit
                            </a>
                        @endcan
                        @can('delete customer')
                            <button type="button" class="btn btn-danger btn-sm flex-fill"
                                    wire:click="deleteConfirm({{$customer->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModalcustomer">
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
        <div class="mt-2">{{ $customers->links() }}</div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-customer" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif customer</h2>
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
                            <label for="business_name">Business Name</label>
                            <input type="text" wire:model.live="business_name"
                                   class="form-control @error('business_name') is-invalid @enderror" id="business_name">
                            @error('business_name')
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
                    </div>
                    <div class="row">
                        <div class="mb-4 col-6">
                            <label for="phone">Phone</label>
                            <input type="text" wire:model.live="phone"
                                   class="form-control @error('phone') is-invalid @enderror" id="phone">
                            @error('phone')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        <div class="mb-4 col-6">
                            <label for="email">Email</label>
                            <input type="email" wire:model.live="email"
                                   class="form-control @error('email') is-invalid @enderror" id="email">
                            @error('email')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                    </div>

                    <div class="row">
                        <div class="mb-4 col-6">
                            <label for="zrb_number">ZRB Number</label>
                            <input type="text" wire:model.live="zrb_number"
                                   class="form-control @error('zrb_number') is-invalid @enderror" id="zrb_number">
                            @error('zrb_number')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="mb-4 col-6">
                            <label for="tin_number">TIN Number</label>
                            <input type="text" wire:model.live="tin_number"
                                   class="form-control @error('tin_number') is-invalid @enderror" id="tin_number">
                            @error('tin_number')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
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
    <!-- End of Modal Content -->

    <!-- Delete Modal -->
    <div wire:ignore.self class="modal fade" id="deleteModalcustomer" data-backdrop="false" tabindex="-1" role="dialog"
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
                $('#modal-customer').modal('hide')
            })
        </script>
    @endpush
</div>
