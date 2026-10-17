<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center  pb-2 mb-3 border-bottom">
        <h1 class="h2">Outstanding Payable Report</h1>
        <div class="btn-toolbar mb-2 mb-md-0">
        </div>
    </div>

        <div class="card card-body d-none d-md-block">
            <table class="table table-hover table-bordered table-sm">
                <tr class="bg-light">
                    <th>#</th>
                    <th>Supplier Id</th>
                    <th>Supplier Name</th>
                    <th class="text-end">Balance</th>
                </tr>

                @forelse($suppliers as $supplier)
                    @if($supplier->inventories->sum('balance') > 0)
                        <tr>
                            <td>{{$loop->index + 1}}</td>
                            <td>{{$supplier->id}}</td>
                            <td>{{$supplier->name}}</td>
                            <td class="text-end"><strong>{{number_format($supplier->inventories->sum('balance'), 2)}}</strong></td>
                        </tr>
                    @endif
                @empty
                @endforelse

            </table>
        </div>

        {{-- Mobile View --}}
        <div class="d-md-none">
            @php $hasRecords = false; @endphp
            @forelse($suppliers as $supplier)
                @if($supplier->inventories->sum('balance') > 0)
                    @php $hasRecords = true; @endphp
                    <div class="card border-0 shadow-sm mb-2">
                        <div class="card-body p-3 d-flex justify-content-between align-items-center">
                            <div>
                                <div class="fw-bold">{{ $supplier->name }}</div>
                                <small class="text-muted">ID: {{ $supplier->id }}</small>
                            </div>
                            <div class="fw-bold text-success">{{ number_format($supplier->inventories->sum('balance'), 2) }}</div>
                        </div>
                    </div>
                @endif
            @empty
            @endforelse
            @if(!$hasRecords)
                <div class="card border-0 shadow-sm mb-2">
                    <div class="card-body p-3 text-center py-4 text-muted">
                        No outstanding payables found.
                    </div>
                </div>
            @endif
        </div>

</div>
