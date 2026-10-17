<?php

namespace App\Http\Livewire;

use App\Models\Customer;
use Livewire\Attributes\On;
use Livewire\Component;

class CustomerSearchComponent extends Component
{
    public $query = '';

    public $customers = [];

    public $selectedCustomer = null;

    #[On('resetCustomerSearch')]
    public function resetFields()
    {
        $this->query = '';
        $this->customers = [];
        $this->selectedCustomer = null;
    }

    public function selectCustomer($customerId)
    {
        $customer = Customer::find($customerId)?->toArray();

        if ($customer) {
            $this->selectedCustomer = $customer;
            $this->query = $customer['name'];
            $this->customers = [];
            $this->dispatch('customerSelected', customerId: $customer['id']);
        }
    }

    public function closeDropdown()
    {
        $this->customers = [];
    }

    public function updatedQuery()
    {
        if (empty($this->query)) {
            $this->customers = [];
            $this->selectedCustomer = null;

            return;
        }

        $this->selectedCustomer = null;

        $this->customers = Customer::query()
            ->where('name', 'like', "%{$this->query}%")
            ->orWhere('phone', 'like', "%{$this->query}%")
            ->orWhere('business_name', 'like', "%{$this->query}%")
            ->orderBy('name')
            ->limit(10)
            ->get()
            ->toArray();
    }

    public function render()
    {
        return view('livewire.customer-search-component');
    }
}
