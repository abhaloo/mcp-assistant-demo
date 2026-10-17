<div>
    <div class="row">
        <div class="col-12">
            <h1 class="h3 mb-3">{{ $product->name }} <strong>History</strong></h1>
        </div>
        <div class="col-12">
            <a href="{{ route('product.index') }}" class="btn btn-primary float-end mb-2"><i class="fa fa-reply"></i> Back </a>
            <table class="table table-hover table-bordered bg-body">
                <tr class="bg-secondary text-white">
                    <th>#</th>
                    <th>Date & Time</th>
                    <th>Warehouse</th>
                    <th>Activity</th>
                    <th>Quantity</th>
                </tr>

                @forelse($product->inventory_entries as $entry)
                    <tr>
                        <td>{{ $loop->index + 1 }}</td>
                        <td>{{ $entry->created_at }}</td>
                        <td>{{ $entry->inventory->warehouse->name ?? 'N/A' }}</td>
                        @if($entry->inventory->type == 'Issue')
                            <td><x-badge color="warning">ISSUING</x-badge></td>
                        @elseif($entry->inventory->type == 'Receive')
                            <td><x-badge color="primary">RECEIVING</x-badge></td>
                        @elseif($entry->inventory->type == 'Adjustment')
                            <td><x-badge color="success">ADJUSTMENT</x-badge></td>
                        @elseif($entry->inventory->type == 'Transfer')
                            <td><x-badge color="success">TRANSFER</x-badge></td>
                        @else
                            <td><x-badge color="warning">SELLING</x-badge></td>
                        @endif
                        <td>{{ $entry->quantity }}</td>
                    </tr>
                @empty
                @endforelse
                @forelse($product->billItems as $bill)
                    <tr>
                        <td>{{ $loop->index + 1 }}</td>
                        <td>{{ $bill->created_at }}</td>
                        <td>{{ $bill->bill->warehouse->name ?? 'N/A' }}</td>
                        @if($bill->bill->type == 'Issue')
                            <td><x-badge color="warning">ISSUING</x-badge></td>
                        @elseif($bill->bill->type == 'Receive')
                            <td><x-badge color="primary">RECEIVING</x-badge></td>
                        @else
                            <td><x-badge color="warning">SELLING</x-badge></td>
                        @endif
                        <td>{{ $bill->quantity }}</td>
                    </tr>
                @empty
                @endforelse
            </table>
        </div>
    </div>
</div>
