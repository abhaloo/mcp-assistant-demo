<?php

declare(strict_types=1);

namespace Tests\Support;

use App\Actions\Entries\PostEntry;
use App\Actions\Generators\BillNumberGenerator;
use App\Models\Account;
use App\Models\Bill;
use App\Models\BillItem;
use App\Models\Currency;
use App\Models\Customer;
use App\Models\CustomerOrder;
use App\Models\Entity;
use App\Models\Journal;
use App\Models\Period;
use App\Models\Product;
use App\Models\Tax;
use App\Models\Unit;
use App\Models\WorkOrder;
use Illuminate\Support\Facades\DB;

/**
 * Static bill/order seed helpers for business-query parity tests.
 * Built on PaymentStatusParityTest's makeBill approach.
 */
final class BillParityFixtures
{
    /**
     * @param  list<array{price: float, quantity: float, discount: float, tax: float}>|null  $items
     * @param  list<float>  $payments
     */
    public static function seedBillWithItems(
        ?array $items = null,
        array $payments = [],
        string $type = 'Invoice',
        string $payable = 'No',
        ?string $dueDate = null,
    ): Bill {
        self::ensureEntity();

        $items ??= [
            ['price' => 100.0, 'quantity' => 2.0, 'discount' => 10.0, 'tax' => 16.0],
            ['price' => 50.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0],
        ];

        $customer = Customer::factory()->create(['entity_id' => 1]);
        $bill = Bill::factory()->create([
            'type' => $type,
            'payable' => $payable,
            'entity_id' => 1,
            'customer_id' => $customer->id,
            'due_date' => $dueDate,
        ]);

        foreach ($items as $item) {
            BillItem::create([
                'bill_id' => $bill->id,
                'product_id' => 1,
                'tax_id' => 1,
                'price' => $item['price'],
                'discount' => $item['discount'],
                'tax' => $item['tax'],
                'quantity' => $item['quantity'],
                'description' => 'Parity fixture item',
            ]);
        }

        if ($payments !== []) {
            $cashAccount = Account::factory()->cash()->create(['entity_id' => 1]);
            foreach ($payments as $debit) {
                Journal::factory()->create([
                    'entity_id' => 1,
                    'account_id' => $cashAccount->id,
                    'bill_id' => $bill->id,
                    'debit' => $debit,
                    'credit' => 0,
                ]);
            }
        }

        return $bill->fresh(['billItems']);
    }

    public static function invoice(Customer $customer, float|int $itemsTotal, float|int $cashReceived): Bill
    {
        self::ensureEntity();

        $bill = Bill::factory()->create([
            'type' => 'Invoice',
            'payable' => 'No',
            'entity_id' => 1,
            'customer_id' => $customer->id,
        ]);

        BillItem::create([
            'bill_id' => $bill->id,
            'product_id' => 1,
            'tax_id' => 1,
            'price' => (float) $itemsTotal,
            'discount' => 0.0,
            'tax' => 0.0,
            'quantity' => 1.0,
            'description' => 'Parity fixture invoice item',
        ]);

        if ($cashReceived > 0) {
            $cashAccount = Account::factory()->cash()->create(['entity_id' => 1]);
            Journal::factory()->create([
                'entity_id' => 1,
                'account_id' => $cashAccount->id,
                'bill_id' => $bill->id,
                'debit' => (float) $cashReceived,
                'credit' => 0,
            ]);
        }

        return $bill->fresh(['billItems']);
    }

