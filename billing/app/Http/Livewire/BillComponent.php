<?php

namespace App\Http\Livewire;

use App\Models\Bill;
use Livewire\Component;
use Livewire\WithPagination;

class BillComponent extends Component
{
    use WithPagination;

    protected $paginationTheme = 'mcp';

    public $search_keyword = null;

    public $delete_confirm = null;

    public $type = 'Invoice';

    public $filter_date_from = null;

    public $filter_date_to = null;

    public $sort_field = 'created_at';

    public $sort_direction = 'desc';

    protected $queryString = [
        'search_keyword' => ['except' => ''],
        'filter_date_from' => ['except' => ''],
        'filter_date_to' => ['except' => ''],
        'sort_field' => ['except' => 'created_at'],
        'sort_direction' => ['except' => 'desc'],
    ];

    public function mount($type)
    {
        $this->type = $type;
    }

    public function setSortField($field)
    {
        if ($this->sort_field === $field) {
            $this->sort_direction = $this->sort_direction === 'asc' ? 'desc' : 'asc';
        } else {
            $this->sort_field = $field;
            $this->sort_direction = 'desc';
        }
        $this->resetPage();
    }

    public function setSort($field, $direction)
    {
        $this->sort_field = $field;
        $this->sort_direction = $direction;
        $this->resetPage();
    }

    public function clearSort()
    {
        $this->sort_field = null;
        $this->sort_direction = null;
        $this->resetPage();
    }

    public function clearDateFilter()
    {
        $this->filter_date_from = null;
        $this->filter_date_to = null;
        $this->resetPage();
    }

    public function updatingSearchKeyword()
    {
        $this->resetPage();
    }

    public function updatingFilterDateFrom()
    {
        $this->resetPage();
    }

    public function updatingFilterDateTo()
    {
        $this->resetPage();
    }

    public function deleteConfirm(Bill $bill)
    {
        $this->delete_confirm = $bill;
    }

    public function destroy()
    {
        abort(403);
    }

    public function render()
    {

        $bills = Bill::query()
            ->with(['customer', 'workOrders', 'billItems', 'currency', 'payments'])
            // Index UI only needs zero-vs-nonzero journals; EXISTS beats correlated COUNT(*).
            ->withExists(['transactions as has_transactions'])
            ->where('type', $this->type);

        if ($this->search_keyword) {
            $bills->where(function ($q) {
                $q->whereHas('customer', function ($q) {
                    $q->where('name', 'like', "%{$this->search_keyword}%");
                })->orWhere('invoice_number', 'like', "%{$this->search_keyword}%");
            });
        }

        // Date range filters
        if ($this->filter_date_from) {
            $bills->whereDate('created_at', '>=', $this->filter_date_from);
        }
        if ($this->filter_date_to) {
            $bills->whereDate('created_at', '<=', $this->filter_date_to);
        }

        // Dynamic sorting
        if ($this->sort_field) {
            $bills->orderBy($this->sort_field, $this->sort_direction);
        }

        $bills = $bills->paginate();

        return view('livewire.bill-component', [
            'bills' => $bills,
        ]);
    }
}
