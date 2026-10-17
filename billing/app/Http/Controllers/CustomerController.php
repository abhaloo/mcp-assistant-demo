<?php

namespace App\Http\Controllers;

use App\Models\Customer;

class CustomerController extends Controller
{
    public function show(Customer $customer)
    {
        $this->authorize('view', $customer);

        $customer->load('bills', 'bills.payments');

        return view('customers.show', compact('customer'));
    }
}
