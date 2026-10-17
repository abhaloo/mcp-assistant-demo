<?php

namespace Database\Factories;

use App\Models\Account;
use App\Models\Currency;
use Illuminate\Database\Eloquent\Factories\Factory;

/**
 * @extends Factory<Account>
 */
class AccountFactory extends Factory
{
    protected $model = Account::class;

    public function definition(): array
    {
        return [
            'entity_id' => fn () => FactoryEntity::id(),
            'currency_id' => Currency::first()?->id ?? 1,
            'name' => fake()->unique()->words(3, true).' Account',
            'code' => fake()->unique()->numerify('####'),
            'account_type' => fake()->randomElement([
                Account::BANK,
                Account::CASH,
                Account::RECEIVABLE,
                Account::PAYABLE,
                Account::OPERATING_REVENUE,
                Account::OPERATING_EXPENSE,
            ]),
            'description' => fake()->sentence(),
            'isSystemAccount' => false,
        ];
    }

    public function bank(): static
    {
        return $this->state(fn (array $attributes) => [
            'account_type' => Account::BANK,
            'name' => fake()->company().' Bank Account',
        ]);
    }

    public function cash(): static
    {
        return $this->state(fn (array $attributes) => [
            'account_type' => Account::CASH,
            'name' => 'Cash '.fake()->word(),
        ]);
    }

    public function receivable(): static
    {
        return $this->state(fn (array $attributes) => [
            'account_type' => Account::RECEIVABLE,
            'name' => 'Accounts Receivable - '.fake()->word(),
        ]);
    }

    public function payable(): static
    {
        return $this->state(fn (array $attributes) => [
            'account_type' => Account::PAYABLE,
            'name' => 'Accounts Payable - '.fake()->word(),
        ]);
    }

    public function revenue(): static
    {
        return $this->state(fn (array $attributes) => [
            'account_type' => Account::OPERATING_REVENUE,
            'name' => 'Sales Revenue - '.fake()->word(),
        ]);
    }

    public function expense(): static
    {
        return $this->state(fn (array $attributes) => [
            'account_type' => Account::OPERATING_EXPENSE,
            'name' => fake()->word().' Expense',
        ]);
    }

    public function inventory(): static
    {
        return $this->state(fn (array $attributes) => [
            'account_type' => Account::INVENTORY,
            'name' => 'Inventory - '.fake()->word(),
        ]);
    }

    public function systemAccount(): static
    {
        return $this->state(fn (array $attributes) => [
            'isSystemAccount' => true,
        ]);
    }
}
