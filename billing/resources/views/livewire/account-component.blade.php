<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-4 mb-md-0">
            <h2 class="h4">Manage Accounts</h2>
        </div>
        <div class="btn-toolbar mb-2 mb-md-0">
            @can('add account')
                <a href="#" wire:click="create" class="btn btn-sm btn-primary d-inline-flex align-items-center"
                   data-coreui-toggle="modal" data-coreui-target="#modal-account">
                    <i class="fa fa-plus"></i>
                    New Account
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
                <th class="border-gray-200">Code</th>
                <th class="border-gray-200">Name</th>
                <th class="border-gray-200">Account Type</th>
                <th class="border-gray-200">Currency</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($accounts as $account)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $account->code }}</td>
                    <td>{{ $account->name }}</td>
                    <td>{{ $account->account_type }}</td>
                    <td>{{ $account->currency->name }}</td>

                    <td class="text-end">
                        @if(!$account->isSystemAccount)
                            @can('edit account')
                                <a href="#" wire:click="update({{ $account->id }})" class="btn btn-secondary btn-sm"
                                   data-coreui-toggle="modal" data-coreui-target="#modal-account">
                                    <i class="fa fa-edit"></i>
                                    Edit</a>
                            @endcan

                            @can('delete account')
                                <button type="button" class="btn btn-danger btn-sm"
                                        wire:click="deleteConfirm({{$account->id}})"
                                        data-coreui-toggle="modal" data-coreui-target="#deleteModalaccount">
                                    <i class="fa fa-trash-alt"></i> Delete
                                </button>
                            @endcan
                        @endif
                    </td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $accounts->links() }}
        </div>
    </div>

    {{-- Mobile Card View --}}
    <div class="d-md-none">
        @forelse($accounts as $account)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <x-badge color="info">{{ $account->account_type }}</x-badge>
                        <x-badge color="secondary">{{ $account->currency->name }}</x-badge>
                    </div>
                    <div class="fw-bold" style="font-size: 0.95rem">{{ $account->name }}</div>
                    <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem">
                        <span><i class="fa fa-hashtag me-1"></i>{{ $account->code }}</span>
                    </div>
                    @if(!$account->isSystemAccount)
                        <div class="d-flex flex-wrap gap-1 pt-2 border-top">
                            @can('edit account')
                                <a href="#" wire:click="update({{ $account->id }})" class="btn btn-secondary btn-sm flex-fill"
                                   data-coreui-toggle="modal" data-coreui-target="#modal-account">
                                    <i class="fa fa-edit"></i> Edit
                                </a>
                            @endcan
                            @can('delete account')
                                <button type="button" class="btn btn-danger btn-sm flex-fill"
                                        wire:click="deleteConfirm({{$account->id}})"
                                        data-coreui-toggle="modal" data-coreui-target="#deleteModalaccount">
                                    <i class="fa fa-trash-alt"></i> Delete
                                </button>
                            @endcan
                        </div>
                    @endif
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center py-4 text-muted">No records found.</div>
            </div>
        @endforelse
        <div class="mt-2">{{ $accounts->links() }}</div>
    </div>

    <!-- Modal Content -->
    <div class="modal fade" wire:ignore.self id="modal-account" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h2 class="h6 modal-title">@if($update) Update @else Add @endif account</h2>
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
                        <div class="mb-4">
                            <label for="account_type">Account Type</label>
                            <select wire:model.live="account_type" class="form-control" id="account_type">
                                <option>--Select--</option>
                                <optgroup label="Asset Accounts"></optgroup>
                                <option> {{ \App\Models\Account::BANK }}</option>
                                <option> {{ \App\Models\Account::CASH }}</option>
                                <option> {{ \App\Models\Account::CURRENT_ASSET }}</option>
                                <option> {{ \App\Models\Account::NON_CURRENT_ASSET }}</option>
                                <option> {{ \App\Models\Account::CONTRA_ASSET }}</option>
                                <option> {{ \App\Models\Account::INVENTORY }}</option>
                                <option> {{ \App\Models\Account::RECEIVABLE }}</option>
                                <optgroup label="Liability Accounts"></optgroup>
                                <option> {{ \App\Models\Account::NON_CURRENT_LIABILITY }}</option>
                                <option> {{ \App\Models\Account::CURRENT_LIABILITY }}</option>
                                <option> {{ \App\Models\Account::CONTROL }}</option>
                                <option> {{ \App\Models\Account::PAYABLE }}</option>
                                <option> {{ \App\Models\Account::EQUITY }}</option>
                                <optgroup label="Operations Accounts"></optgroup>
                                <option> {{ \App\Models\Account::OPERATING_REVENUE }}</option>
                                <option> {{ \App\Models\Account::OPERATING_EXPENSE }}</option>
                                <optgroup label="Non-Operations Accounts"></optgroup>
                                <option> {{ \App\Models\Account::NON_OPERATING_REVENUE }}</option>
                                <option> {{ \App\Models\Account::DIRECT_EXPENSE }}</option>
                                <option> {{ \App\Models\Account::OVERHEAD_EXPENSE }}</option>
                                <option> {{ \App\Models\Account::OTHER_EXPENSE }}</option>
                                <optgroup label="Miscellaneous Accounts"></optgroup>
                                <option> {{ \App\Models\Account::RECONCILIATION }}</option>

                            </select>
                            @error('account_type')
                            <div class="invalid-feedback">
                                {{ $message }}
                            </div>
                            @enderror
                        </div>

                        @if($account_type == 'BANK')
                            <div class="mb-4">
                                <label for="bank_name">Bank Name</label>
                                <input type="text" wire:model.live="bank_name"
                                       class="form-control @error('bank_name') is-invalid @enderror" id="bank_name">
                                @error('bank_name')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            </div>

                            <div class="mb-4">
                                <label for="account_name">Account Name</label>
                                <input type="text" wire:model.live="account_name"
                                       class="form-control @error('account_name') is-invalid @enderror" id="account_name">
                                @error('account_name')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            </div>
                            <div class="mb-4">
                                <label for="account_number">Account Number</label>
                                <input type="text" wire:model.live="account_number"
                                       class="form-control @error('account_number') is-invalid @enderror" id="account_number">
                                @error('account_number')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            </div>

                            <div class="mb-4">
                                <label for="swift_code">Swift Code</label>
                                <input type="text" wire:model.live="swift_code"
                                       class="form-control @error('swift_code') is-invalid @enderror" id="swift_code">
                                @error('swift_code')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            </div>
                        @endif

                        <div class="mb-4">
                            <label for="currency">Currency</label>
                            @if($update && $hasTransactions)
                                {{-- Disabled when account has transactions --}}
                                <input type="text" value="{{ \App\Models\Currency::find($currency)?->name ?? 'Unknown' }}" disabled
                                       class="form-control">
                                <small class="text-muted">Currency cannot be changed for accounts with transactions.</small>
                            @else
                                {{-- Editable for new accounts or accounts without transactions --}}
                                <select wire:model.live="currency"
                                        class="form-control @error('currency') is-invalid @enderror"
                                        id="currency">
                                    <option value="">--Select--</option>
                                    @foreach($currencies as $cy)
                                        <option value="{{ $cy->id }}" @if($currency == $cy->id) selected @endif>{{ $cy->name }}</option>
                                    @endforeach
                                </select>
                                @error('currency')
                                <div class="invalid-feedback">
                                    {{ $message }}
                                </div>
                                @enderror
                            @endif
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
    <div wire:ignore.self class="modal fade" id="deleteModalaccount" data-backdrop="false" tabindex="-1" role="dialog"
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
                $('#modal-account').modal('hide')
            })
        </script>
    @endpush
</div>
