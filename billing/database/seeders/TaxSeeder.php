<?php

namespace Database\Seeders;

use App\Models\Account;
use App\Models\Tax;
use Illuminate\Database\Seeder;

class TaxSeeder extends Seeder
{
    /**
     * Run the database seeds.
     *
     * @return void
     */
    public function run()
    {
        $account = Account::create([
            'name' => 'VAT Account',
            'account_type' => Account::CONTROL,
            'currency_id' => 1,
            'entity_id' => 1,
            'isSystemAccount' => true,
        ]);

        Account::create([
            'name' => 'Output Tax Payable',
            'account_type' => Account::CURRENT_LIABILITY,
            'currency_id' => 1,
            'entity_id' => 1,
            'isSystemAccount' => true,
        ]);

        Tax::create([
            'name' => 'VAT',
            'rate' => 15,
            'account_id' => $account->id,
        ]);
    }
}
