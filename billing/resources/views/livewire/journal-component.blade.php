<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-4 mb-md-0">
            <h2 class="h4">Manage Journal Entries</h2>
        </div>
        <div class="btn-toolbar mb-2 mb-md-0">
            @can('add journal entry')
                <a href="#" class="btn btn-sm btn-primary float-end"
                   data-coreui-toggle="modal" data-coreui-target="#modal-gl">
                    <svg class="icon icon-xs me-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"
                         xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                              d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>
                    </svg>
                    New Entry
                </a>
            @endcan
        </div>
    </div>
    <div class="table-settings mb-4">
        <div class="row align-items-center justify-content-between">
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
    <div class="card card-body border-0 shadow table-wrapper table-responsive d-none d-md-block">
        <table class="table table-hover table-sm table-bordered">
            <thead>
            <tr>
                <th class="border-gray-200">#</th>
                <th class="border-gray-200">Document No.</th>
                <th class="border-gray-200">Bill No.</th>
                <th class="border-gray-200">Post Date</th>
                <th class="border-gray-200">Value Date</th>
                <th class="border-gray-200">Debit Account</th>
                <th class="border-gray-200">Credit Account</th>
                <th class="border-gray-200 text-end">Amount</th>
                <th class="border-gray-200">Narration</th>
                <th class="border-gray-200 text-center">Actions</th>
            </tr>
            </thead>
            <tbody>
            @forelse($journals as $journal)
                <tr>
                    <td>{{ $loop->index + 1 }}</td>
                    <td>{{ $journal->document_no }}</td>
                    <td>{{ $journal->bill_id }}</td>
                    <td>{{ $journal->created_at }}</td>
                    <td>{{ $journal->post_date }}</td>
                    <td>{{ $journal->debit_account}}</td>
                    <td>{{ $journal->credit_account }}</td>
                    <td class="text-success fw-bold text-end">{{ number_format($journal->amount, 2) }}</td>
                    <td>{{ $journal->narration }}</td>


                    <td class="text-end">
                        <button type="button" class="btn btn-danger btn-sm"
                                wire:click="deleteConfirm({{$journal->document_no}})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModaljournal">
                            <i class="fa fa-trash-alt"></i> Reverse
                        </button>
                    </td>
                </tr>
            @empty
            @endforelse
            </tbody>
        </table>

        <div class="card-footer px-3 border-0">
            {{ $journals->links() }}
        </div>
    </div>

    {{-- Mobile Card View --}}
    <div class="d-md-none">
        @forelse($journals as $journal)
            <div class="card border-0 shadow-sm mb-2">
                <div class="card-body p-3">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <span class="small text-muted">{{ $journal->document_no }}</span>
                        <span class="text-success fw-bold">{{ number_format($journal->amount, 2) }}</span>
                    </div>
                    <div class="fw-bold" style="font-size: 0.95rem">{{ \Illuminate\Support\Str::limit($journal->narration, 50) }}</div>
                    <div class="d-flex flex-wrap gap-3 text-muted mb-2" style="font-size: 0.8rem">
                        <span><i class="fa fa-calendar me-1"></i>{{ $journal->post_date }}</span>
                        @if($journal->bill_id)
                            <span><i class="fa fa-hashtag me-1"></i>Bill {{ $journal->bill_id }}</span>
                        @endif
                    </div>
                    <div class="d-flex align-items-center gap-1 border-top py-2" style="font-size: 0.85rem">
                        <span class="text-muted">{{ $journal->debit_account }}</span>
                        <i class="fa fa-arrow-right text-primary mx-1"></i>
                        <span class="text-muted">{{ $journal->credit_account }}</span>
                    </div>
                    <div class="d-flex flex-wrap gap-1 pt-2 border-top">
                        <button type="button" class="btn btn-danger btn-sm flex-fill"
                                wire:click="deleteConfirm({{$journal->document_no}})"
                                data-coreui-toggle="modal" data-coreui-target="#deleteModaljournal">
                            <i class="fa fa-trash-alt"></i> Reverse
                        </button>
                    </div>
                </div>
            </div>
        @empty
            <div class="card border-0 shadow-sm">
                <div class="card-body text-center py-4 text-muted">No records found.</div>
            </div>
        @endforelse
        <div class="mt-2">{{ $journals->links() }}</div>
    </div>

    <!-- Modal GL Post -->
    <div class="modal fade" wire:ignore.self id="modal-gl" tabindex="-1" role="dialog"
         aria-labelledby="modal-default" aria-hidden="true">
        <div class="modal-dialog modal-xl modal-dialog-centered" role="document">
            <div class="modal-content">
                <div class="modal-body">
                    @livewire('g-l-component')
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary text-gray-600 ms-auto"
                                data-coreui-dismiss="modal">
                            Close
                        </button>
                    </div>

                </div>
            </div>
        </div>
    </div>
    <!-- End of Modal GL Post -->

    <!-- Delete Modal -->
    <div wire:ignore.self class="modal fade" id="deleteModaljournal" data-backdrop="false" tabindex="-1" role="dialog"
         aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog" role="document">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="exampleModalLabel">Reverse Confirm </h5>
                </div>
                <div class="modal-body">
                    <p>Put in a reason for this reverse</p>
                    <label>
                        <textarea class="form-control col-xs-12" rows="3" cols="50" wire:model.live="reason"></textarea>
                    </label>
                    @error('reason')
                    <div class="invalid-feedback">
                        {{ $message }}
                    </div>
                    @enderror
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary close-btn" data-coreui-dismiss="modal">Cancel
                    </button>
                    <button type="button" wire:click.prevent="destroy()" class="btn btn-danger close-modal"
                            data-coreui-dismiss="modal">Yes, Reverse
                    </button>
                </div>
            </div>
        </div>
    </div>

    @push('scripts')
        <script>
            Livewire.on('closeModal', () => {
                $('#modal-journal').modal('hide')
            })
        </script>
    @endpush
</div>