    /** One PAID, one OVERPAID, one PARTIAL PAID, one UNPAID (never paid). */
    public static function seedFourPaymentStates(): array
    {
        return [
            'paid' => self::seedBillWithItems(
                [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
                [100.0],
            ),
            'overpaid' => self::seedBillWithItems(
                [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
                [150.0],
            ),
            'partial' => self::seedBillWithItems(
                [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
                [40.0],
            ),
            'never_paid' => self::seedBillWithItems(
                [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
                [],
            ),
        ];
    }

    /** A $100 receivable reduced by $30 cash and a $20 sales discount. */
    public static function seedBillWithCashAndDiscountSettlement(): Bill
    {
        $bill = self::seedBillWithItems(
            [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
        );
        $cash = Account::factory()->cash()->create(['entity_id' => 1]);
        $discount = Account::factory()->expense()->create([
            'entity_id' => 1,
            'account_type' => Account::OPERATING_EXPENSE,
            'name' => 'Sales Discount',
        ]);

        Journal::factory()->create([
            'entity_id' => 1, 'account_id' => $cash->id, 'bill_id' => $bill->id, 'debit' => 30.0, 'credit' => 0,
        ]);
        Journal::factory()->create([
            'entity_id' => 1, 'account_id' => $discount->id, 'bill_id' => $bill->id, 'debit' => 20.0, 'credit' => 0,
        ]);

        return $bill->fresh(['billItems']);
    }

    /** @return array{usd: Bill, eur: Bill, direct_invoice_job: WorkOrder, direct_quotation_job: WorkOrder, delivered_job: WorkOrder} */
    public static function seedEffectiveBillAndTwoCurrencyJobs(): array
    {
        self::ensureEntity();
        $customer = Customer::factory()->create(['entity_id' => 1]);
        $usd = Currency::factory()->create(['entity_id' => 1, 'currency_code' => 'USD']);
        $eur = Currency::factory()->create(['entity_id' => 1, 'currency_code' => 'EUR']);
        $parent = CustomerOrder::factory()->create(['customer_id' => $customer->id, 'entity_id' => 1]);
        $parentBill = Bill::factory()->create([
            'entity_id' => 1, 'customer_id' => $customer->id, 'customer_order_id' => $parent->id, 'currency_id' => $usd->id,
        ]);

        WorkOrder::factory()->create([
            'entity_id' => 1, 'customer_order_id' => $parent->id, 'status' => 'FINISHED', 'bill_id' => null,
        ]);
        $quotationOnlyParent = CustomerOrder::factory()->create(['customer_id' => $customer->id, 'entity_id' => 1]);
        Bill::factory()->quotation()->create([
            'entity_id' => 1, 'customer_id' => $customer->id, 'customer_order_id' => $quotationOnlyParent->id, 'currency_id' => $usd->id,
        ]);
        WorkOrder::factory()->create([
            'entity_id' => 1, 'customer_order_id' => $quotationOnlyParent->id, 'status' => 'FINISHED', 'bill_id' => null,
        ]);
        $directInvoiceJob = WorkOrder::factory()->create([
            'entity_id' => 1, 'status' => 'FINISHED', 'bill_id' => $parentBill->id,
        ]);
        $directQuotation = Bill::factory()->quotation()->create([
            'entity_id' => 1, 'customer_id' => $customer->id, 'currency_id' => $usd->id,
        ]);
        $directQuotationJob = WorkOrder::factory()->create([
            'entity_id' => 1, 'status' => 'FINISHED', 'bill_id' => $directQuotation->id,
        ]);
        WorkOrder::factory()->create([
            'entity_id' => 1, 'status' => 'FINISHED', 'bill_id' => null,
        ]);
        // DELIVERED, no direct bill and no parent order: effective_bill_id is NULL,
        // same as a FINISHED job with no bill. jobs_finished_unbilled must count both.
        $deliveredJob = WorkOrder::factory()->create([
            'entity_id' => 1, 'status' => 'DELIVERED', 'bill_id' => null,
        ]);
        $eurBill = Bill::factory()->create([
            'entity_id' => 1, 'customer_id' => $customer->id, 'currency_id' => $eur->id,
        ]);

        return [
            'usd' => $parentBill,
            'eur' => $eurBill,
            'direct_invoice_job' => $directInvoiceJob,
            'direct_quotation_job' => $directQuotationJob,
            'delivered_job' => $deliveredJob,
        ];
    }

    /**
     * Quotation with two items (nonzero discount AND tax) and a revenue account
     * of OPERATING_REVENUE so convert() posts into ledger_revenue's filter.
     */
    public static function seedQuotationWithItems(): Bill
    {
        self::ensureEntity();
        self::ensureAccountingScaffold();

        $revenue = Account::factory()->revenue()->create([
            'entity_id' => 1,
            'account_type' => Account::OPERATING_REVENUE,
        ]);
        // Tax GL must NOT be a revenue account type — otherwise tax credits
        // inflate ledger_revenue (BillController posts tax to tax->account_id).
        $taxAccount = Account::factory()->create([
            'entity_id' => 1,
            'account_type' => Account::CURRENT_LIABILITY,
            'name' => 'Output Tax',
        ]);
        $unit = Unit::factory()->create(['entity_id' => 1]);
        $product = Product::factory()->create([
            'entity_id' => 1,
            'unit_id' => $unit->id,
            'sell_account' => $revenue->id,
            'revenue_account' => $revenue->id,
        ]);
        $tax = Tax::factory()->create([
            'rate' => 16,
            'account_id' => $taxAccount->id,
        ]);

        $customer = Customer::factory()->create(['entity_id' => 1]);
        $bill = Bill::factory()->quotation()->create([
            'entity_id' => 1,
            'customer_id' => $customer->id,
        ]);

        BillItem::create([
            'bill_id' => $bill->id,
            'product_id' => $product->id,
            'tax_id' => $tax->id,
            'price' => 100.0,
            'quantity' => 2.0,
            'discount' => 10.0,
            'tax' => 16.0,
            'description' => 'Quotation line A',
        ]);
        BillItem::create([
            'bill_id' => $bill->id,
            'product_id' => $product->id,
            'tax_id' => $tax->id,
            'price' => 50.0,
            'quantity' => 1.0,
            'discount' => 5.0,
            'tax' => 10.0,
            'description' => 'Quotation line B',
        ]);

        return $bill->fresh(['billItems.product']);
    }

    /**
     * Mirror BillController::convert() posting semantics with dynamic account ids
     * (receivable + Sales Discount OPERATING_EXPENSE), not hardcoded 2/4.
     */
    public static function convertToInvoice(Bill $bill): Bill
    {
        self::ensureAccountingScaffold();

        $bill->load('billItems.product');
        $bill->type = 'Invoice';
        $bill->status = 'APPROVED';
        $bill->invoice_number = BillNumberGenerator::handle('Invoice');
        $bill->save();

        $receivable = Account::factory()->receivable()->create(['entity_id' => $bill->entity_id]);
        $salesDiscount = Account::factory()->expense()->create([
            'entity_id' => $bill->entity_id,
            'name' => 'Sales Discount',
            'account_type' => Account::OPERATING_EXPENSE,
        ]);

        foreach ($bill->billItems as $item) {
            $product = $item->product;
            if ($product === null) {
                continue;
            }

            // see BillController.php:151-158 — gross price * qty to revenue
            PostEntry::handle(
                $item->price * $item->quantity,
                $receivable->id,
                $product->sell_account ?? $product->revenue_account,
                'Sales for '.$product->name.' Invoice#'.$bill->id,
                bill_id: $bill->id,
                item_id: $item->id,
            );

            if ($item->tax > 0) {
                $tax = Tax::find($item->tax_id);
                if ($tax !== null) {
                    PostEntry::handle(
                        (($item->price * $item->quantity) - $item->discount) * ($item->tax / 100),
                        $receivable->id,
                        $tax->account_id,
                        'Tax for '.$product->name.' Invoice#'.$bill->id,
                        bill_id: $bill->id,
                    );
                }
            }

            if ($item->discount > 0) {
                // see BillController.php:176-183 — debit Sales Discount (OPERATING_EXPENSE)
                PostEntry::handle(
                    $item->discount,
                    $salesDiscount->id,
                    $receivable->id,
                    'Sales Discount for '.$product->name.' Invoice#'.$bill->id,
                    bill_id: $bill->id,
                );
            }
        }

        return $bill->fresh(['billItems']);
    }

    /**
     * Ten-bill aging fixture: due 1/30/31/60/61/90/91 days ago (outstanding),
     * one not yet due, one fully paid, one NULL due_date.
     *
     * @return list<Bill>
     */
    public static function seedAgingBoundaryBills(): array
    {
        $calendar = self::sessionCalendarDates();
        $bills = [];

        foreach ([1, 30, 31, 60, 61, 90, 91] as $daysAgo) {
            $bills[] = self::seedBillWithItems(
                [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
                [],
                dueDate: $calendar['minus']($daysAgo),
            );
        }

        $bills[] = self::seedBillWithItems(
            [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
            [],
            dueDate: $calendar['plus'](5),
        );

        $bills[] = self::seedBillWithItems(
            [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
            [100.0],
            dueDate: $calendar['minus'](45),
        );

        $bills[] = self::seedBillWithItems(
            [['price' => 100.0, 'quantity' => 1.0, 'discount' => 0.0, 'tax' => 0.0]],
            [],
            dueDate: null,
        );

        return $bills;
    }

    /** Invoiced meaning: Σ[(p·q−d) + (p·q−d)·tax/100]. */
    public static function expectedItemRevenue(Bill $bill): float
    {
        $total = 0.0;
        foreach ($bill->billItems as $item) {
            $itemValue = ($item->price * $item->quantity) - $item->discount;
            $total += $itemValue + ($itemValue * ($item->tax / 100));
        }

        return $total;
    }

    /** Posted/ledger meaning: Σ(price × qty) — gross of discount, excluding tax. */
    public static function expectedLedgerRevenue(Bill $bill): float
    {
        $total = 0.0;
        foreach ($bill->billItems as $item) {
            $total += $item->price * $item->quantity;
        }

        return $total;
    }

    private static function ensureEntity(): void
    {
        if (! Entity::query()->whereKey(1)->exists()) {
            Entity::factory()->create(['id' => 1]);
        }
    }

    private static function ensureAccountingScaffold(): void
    {
        self::ensureEntity();

        if (! Currency::query()->whereKey(1)->exists()) {
            Currency::factory()->create(['id' => 1, 'entity_id' => 1, 'rate' => 1.0]);
        }

        if (! Period::exists()) {
            Period::create([
                'entity_id' => 1,
                'period_count' => 1,
                'status' => Period::OPEN,
                'calendar_year' => (string) date('Y'),
            ]);
        }
    }

    /**
     * @return array{minus: callable(int): string, plus: callable(int): string}
     */
    private static function sessionCalendarDates(): array
    {
        if (DB::connection()->getDriverName() === 'sqlite') {
            return [
                'minus' => static function (int $days): string {
                    return (string) DB::selectOne(
                        "SELECT date('now', 'localtime', ? ) AS d",
                        ["-{$days} days"]
                    )->d;
                },
                'plus' => static function (int $days): string {
                    return (string) DB::selectOne(
                        "SELECT date('now', 'localtime', ? ) AS d",
                        ["+{$days} days"]
                    )->d;
                },
            ];
        }

        return [
            'minus' => static function (int $days): string {
                return (string) DB::selectOne(
                    'SELECT DATE_SUB(CURRENT_DATE, INTERVAL ? DAY) AS d',
                    [$days]
                )->d;
            },
            'plus' => static function (int $days): string {
                return (string) DB::selectOne(
                    'SELECT DATE_ADD(CURRENT_DATE, INTERVAL ? DAY) AS d',
                    [$days]
                )->d;
            },
        ];
    }
}
