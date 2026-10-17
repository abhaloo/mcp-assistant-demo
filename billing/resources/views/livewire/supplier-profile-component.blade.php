<div>
    <div class="mcp">
        <x-mcp.page-header>
            <x-slot:titleSlot>{{ $supplier->name }}</x-slot:titleSlot>
            <x-slot:titleAdornment>
                @canany(['view invoice','view quotation'])
                    <span class="ms-2 text-muted">Balance {{ number_format($supplier->inventories->sum('balance'), 2) }} {{ $supplier->currency->symbol }}</span>
                @endcanany
            </x-slot:titleAdornment>
            <x-slot:subtitleSlot>Type : {{ $supplier->type }} · Registered {{ $supplier->created_at->diffForHumans() }}</x-slot:subtitleSlot>
            <x-slot:actions>
                <x-mcp.btn variant="primary" size="sm" icon="fa-reply" :href="route('supplier.index')">Back</x-mcp.btn>
            </x-slot:actions>
        </x-mcp.page-header>

        <x-mcp.card>
            <div class="nav nav-tabs mb-4" id="nav-tab" role="tablist">
                <a class="nav-item nav-link active" id="nav-profile-tab" data-coreui-toggle="tab" href="#profile" role="tab"
                   aria-controls="nav-profile" aria-selected="true" wire:ignore>
                    <i class="fa fa-user"></i> Profile</a>
                @canany(['view invoice','view quotation'])
                    <a class="nav-item nav-link" id="nav-inventories-tab" data-coreui-toggle="tab" href="#inventories" role="tab"
                       aria-controls="nav-inventories" aria-selected="false" wire:ignore>
                        <i class="fa fa-coins"></i> Bills</a>
                @endcanany

                @can('supplier statement')
                    <a class="nav-item nav-link" id="nav-payments-tab" data-coreui-toggle="tab" href="#statement" role="tab"
                       aria-controls="nav-payments" aria-selected="false" wire:ignore>
                        <i class="fa fa-money-bill"></i> Statement</a>
                @endcan
            </div>

            <div class="tab-content pt-2" id="myTabContent">
                <div class="tab-pane fade show active" id="profile" role="tabpanel" aria-labelledby="profile-tab"
                     wire:ignore.self>
                    <x-mcp.deflist>
                        <x-mcp.deflist.row label="Reference">{{ $supplier->reference }}</x-mcp.deflist.row>
                        <x-mcp.deflist.row label="Email">{{ $supplier->email }}</x-mcp.deflist.row>
                        <x-mcp.deflist.row label="Phone">{{ $supplier->phone }}</x-mcp.deflist.row>
                        <x-mcp.deflist.row label="Address">{{ $supplier->address }}</x-mcp.deflist.row>
                    </x-mcp.deflist>
                </div>

                <div class="tab-pane fade" id="inventories" role="tabpanel" aria-labelledby="inventory-tab" wire:ignore.self>
                    {{-- ==================== DESKTOP TABLE ==================== --}}
                    <div class="d-none d-md-block">
                        <div class="mcp-table-wrap">
                            <table class="mcp-table mcp-table--supplier-bills">
                                <thead>
                                    <tr>
                                        <th>#</th>
                                        <th>Bill Id</th>
                                        <th>Bill Type</th>
                                        <th>Bill For</th>
                                        <th>Issue Date</th>
                                        <th class="mcp-td--right">Total</th>
                                        <th class="mcp-td--right">Balance</th>
                                        <th>Status</th>
                                        <th class="mcp-td--right">Action</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    @forelse($inventories as $inventory)
                                        <tr wire:key="inventory-{{ $inventory->id }}">
                                            <td class="mcp-td--mute">{{ $loop->index + 1 }}</td>
                                            <td>{{ $inventory->id }}</td>
                                            <td class="mcp-td--secondary">{{ $inventory->type }}</td>
                                            <td class="mcp-cell-strong">{{ $inventory->supplier->name }}</td>
                                            <td class="mcp-td--mono mcp-td--secondary">{{ $inventory->created_at->format('d-m-Y') }}</td>
                                            <td class="mcp-td--right mcp-td--mono">{{ number_format($inventory->amount(), 2) }}</td>
                                            <td class="mcp-td--right mcp-td--mono">{{ number_format($inventory->balance(), 2) }}</td>
                                            <td>{!! $inventory->balanceStatus() !!}</td>
                                            <td class="mcp-td--right">
                                                <div class="mcp-row-actions">
                                                    <x-mcp.icon-btn variant="primary" icon="fa-eye"
                                                        :href="route('inventory.show', $inventory)" title="Show" label="Show" />
                                                    @can('edit invoice')
                                                        <x-mcp.icon-btn icon="fa-edit"
                                                            :href="route('inventory.create', $inventory)" title="Edit" label="Edit" />
                                                    @endcan
                                                </div>
                                            </td>
                                        </tr>
                                    @empty
                                    @endforelse
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {{-- ==================== MOBILE CARDS ==================== --}}
                    <div class="d-md-none">
                        @forelse($inventories as $inventory)
                            <x-mcp.mobile-card>
                                <div class="mcp-mobile-card__head">
                                    <x-mcp.status kind="info" size="sm" :label="$inventory->type" />
                                    <span>{!! $inventory->balanceStatus() !!}</span>
                                </div>
                                <div class="mcp-mobile-card__title">{{ $inventory->supplier->name }}</div>
                                <div class="mcp-mobile-card__meta">
                                    <span><i class="fa fa-hashtag"></i> {{ $inventory->id }}</span>
                                    <span><i class="fa fa-calendar"></i> {{ $inventory->created_at->format('d-m-Y') }}</span>
                                </div>
                                <div class="mcp-mobile-card__meta">
                                    <span>Total: {{ number_format($inventory->amount(), 2) }}</span>
                                    <span class="fw-bold">Balance: {{ number_format($inventory->balance(), 2) }}</span>
                                </div>
                                <div class="mcp-mobile-card__actions">
                                    <x-mcp.btn variant="primary" size="sm" icon="fa-eye" :href="route('inventory.show', $inventory)">
                                        Show
                                    </x-mcp.btn>
                                    @can('edit invoice')
                                        <x-mcp.btn variant="secondary" size="sm" icon="fa-edit" :href="route('inventory.create', $inventory)">
                                            Edit
                                        </x-mcp.btn>
                                    @endcan
                                </div>
                            </x-mcp.mobile-card>
                        @empty
                            <x-mcp.empty title="No bills found" />
                        @endforelse
                    </div>

                    <div class="mt-3">
                        {{ $inventories->links() }}
                    </div>
                </div>

                <div class="tab-pane fade" id="statement" role="tabpanel" aria-labelledby="inventory-tab" wire:ignore.self>
                    @livewire('supplier-statement-component',['supplier' => $supplier->id])
                </div>
            </div>
        </x-mcp.card>
    </div>
</div>
