<?php

namespace App\Http\Livewire\Dashboard;

use App\Models\Inventory;
use App\Support\DashboardPeriod;
use Livewire\Component;
use Livewire\WithPagination;

class LatestPurchasesComponent extends Component
{
    use WithPagination;

    protected $paginationTheme = 'mcp';

    public string $period = 'month';

    public function mount(?string $period = null): void
    {
        abort_unless(auth()->user()?->can('full dashboard'), 403);

        $this->period = DashboardPeriod::normalize($period ?? request('period', 'month'));
    }

    public function render()
    {
        abort_unless(auth()->user()?->can('full dashboard'), 403);

        [$start, $end] = DashboardPeriod::window($this->period);

        $inventories = Inventory::with(['supplier', 'inventoryItems'])
            ->whereBetween('inventory_date', [$start, $end])
            ->latest()
            ->paginate(5, ['*'], 'inventories_page');

        return view('livewire.dashboard.latest-purchases-component', [
            'inventories' => $inventories,
        ]);
    }
}
