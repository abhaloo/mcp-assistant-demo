<?php

namespace Database\Seeders;

// use Illuminate\Database\Console\Seeds\WithoutModelEvents;
use App\Models\User;
use Illuminate\Database\Seeder;
use Spatie\Permission\Models\Permission;

class DatabaseSeeder extends Seeder
{
    /**
     * Seed the application's database.
     *
     * @return void
     */
    public function run()
    {
        $this->call(PermissionTableSeeder::class);
        $this->call(UserSeeder::class);
        $admin = User::find(1);
        $admin->givePermissionTo(Permission::all());
        $this->call(ProductsSeeder::class);
        $this->call(TaxSeeder::class);
    }
}
