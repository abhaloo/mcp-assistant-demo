<?php

namespace App\Http\Livewire\Dashboard;

use App\Models\Bill;
use App\Support\DashboardPeriod;
use Livewire\Component;
use Livewire\WithPagination;

class LatestInvoicesComponent extends Component
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

        $bills = Bill::with(['customer', 'billItems', 'payments'])
            ->whereBetween('created_at', [$start, $end])
            ->latest()
            ->paginate(5, ['*'], 'bills_page');

        return view('livewire.dashboard.latest-invoices-component', [
            'bills' => $bills,
        ]);
    }
}
