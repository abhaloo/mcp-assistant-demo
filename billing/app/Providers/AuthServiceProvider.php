<?php

namespace App\Providers;

use App\Models\Bill;
use App\Models\Customer;
use App\Models\CustomerOrder;
use App\Models\WorkOrder;
use App\Policies\BillPolicy;
use App\Policies\CustomerOrderPolicy;
use App\Policies\CustomerPolicy;
use App\Policies\WorkOrderPolicy;
use Illuminate\Foundation\Support\Providers\AuthServiceProvider as ServiceProvider;

class AuthServiceProvider extends ServiceProvider
{
    /**
     * The model to policy mappings for the application.
     *
     * @var array<class-string, class-string>
     */
    protected $policies = [
        WorkOrder::class => WorkOrderPolicy::class,
        Customer::class => CustomerPolicy::class,
        Bill::class => BillPolicy::class,
        CustomerOrder::class => CustomerOrderPolicy::class,
    ];

    /**
     * Register any authentication / authorization services.
     *
     * @return void
     */
    public function boot()
    {
        $this->registerPolicies();

        //
    }
}
