<?php

namespace App\Console\Commands;

use App\Models\Entity;
use App\Models\User;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Str;
use Spatie\Permission\Models\Permission;

class DemoLockPersonas extends Command
{
    /**
     * The name and signature of the console command.
     *
     * @var string
     */
    protected $signature = 'demo:lock-personas';

    /**
     * The console command description.
     *
     * @var string
     */
    protected $description = 'Lock down demo personas with minimal permissions and budgets';

    /**
     * Execute the console command.
     */
    public function handle(): int
    {
        $finance = User::where('username', 'demo_finance')->first();
        if (! $finance) {
            $this->error("Missing demo user: demo_finance");

            return Command::FAILURE;
        }

        $production = User::where('username', 'demo_production')->first();
        if (! $production) {
            $this->error("Missing demo user: demo_production");

            return Command::FAILURE;
        }

        $financePermissions = ['view job', 'view invoice', 'view customer'];
        $productionPermissions = ['view job'];

        foreach (array_unique(array_merge($financePermissions, $productionPermissions)) as $name) {
            Permission::firstOrCreate(['name' => $name, 'guard_name' => 'web'], ['group' => 'demo']);
        }

        // Lock demo_finance
        $finance->department_id = null;
        $finance->password = Hash::make(Str::random(40));
        $finance->save();
        $finance->syncRoles([]);
        $finance->syncPermissions($financePermissions);

        if ($finance->entity_id) {
            Entity::where('id', $finance->entity_id)->update(['ask_budget_usd' => 5.00]);
        }

        // Lock demo_production
        $production->department_id = null;
        $production->password = Hash::make(Str::random(40));
        $production->save();
        $production->syncRoles([]);
        $production->syncPermissions($productionPermissions);

        if ($production->entity_id) {
            Entity::where('id', $production->entity_id)->update(['ask_budget_usd' => 5.00]);
        }

        $this->info('Demo personas locked successfully.');

        return Command::SUCCESS;
    }
}
