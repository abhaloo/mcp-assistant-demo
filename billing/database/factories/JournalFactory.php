<?php

namespace Database\Factories;

use App\Models\Account;
use App\Models\Currency;
use App\Models\Journal;
use App\Models\Period;
use Illuminate\Database\Eloquent\Factories\Factory;

/**
 * @extends Factory<Journal>
 */
class JournalFactory extends Factory
{
    protected $model = Journal::class;

    public function definition(): array
    {
        $isDebit = fake()->boolean();
        $amount = fake()->randomFloat(2, 10, 10000);

        return [
            'entity_id' => fn () => FactoryEntity::id(),
            'period_id' => Period::first()?->id ?? 1,
            'currency_id' => Currency::first()?->id ?? 1,
            'account_id' => Account::factory(),
            'document_no' => 'DOC-'.fake()->unique()->numerify('######'),
            'post_date' => fake()->dateTimeBetween('-2 years', 'now')->format('Y-m-d'),
            'debit' => $isDebit ? $amount : 0,
            'credit' => $isDebit ? 0 : $amount,
            'foreign_debit' => $isDebit ? $amount : 0,
            'foreign_credit' => $isDebit ? 0 : $amount,
            'exchange_rate' => 1.0000,
            'description' => fake()->sentence(),
            'bill_id' => null,
            'customer_id' => null,
            'supplier_id' => null,
            'inventory_id' => null,
            'reference' => null,
            'receipt_number' => null,
            'voucher_number' => null,
            'cash' => false,
        ];
    }

    public function debit(?float $amount = null): static
    {
        $amount = $amount ?? fake()->randomFloat(2, 100, 5000);

        return $this->state(fn (array $attributes) => [
            'debit' => $amount,
            'credit' => 0,
            'foreign_debit' => $amount,
            'foreign_credit' => 0,
        ]);
    }

    public function credit(?float $amount = null): static
    {
        $amount = $amount ?? fake()->randomFloat(2, 100, 5000);

        return $this->state(fn (array $attributes) => [
            'debit' => 0,
            'credit' => $amount,
            'foreign_debit' => 0,
            'foreign_credit' => $amount,
        ]);
    }

    public function forAccount(Account $account): static
    {
        return $this->state(fn (array $attributes) => [
            'account_id' => $account->id,
        ]);
    }

    public function onDate(string $date): static
    {
        return $this->state(fn (array $attributes) => [
            'post_date' => $date,
        ]);
    }

    public function withDocumentNo(string $documentNo): static
    {
        return $this->state(fn (array $attributes) => [
            'document_no' => $documentNo,
        ]);
    }

    public function withReceipt(string $receiptNumber): static
    {
        return $this->state(fn (array $attributes) => [
            'receipt_number' => $receiptNumber,
        ]);
    }

    public function withVoucher(string $voucherNumber): static
    {
        return $this->state(fn (array $attributes) => [
            'voucher_number' => $voucherNumber,
        ]);
    }

    /**
     * Create a balanced double-entry pair
     */
    public static function doubleEntry(
        Account $debitAccount,
        Account $creditAccount,
        float $amount,
        string $date,
        ?string $description = null
    ): array {
        $documentNo = 'DOC-'.fake()->unique()->numerify('######');
        $description = $description ?? fake()->sentence();

        $debitEntry = Journal::factory()
            ->forAccount($debitAccount)
            ->debit($amount)
            ->onDate($date)
            ->withDocumentNo($documentNo)
            ->create(['description' => $description]);

        $creditEntry = Journal::factory()
            ->forAccount($creditAccount)
            ->credit($amount)
            ->onDate($date)
            ->withDocumentNo($documentNo)
            ->create(['description' => $description]);

        return [$debitEntry, $creditEntry];
    }
}
