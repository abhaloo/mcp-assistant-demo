<?php

namespace App\Http\Livewire;

use App\Models\Bill;
use Livewire\Component;
use Livewire\WithPagination;

class CustomerProfileComponent extends Component
{
    use WithPagination;

    protected $paginationTheme = 'bootstrap';

    public $customer;

    public function mount($customer)
    {
        $this->customer = $customer->load('payable_bills', 'bills');
    }

    public function render()
    {
        $bills = Bill::with(['transactions', 'billItems', 'payments'])->where('customer_id', $this->customer->id)
            ->latest()->paginate(10, ['*'], 'billsPage');

        return view('livewire.customer-profile-component', [
            'customer' => $this->customer,
            'bills' => $bills,
        ]);
    }
}
