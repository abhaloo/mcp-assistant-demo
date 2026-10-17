<?php

namespace App\Http\Livewire;

use App\Models\Currency;
use App\Models\Customer;
use App\Reports\CustomerStatement;
use Livewire\Component;

class CustomerStatementComponent extends Component
{
    public $from_date;

    public $to_date;

    public $customer = null;

    protected $statement = null;

    public function mount(Customer $customer)
    {
        $this->customer = $customer;
    }

    public function filter()
    {
        if ($this->from_date && $this->to_date) {
            $this->statement = new CustomerStatement(
                $this->customer->id,
                $this->from_date,
                $this->to_date,
                $this->customer->currency_id,
            );

            $this->statement = $this->statement->run();
        }

    }

    public function render()
    {
        return view('livewire.customer-statement-component', [
            'statement' => $this->statement,
            'currencies' => Currency::all(),
        ]);
    }
}
