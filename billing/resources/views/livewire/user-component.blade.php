<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-4 mb-md-0">
            <h2 class="h4">Manage User</h2>
        </div>
        <div class="btn-toolbar mb-2 mb-md-0">

            @can('add user')
                <a href="#" wire:click="create" class="btn btn-sm btn-primary d-inline-flex align-items-center"
                   data-coreui-toggle="modal" data-coreui-target="#modal-user">
                    <i class="fa fa-plus"></i>
                    New User
                </a>
            @endcan
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
                <th class="border-gray-200">Username</th>
                <th class="border-gray-200">Email</th>
                <th class="border-gray-200">Created</th>
                <th class="border-gray-200">Department</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($users as $user)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $user->name }}</td>
                    <td>{{ $user->username }}</td>
                    <td>{{ $user->email }}</td>
                    <td>{{ $user->created_at->diffForHumans() }}</td>
                    <td>{{ $user->department->name ?? 'All Department' }}</td>

                    <td class="text-end">
                        @can('change permission')
                            <a href="{{ route('show.permission', $user) }}" class="btn btn-primary btn-sm">
                                <i class="fa fa-lock"></i>
                                Permissions
                            </a>
                        @endcan

                        @can('edit user')
                            <a href="#" wire:click="update({{ $user->id }})" class="btn btn-secondary btn-sm"
                               data-coreui-toggle="modal" data-coreui-target="#modal-user">
                                <i class="fa fa-edit"></i>
                                Edit</a>
                        @endcan

                        @can('delete user')
                            <button type="button" class="btn btn-danger btn-sm"
                                    wire:click="deleteConfirm({{$user->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModaluser">
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
            {{ $users->links() }}
        </div>
    </div>

    {{-- Mobile Card View --}}
    <div class="d-md-none">
        @forelse($users as $user)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <x-badge color="info">{{ $user->department->name ?? 'All Department' }}</x-badge>
                        <small class="text-muted">{{ $user->created_at->diffForHumans() }}</small>
                    </div>
                    <div class="fw-bold" style="font-size: 0.95rem">{{ $user->name }}</div>
                    <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem">
                        <span><i class="fa fa-user me-1"></i>{{ $user->username }}</span>
                        <span><i class="fa fa-envelope me-1"></i>{{ $user->email }}</span>
                    </div>
                    <div class="d-flex flex-wrap gap-1 pt-2 border-top">
                        @can('change permission')
                            <a href="{{ route('show.permission', $user) }}" class="btn btn-primary btn-sm flex-fill">
                                <i class="fa fa-lock"></i> Permissions
                            </a>
                        @endcan
                        @can('edit user')
                            <a href="#" wire:click="update({{ $user->id }})" class="btn btn-secondary btn-sm flex-fill"
                               data-coreui-toggle="modal" data-coreui-target="#modal-user">
                                <i class="fa fa-edit"></i> Edit
                            </a>
                        @endcan
                        @can('delete user')
                            <button type="button" class="btn btn-danger btn-sm flex-fill"
                                    wire:click="deleteConfirm({{$user->id}})"
                                    data-coreui-toggle="modal" data-coreui-target="#deleteModaluser">
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
        <div class="mt-2">{{ $users->links() }}</div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-user" tabindex="-1" role="dialog" aria-labelledby="modal-default"
         aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif user</h2>
                    <button type="button" class="btn-close" data-coreui-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="mb-4">
                            <label for="username">Username</label>
                            <input type="text" wire:model.live="username" @if($update) disabled @endif
                                   class="form-control @error('username') is-invalid @enderror" id="username">
                            @error('username')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="mb-4">
                            <label for="name">Full Name</label>
                            <input type="text" wire:model.live="name"
                                   class="form-control @error('name') is-invalid @enderror" id="name">
                            @error('name')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="mb-4">
                            <label for="department">Department</label>
                            <select wire:model.live="department"
                                    class="form-control @error('department') is-invalid @enderror" id="department">
                                <option value="">All Departments</option>
                                @forelse($departments as $department)
                                    <option value="{{ $department->id }}">{{ $department->name }}</option>
                                @empty
                                @endforelse
                            </select>
                            @error('department')
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
                        <div class="mb-4">
                            <label for="password">Password</label>
                            <input type="password" wire:model.live="password"
                                   class="form-control @error('password') is-invalid @enderror" id="password">
                            @error('password')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="mb-4">
                            <label for="password_confirmation">Confirm Password</label>
                            <input type="password" wire:model.live="password_confirmation"
                                   class="form-control @error('password_confirmation') is-invalid @enderror"
                                   id="password_confirmation">
                            @error('password_confirmation')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>
                        <div class="modal-footer">

                            <button type="button" wire:click.prevent="store" class="btn btn-secondary"> @if($update)
                                    Update @else Add @endif</button>
                            <button type="button" class="btn btn-link text-gray-600 ms-auto"
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
    <div wire:ignore.self class="modal fade" id="deleteModaluser" data-backdrop="false" tabindex="-1" role="dialog"
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
                $('#modal-user').modal('hide')
            })
        </script>
    @endpush
</div>
