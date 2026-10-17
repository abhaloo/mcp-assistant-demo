<?php

namespace App\Http\Livewire;

use App\Models\Customer;
use Livewire\Component;
use Livewire\WithPagination;

class CustomerComponent extends Component
{
    use WithPagination;

    protected $paginationTheme = 'bootstrap';

    public $search_keyword = null;

    public $update = false;

    public $delete_confirm = null;

    public $customer_id;

    public $name;

    public $address;

    public $type;

    public $currency;

    public $zrb_number;

    public $tin_number;

    public $business_name;

    public $phone;

    public $email = null;

    protected $rules = [
        'name' => 'required|max:150|unique:customers',
        'type' => 'required',
        'address' => 'nullable|string',
        'phone' => 'nullable|string',
        'email' => 'nullable|email',
    ];

    public function create()
    {
        $this->resetField();
    }

    public function store()
    {
        abort(403);
    }

    public function update($customer_id)
    {

        $this->resetErrorBag();
        $this->update = true;
        $this->customer_id = $customer_id;

        $customer = Customer::findOrFail($customer_id);
        $this->name = $customer->name;
        $this->type = $customer->type;
        $this->address = $customer->address;
        $this->phone = $customer->phone;
        $this->email = $customer->email;
        $this->zrb_number = $customer->zrb_number;
        $this->tin_number = $customer->tin_number;
        $this->business_name = $customer->business_name;
        $this->currency = $customer->currency_id;
    }

    public function deleteConfirm(Customer $customer)
    {
        $this->delete_confirm = $customer;
    }

    public function destroy()
    {
        abort(403);
    }

    private function resetField()
    {
        $this->reset('name', 'phone', 'email', 'address', 'currency', 'type', 'customer_id', 'zrb_number', 'tin_number', 'business_name', 'update');
    }

    public function render()
    {
        $customers = Customer::query()->with('currency')->latest();
        if ($this->search_keyword) {
            $customers->where('id', $this->search_keyword)
                ->orWhere('name', 'like', "%{$this->search_keyword}%");
        }

        $customers = $customers->paginate();

        return view('livewire.customer-component', [
            'customers' => $customers,
        ]);
    }
}
