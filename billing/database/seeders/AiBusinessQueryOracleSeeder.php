<?php

declare(strict_types=1);

namespace Database\Seeders;

use App\Models\Account;
use App\Models\Bill;
use App\Models\BillItem;
use App\Models\Currency;
use App\Models\Customer;
use App\Models\CustomerOrder;
use App\Models\Entity;
use App\Models\Journal;
use App\Models\WorkOrder;
use DateTimeImmutable;
use Illuminate\Database\Seeder;

/**
 * Task I1 (billing half) — literal-oracle seeds for the paired real-MySQL
 * BusinessQueryModule integration test (RAG-side: task I1b,
 * tests/integration/test_billing_projection_contract.py in mcp-rag-qa).
 *
 * Mirrors the AiProjectionContractSeeder pattern: explicit stable IDs,
 * explicit created_at/updated_at, withoutEvents() on every
 * Bill/Customer/CustomerOrder/Account/Currency/Journal create() (
 * App\Traits\EntityTrait::booted() assigns entity_id when missing and other
 * booted() listeners touch auth() state with no authenticated user in a
 * seeder/CI run). NOT wired into DatabaseSeeder::run() — standalone
 * invocation only, mirrored into the RAG test by hand, never imported.
 *
 * Every date is derived from BUSINESS_DATE, the same independent request
 * anchor used by the paired RAG oracle. The fixture therefore keeps identical
 * month and aging results regardless of the day or timezone in which it runs.
 */
final class AiBusinessQueryOracleSeeder extends Seeder
{
    public const BUSINESS_DATE = '2026-08-10';

    // -- Entities -------------------------------------------------------
    public const ENTITY_PRIMARY = 9101;

    public const ENTITY_SECONDARY = 9102;

    // -- Currencies -------------------------------------------------------
    public const CURRENCY_PRIMARY = 9201;

    public const CURRENCY_SECONDARY = 9202;

    public const CURRENCY_PRIMARY_ALT = 9203;

    // -- Customers --------------------------------------------------------
    public const CUSTOMER_PRIMARY = 9301;

    /** cust-no-orders shape: has_orders eq false. */
    public const CUSTOMER_NO_ORDERS = 9302;

    public const CUSTOMER_SECONDARY = 9303;

    // -- Accounts, entity PRIMARY -----------------------------------------
    public const ACCOUNT_RECEIVABLE = 9401;

    public const ACCOUNT_REVENUE = 9402;

    /** Mirrors UserSeeder.php:78-83's "Sales Discount" OPERATING_EXPENSE account. */
    public const ACCOUNT_SALES_DISCOUNT = 9403;

    public const ACCOUNT_CASH = 9404;

    public const ACCOUNT_TAX_LIABILITY = 9405;

    // -- Accounts, entity SECONDARY (cross-entity isolation mirror) -------
    public const ACCOUNT_RECEIVABLE_SECONDARY = 9411;

    public const ACCOUNT_REVENUE_SECONDARY = 9412;

    public const ACCOUNT_CASH_SECONDARY = 9413;

    public const ACCOUNT_SALES_DISCOUNT_SECONDARY = 9414;

    public const ACCOUNT_TAX_LIABILITY_SECONDARY = 9415;

    // -- Customer orders, entity PRIMARY -----------------------------------
    public const ORDER_OPEN_1 = 9701;

    public const ORDER_FINISHED = 9702;

    public const ORDER_CANCELLED = 9703;

    public const ORDER_OPEN_2 = 9704;

    public const ORDER_PARENT_INVOICE = 9705;

    public const ORDER_PARENT_QUOTATION = 9706;

    // -- Customer orders, entity SECONDARY ---------------------------------
    public const ORDER_SECONDARY_OPEN = 9711;

    // -- Bills, entity PRIMARY: payment-state partition + term-by-term money grouping ---
    public const BILL_PAID_TERMTAX = 9801;

    public const BILL_UNPAID = 9802;

    public const BILL_OVERPAID = 9803;

    public const BILL_PARTIAL = 9804;

    /**
     * Dedicated ledger_revenue vs invoiced_revenue divergence bill — kept
     * separate from BILL_PAID_TERMTAX. Account::OPERATING_EXPENSE (the
     * "Sales Discount" account) is itself one of Bill::payments()' qualifying
     * payment account types, so a convert()-style discount posting on the
     * SAME bill as a cash receipt double-counts toward payments_total.
     * Verified empirically: combining both on one bill drove
     * payments_total to items_total+discount, flipping PAID to OVERPAID.
     * Splitting the two proofs onto separate bills avoids that interaction.
     */
    public const BILL_LEDGER_PRIMARY = 9805;

    // -- Bills, entity PRIMARY: ar_aging / overdue-listing fixture ---------
    public const BILL_AGING_1 = 9811;

    public const BILL_AGING_30 = 9812;

    public const BILL_AGING_31 = 9813;

    public const BILL_AGING_60 = 9814;

    public const BILL_AGING_61 = 9815;

    public const BILL_AGING_90 = 9816;

    public const BILL_AGING_91 = 9817;

    public const BILL_AGING_NOT_DUE = 9818;

    /** Fully paid + overdue — must be EXCLUDED from ar_aging/overdue via the outstanding>0 gate. */
    public const BILL_AGING_PAID_OVERDUE = 9819;

    /** due_date IS NULL — must land in NO ar_aging bucket. */
    public const BILL_AGING_NULL_DUE = 9820;

    /** 0 < outstanding <= 0.01 — must appear under exact `outstanding > 0` (no 0.01 epsilon). */
    public const BILL_AGING_EPSILON = 9821;

    // -- Bill, entity PRIMARY: product_revenue per-kind boundary -----------
    public const BILL_PAYABLE_QUOTATION = 9831;

    public const BILL_CASH_AND_DISCOUNT = 9841;

    public const BILL_PRIMARY_ALT_CURRENCY = 9842;

    public const BILL_DIRECT_INVOICE = 9843;

    public const BILL_DIRECT_QUOTATION = 9844;

    public const BILL_PARENT_INVOICE = 9845;

    public const BILL_PARENT_QUOTATION = 9846;

    public const JOB_DIRECT_INVOICE = 9851;

    public const JOB_DIRECT_QUOTATION = 9852;

    public const JOB_PARENT_INVOICE = 9853;

    public const JOB_PARENT_QUOTATION = 9854;

    /** DELIVERED, no direct bill and no parent order: effective_bill_id is NULL. */
    public const JOB_DIRECT_DELIVERED = 9855;

    // -- Bills, entity SECONDARY: cross-entity isolation mirror ------------
    public const BILL_SECONDARY_AGING = 9901;

    public const BILL_SECONDARY_LEDGER = 9902;

    public function run(): void
    {
        $this->seedEntitiesAndCurrencies();
        $this->seedCustomers();
        $this->seedAccounts();
        $this->seedCustomerOrders();
        $this->seedPaymentStatePartition();
        $this->seedRemediationCounterfactuals();
        $this->seedAgingAndOverdueFixture();
        $this->seedProductRevenueBoundary();
        $this->seedCrossEntityMirror();
    }

    private function seedEntitiesAndCurrencies(): void
    {
        Entity::factory()->create(['id' => self::ENTITY_PRIMARY, 'name' => 'Business Query Oracle Entity — Primary']);
        Entity::factory()->create(['id' => self::ENTITY_SECONDARY, 'name' => 'Business Query Oracle Entity — Secondary']);

        Currency::withoutEvents(fn () => Currency::factory()->create([
            'id' => self::CURRENCY_PRIMARY,
            'entity_id' => self::ENTITY_PRIMARY,
            'rate' => 1.0,
            'currency_code' => 'USD',
        ]));
        Currency::withoutEvents(fn () => Currency::factory()->create([
            'id' => self::CURRENCY_SECONDARY,
            'entity_id' => self::ENTITY_SECONDARY,
            'rate' => 1.0,
            'currency_code' => 'KES',
        ]));
        Currency::withoutEvents(fn () => Currency::factory()->create([
            'id' => self::CURRENCY_PRIMARY_ALT,
            'entity_id' => self::ENTITY_PRIMARY,
            'rate' => 1.0,
            'currency_code' => 'USD',
        ]));
    }

    private function seedCustomers(): void
    {
        $this->customer(self::CUSTOMER_PRIMARY, self::ENTITY_PRIMARY, self::CURRENCY_PRIMARY, 'Oracle Primary Co');
        $this->customer(self::CUSTOMER_NO_ORDERS, self::ENTITY_PRIMARY, self::CURRENCY_PRIMARY, 'Oracle No-Orders Co');
        $this->customer(self::CUSTOMER_SECONDARY, self::ENTITY_SECONDARY, self::CURRENCY_SECONDARY, 'Oracle Secondary Co');
    }

    private function seedAccounts(): void
    {
        $this->account(self::ACCOUNT_RECEIVABLE, self::ENTITY_PRIMARY, self::CURRENCY_PRIMARY, Account::RECEIVABLE, 'Oracle Receivable');
        $this->account(self::ACCOUNT_REVENUE, self::ENTITY_PRIMARY, self::CURRENCY_PRIMARY, Account::OPERATING_REVENUE, 'Oracle Sales Revenue');
        $this->account(self::ACCOUNT_SALES_DISCOUNT, self::ENTITY_PRIMARY, self::CURRENCY_PRIMARY, Account::OPERATING_EXPENSE, 'Sales Discount');
        $this->account(self::ACCOUNT_CASH, self::ENTITY_PRIMARY, self::CURRENCY_PRIMARY, Account::CASH, 'Oracle Cash');
        $this->account(self::ACCOUNT_TAX_LIABILITY, self::ENTITY_PRIMARY, self::CURRENCY_PRIMARY, Account::CURRENT_LIABILITY, 'Output Tax');

        $this->account(self::ACCOUNT_RECEIVABLE_SECONDARY, self::ENTITY_SECONDARY, self::CURRENCY_SECONDARY, Account::RECEIVABLE, 'Oracle Receivable (Secondary)');
        $this->account(self::ACCOUNT_REVENUE_SECONDARY, self::ENTITY_SECONDARY, self::CURRENCY_SECONDARY, Account::OPERATING_REVENUE, 'Oracle Sales Revenue (Secondary)');
        $this->account(self::ACCOUNT_CASH_SECONDARY, self::ENTITY_SECONDARY, self::CURRENCY_SECONDARY, Account::CASH, 'Oracle Cash (Secondary)');
        $this->account(self::ACCOUNT_SALES_DISCOUNT_SECONDARY, self::ENTITY_SECONDARY, self::CURRENCY_SECONDARY, Account::OPERATING_EXPENSE, 'Sales Discount (Secondary)');
        $this->account(self::ACCOUNT_TAX_LIABILITY_SECONDARY, self::ENTITY_SECONDARY, self::CURRENCY_SECONDARY, Account::CURRENT_LIABILITY, 'Output Tax (Secondary)');
    }

    /**
     * open_customer_orders filters the STORED status column directly
     * (`status NOT IN ('FINISHED','CANCELLED')`) — CustomerOrder::getComputedStatusAttribute()
     * only ever returns FINISHED/CANCELLED when the stored value IS that,
     * otherwise NEW/IN_PROGRESS depending on job count, so filtering on
     * stored status alone is accessor-faithful and no WorkOrder rows are
     * needed to prove this measure.
     *
     * open_customer_orders(PRIMARY) = 4 (ORDER_OPEN_1, ORDER_OPEN_2,
     * ORDER_PARENT_INVOICE, ORDER_PARENT_QUOTATION). orders_count(PRIMARY) = 6.
     * open_customer_orders(SECONDARY) = 1 — isolation counterfactual: if the
     * entity predicate leaked, PRIMARY's count would read 5 instead of 4.
     */
    private function seedCustomerOrders(): void
    {
        $this->order(self::ORDER_OPEN_1, self::ENTITY_PRIMARY, self::CUSTOMER_PRIMARY, 'ORD-ORACLE-9701', 'CREATED');
        $this->order(self::ORDER_FINISHED, self::ENTITY_PRIMARY, self::CUSTOMER_PRIMARY, 'ORD-ORACLE-9702', 'FINISHED');
        $this->order(self::ORDER_CANCELLED, self::ENTITY_PRIMARY, self::CUSTOMER_PRIMARY, 'ORD-ORACLE-9703', 'CANCELLED');
        $this->order(self::ORDER_OPEN_2, self::ENTITY_PRIMARY, self::CUSTOMER_PRIMARY, 'ORD-ORACLE-9704', 'CREATED');
        $this->order(self::ORDER_PARENT_INVOICE, self::ENTITY_PRIMARY, self::CUSTOMER_PRIMARY, 'ORD-ORACLE-9705', 'CREATED');
        $this->order(self::ORDER_PARENT_QUOTATION, self::ENTITY_PRIMARY, self::CUSTOMER_PRIMARY, 'ORD-ORACLE-9706', 'CREATED');

        $this->order(self::ORDER_SECONDARY_OPEN, self::ENTITY_SECONDARY, self::CUSTOMER_SECONDARY, 'ORD-ORACLE-9711', 'CREATED');
    }

    /**
     * One invoice per BillPaymentState::classify() branch (BillPaymentState.php:75-88),
     * proving the partition is exclusive.
     *
     * BILL_PAID_TERMTAX carries the term-by-term money-grouping proof:
     *   item: price=100, qty=2, discount=40, tax=10
     *   itemValue = 100*2 - 40 = 160.00        (never *(1+tax/100) — matches
     *   items_total = 160 + 160*(10/100) = 176.00   BillPaymentState::itemsTotal()
     *                                                and SemanticViews::itemsTotalSubquery()
     *                                                term-by-term, bit-for-bit)
     *   payment (CASH) = 176.00 -> outstanding = 0.00 -> PAID (balance == 0 boundary)
     *
     * BILL_LEDGER_PRIMARY carries the ledger_revenue vs invoiced_revenue divergence,
     * on a SEPARATE bill from BILL_PAID_TERMTAX (see its own constant docblock for
     * why): same item shape (price=100, qty=2, discount=40, tax=10) -> items_total=176.00,
     * but NO cash payment — only convert()-style postings (BillController.php:151-158):
     *     revenue leg:  price*qty = 200.00        credit ACCOUNT_REVENUE (OPERATING_REVENUE)
     *     tax leg:      (200-40)*(10/100) = 16.00  credit ACCOUNT_TAX_LIABILITY (excluded — not revenue)
     *     discount leg: discount = 40.00           debit  ACCOUNT_SALES_DISCOUNT (OPERATING_EXPENSE)
     *   => ledger_revenue contribution   = 200.00 (gross price*qty; discount/tax excluded)
     *      invoiced_revenue contribution = items_total = 176.00
     *   Genuinely different literals from the SAME bill, same posting event.
     *   NOTE: the discount leg's debit lands on an OPERATING_EXPENSE account, which
     *   IS one of Bill::payments()' qualifying payment types — so this bill's own
     *   payments_total is 40.00 (from the discount leg) and outstanding=136.00,
     *   landing it in invoices_partially_paid (0 < 136 < 176). That is real,
     *   verified app behavior (a discount posting genuinely reduces a bill's
     *   receivable balance the same way a cash receipt does) — counted in the
     *   partition totals below, not a fixture bug.
     *
     * BILL_UNPAID:   price=50,  qty=3, disc=0,  tax=0 -> items_total=150.00, no payment
     *                -> outstanding=150.00 (== items_total) -> UNPAID / invoices_never_paid.
     * BILL_OVERPAID: price=100, qty=1, disc=0,  tax=0 -> items_total=100.00, payment=150.00
     *                -> outstanding=-50.00 -> OVERPAID.
     * BILL_PARTIAL:  price=200, qty=1, disc=0,  tax=0 -> items_total=200.00, payment=80.00
     *                -> outstanding=120.00 (0 < 120 < 200) -> PARTIAL PAID.
     *
     * due_date is left NULL on all five — this cluster proves payment-state
     * partitioning, not aging; a null due_date keeps them out of every
     * ar_aging bucket and the overdue listing (see BILL_AGING_NULL_DUE for
     * the dedicated null-due-date counterfactual).
     */
    private function seedPaymentStatePartition(): void
    {
        $recognizedAt = $this->recentTimestamp(5);

        $bill = $this->bill(self::BILL_PAID_TERMTAX, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-9801',
            'description' => 'Oracle payment-state PAID + term-by-term fixture',
        ], $recognizedAt, null);
        $this->billItem($bill->id, price: 100.0, quantity: 2, discount: 40.0, tax: 10.0);
        $this->cashPayment(self::ACCOUNT_CASH, self::ENTITY_PRIMARY, $bill->id, 176.0, $recognizedAt);

        $bill = $this->bill(self::BILL_LEDGER_PRIMARY, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-9805',
            'description' => 'Oracle ledger_revenue vs invoiced_revenue divergence fixture',
        ], $recognizedAt, null);
        $this->billItem($bill->id, price: 100.0, quantity: 2, discount: 40.0, tax: 10.0);
        $this->postConvertJournals(
            entityId: self::ENTITY_PRIMARY,
            billId: $bill->id,
            receivableId: self::ACCOUNT_RECEIVABLE,
            revenueId: self::ACCOUNT_REVENUE,
            taxLiabilityId: self::ACCOUNT_TAX_LIABILITY,
            salesDiscountId: self::ACCOUNT_SALES_DISCOUNT,
            grossPriceQty: 200.0,
            taxAmount: 16.0,
            discountAmount: 40.0,
            postDate: $recognizedAt,
            documentPrefix: 'ORACLE-9805',
        );

        $bill = $this->bill(self::BILL_UNPAID, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-9802',
            'description' => 'Oracle payment-state UNPAID / never_paid fixture',
        ], $this->recentTimestamp(10), null);
        $this->billItem($bill->id, price: 50.0, quantity: 3, discount: 0.0, tax: 0.0);

        $bill = $this->bill(self::BILL_OVERPAID, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-9803',
            'description' => 'Oracle payment-state OVERPAID fixture',
        ], $this->recentTimestamp(10), null);
        $this->billItem($bill->id, price: 100.0, quantity: 1, discount: 0.0, tax: 0.0);
        $this->cashPayment(self::ACCOUNT_CASH, self::ENTITY_PRIMARY, $bill->id, 150.0, $this->recentTimestamp(10));

        $bill = $this->bill(self::BILL_PARTIAL, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-9804',
            'description' => 'Oracle payment-state PARTIAL PAID fixture',
        ], $this->recentTimestamp(10), null);
        $this->billItem($bill->id, price: 200.0, quantity: 1, discount: 0.0, tax: 0.0);
        $this->cashPayment(self::ACCOUNT_CASH, self::ENTITY_PRIMARY, $bill->id, 80.0, $this->recentTimestamp(10));
    }

    /** Literal remediation counterfactuals; expected values do not use projections or definitions. */
    private function seedRemediationCounterfactuals(): void
    {
        $issued = $this->recentTimestamp(8);
        $bill = $this->bill(self::BILL_CASH_AND_DISCOUNT, self::ENTITY_PRIMARY, [
            'type' => 'Invoice', 'invoice_number' => 'INV-ORACLE-9841',
        ], $issued, null);
        $this->billItem($bill->id, price: 100.0, quantity: 1, discount: 0.0, tax: 0.0);
        $this->cashPayment(self::ACCOUNT_CASH, self::ENTITY_PRIMARY, $bill->id, 30.0, $issued);
        $this->cashPayment(self::ACCOUNT_SALES_DISCOUNT, self::ENTITY_PRIMARY, $bill->id, 20.0, $issued);

        $alt = $this->bill(self::BILL_PRIMARY_ALT_CURRENCY, self::ENTITY_PRIMARY, [
            'type' => 'Invoice', 'invoice_number' => 'INV-ORACLE-9842', 'currency_id' => self::CURRENCY_PRIMARY_ALT,
        ], $issued, null);
        $this->billItem($alt->id, price: 200.0, quantity: 1, discount: 0.0, tax: 0.0);
        // Journal posted through cashPayment() always carries the entity's home
        // currency (CURRENCY_PRIMARY/USD) regardless of the bill's own currency —
        // the ledger_entry.currency_code label must resolve to this bill's own
        // currency (USD) instead, proving the currency-label fix over a real
        // non-home-currency bill.
        $this->cashPayment(self::ACCOUNT_CASH, self::ENTITY_PRIMARY, $alt->id, 50.0, $issued);

        $directInvoice = $this->bill(self::BILL_DIRECT_INVOICE, self::ENTITY_PRIMARY, ['type' => 'Invoice'], $issued, null);
        $directQuotation = $this->bill(self::BILL_DIRECT_QUOTATION, self::ENTITY_PRIMARY, ['type' => 'Quotation'], $issued, null);
        $parentInvoice = $this->bill(self::BILL_PARENT_INVOICE, self::ENTITY_PRIMARY, [
            'type' => 'Invoice', 'customer_order_id' => self::ORDER_PARENT_INVOICE,
        ], $issued, null);
        $this->bill(self::BILL_PARENT_QUOTATION, self::ENTITY_PRIMARY, [
            'type' => 'Quotation', 'customer_order_id' => self::ORDER_PARENT_QUOTATION,
        ], $issued, null);

        foreach ([
            [self::JOB_DIRECT_INVOICE, null, $directInvoice->id],
            [self::JOB_DIRECT_QUOTATION, null, $directQuotation->id],
            [self::JOB_PARENT_INVOICE, self::ORDER_PARENT_INVOICE, null],
            [self::JOB_PARENT_QUOTATION, self::ORDER_PARENT_QUOTATION, null],
        ] as [$id, $orderId, $billId]) {
            WorkOrder::withoutEvents(fn () => WorkOrder::factory()->create([
                'id' => $id, 'entity_id' => self::ENTITY_PRIMARY, 'customer_order_id' => $orderId,
                'bill_id' => $billId, 'customer_id' => self::CUSTOMER_PRIMARY, 'status' => 'FINISHED',
                'created_at' => $issued, 'updated_at' => $issued,
            ]));
        }

        // jobs_finished_unbilled must count DELIVERED alongside FINISHED.
        WorkOrder::withoutEvents(fn () => WorkOrder::factory()->create([
            'id' => self::JOB_DIRECT_DELIVERED, 'entity_id' => self::ENTITY_PRIMARY, 'customer_order_id' => null,
            'bill_id' => null, 'customer_id' => self::CUSTOMER_PRIMARY, 'status' => 'DELIVERED',
            'created_at' => $issued, 'updated_at' => $issued,
        ]));
    }

    /**
     * ar_aging 10-bill boundary fixture, same day offsets already proven by
     * BillParityFixtures::seedAgingBoundaryBills() / DefinitionParityTest
     * (bucket bounds: dpd<=0 Current, <=30 "1-30", <=60 "31-60", <=90 "61-90",
     * else "91+" — BucketSetDefinition::caseSql()):
     *   dpd=1,30,31,60,61,90,91  -> the four bucket-transition edges (0/1,30/31,60/61,90/91)
     *   dpd=-5 (due in 5 days)   -> Current/not due, deep interior
     *   dpd=45, outstanding=0    -> fully-paid overdue: EXCLUDED via appliesFilterSql's outstanding>0 gate
     *   due_date=NULL            -> EXCLUDED via appliesFilterSql's days_past_due IS NOT NULL gate
     * Plus BILL_AGING_EPSILON (dpd=45, outstanding=0.01) for the overdue-listing
     * epsilon counterfactual (Success Contract oracle rule 6 / Pass 6 BLOCKER-2:
     * no 0.01 epsilon anywhere — outstanding>0 is exact).
     *
     * Eligible for ar_aging (outstanding>0 AND days_past_due IS NOT NULL): the
     * 7 boundary bills + BILL_AGING_NOT_DUE + BILL_AGING_EPSILON = 9 rows.
     * Bucket contents [invoices_count, bill_outstanding]:
     *   Current/not due : {dpd=-5}                    -> [1, 100.00]
     *   1-30             : {dpd=1, dpd=30}             -> [2, 200.00]
     *   31-60            : {dpd=31, dpd=60, dpd=45(eps)} -> [3, 200.01]
     *   61-90            : {dpd=61, dpd=90}             -> [2, 200.00]
     *   91+              : {dpd=91}                     -> [1, 100.00]
     *
     * overdue-invoice ROWS listing (days_past_due>30 AND outstanding>0): the
     * dpd=31,60,61,90,91 bills plus the epsilon row (dpd=45, outstanding=0.01)
     * = 6 rows. BILL_AGING_PAID_OVERDUE (dpd=45, outstanding=0) is excluded
     * — the counterfactual proving the outstanding>0 gate, not a date gate.
     */
    private function seedAgingAndOverdueFixture(): void
    {
        $issued = $this->recentTimestamp(10);

        $unpaid = [
            self::BILL_AGING_1 => 1,
            self::BILL_AGING_30 => 30,
            self::BILL_AGING_31 => 31,
            self::BILL_AGING_60 => 60,
            self::BILL_AGING_61 => 61,
            self::BILL_AGING_90 => 90,
            self::BILL_AGING_91 => 91,
        ];
        foreach ($unpaid as $billId => $daysPastDue) {
            $bill = $this->bill($billId, self::ENTITY_PRIMARY, [
                'type' => 'Invoice',
                'invoice_number' => "INV-ORACLE-{$billId}",
                'description' => "Oracle ar_aging boundary fixture, days_past_due={$daysPastDue}",
            ], $issued, $this->dueDateDaysAgo($daysPastDue));
            $this->billItem($bill->id, price: 100.0, quantity: 1, discount: 0.0, tax: 0.0);
        }

        $bill = $this->bill(self::BILL_AGING_NOT_DUE, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-'.self::BILL_AGING_NOT_DUE,
            'description' => 'Oracle ar_aging boundary fixture, not yet due (days_past_due=-5)',
        ], $issued, $this->dueDateDaysAhead(5));
        $this->billItem($bill->id, price: 100.0, quantity: 1, discount: 0.0, tax: 0.0);

        $bill = $this->bill(self::BILL_AGING_PAID_OVERDUE, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-'.self::BILL_AGING_PAID_OVERDUE,
            'description' => 'Oracle ar_aging counterfactual: fully paid but overdue — excluded via outstanding>0',
        ], $issued, $this->dueDateDaysAgo(45));
        $this->billItem($bill->id, price: 100.0, quantity: 1, discount: 0.0, tax: 0.0);
        $this->cashPayment(self::ACCOUNT_CASH, self::ENTITY_PRIMARY, $bill->id, 100.0, $issued);

        $bill = $this->bill(self::BILL_AGING_NULL_DUE, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-'.self::BILL_AGING_NULL_DUE,
            'description' => 'Oracle ar_aging counterfactual: NULL due_date — excluded, lands in no bucket',
        ], $issued, null);
        $this->billItem($bill->id, price: 100.0, quantity: 1, discount: 0.0, tax: 0.0);

        $bill = $this->bill(self::BILL_AGING_EPSILON, self::ENTITY_PRIMARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-'.self::BILL_AGING_EPSILON,
            'description' => 'Oracle overdue-listing epsilon counterfactual: 0 < outstanding <= 0.01, no epsilon gate',
        ], $issued, $this->dueDateDaysAgo(45));
        $this->billItem($bill->id, price: 100.01, quantity: 1, discount: 0.0, tax: 0.0);
        $this->cashPayment(self::ACCOUNT_CASH, self::ENTITY_PRIMARY, $bill->id, 100.0, $issued);
    }

    /**
     * A Payable Quotation bill with items: 'payable_quotation_item' resource
     * predicate is bill_type='Payable Quotation' AND bill_payable='Yes'; the
     * product_revenue measure runs on 'invoice_item' (bill_type='Invoice').
     * If the per-kind item-resource boundary leaked, this bill's
     * item_revenue (1000.00) would inflate product_revenue by a detectable
     * amount (1726.01 -> 2726.01 — see report literal table).
     */
    private function seedProductRevenueBoundary(): void
    {
        $bill = $this->bill(self::BILL_PAYABLE_QUOTATION, self::ENTITY_PRIMARY, [
            'type' => 'Payable Quotation',
            'payable' => 'Yes',
            'invoice_number' => 'PQ-ORACLE-'.self::BILL_PAYABLE_QUOTATION,
            'description' => 'Oracle product_revenue per-kind boundary fixture (Payable Quotation)',
        ], $this->recentTimestamp(10), null);
        $this->billItem($bill->id, price: 500.0, quantity: 2, discount: 0.0, tax: 0.0);
    }

    /**
     * Cross-entity isolation: mirrors the aging cluster and the ledger-
     * divergence cluster under ENTITY_SECONDARY. A plan scoped to
     * ENTITY_PRIMARY must return zero rows / unchanged totals when these
     * exist — if the entity predicate leaked, PRIMARY's ar_aging 31-60
     * bucket would gain BILL_SECONDARY_AGING and PRIMARY's ledger_revenue
     * would read 400.00 instead of 200.00.
     *
     * BILL_SECONDARY_AGING: price=100, qty=1, disc=0, tax=0, due 45 days ago
     *   -> items_total=100.00, no payment -> outstanding=100.00 (dpd=45, 31-60 bucket shape).
     * BILL_SECONDARY_LEDGER: price=100, qty=2, disc=20, tax=5
     *   -> itemValue = 200-20 = 180.00; items_total = 180 + 180*0.05 = 189.00
     *   No cash payment — only convert()-style postings (see BILL_LEDGER_PRIMARY's
     *   docblock for why cash + discount-posting never combine on one bill here):
     *   revenue leg price*qty=200.00 (credit, OPERATING_REVENUE);
     *   tax leg (180)*(5/100)=9.00 (credit, excluded); discount leg 20.00 (debit, excluded)
     *   -> ledger_revenue(SECONDARY)=200.00, invoiced_revenue(SECONDARY)=189.00.
     */
    private function seedCrossEntityMirror(): void
    {
        $issued = $this->recentTimestamp(10);

        $bill = $this->bill(self::BILL_SECONDARY_AGING, self::ENTITY_SECONDARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-'.self::BILL_SECONDARY_AGING,
            'description' => 'Oracle cross-entity isolation mirror: aging/overdue row under ENTITY_SECONDARY',
        ], $issued, $this->dueDateDaysAgo(45));
        $this->billItem($bill->id, price: 100.0, quantity: 1, discount: 0.0, tax: 0.0);

        $bill = $this->bill(self::BILL_SECONDARY_LEDGER, self::ENTITY_SECONDARY, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-ORACLE-'.self::BILL_SECONDARY_LEDGER,
            'description' => 'Oracle cross-entity isolation mirror: ledger/invoiced divergence under ENTITY_SECONDARY',
        ], $issued, null);
        $this->billItem($bill->id, price: 100.0, quantity: 2, discount: 20.0, tax: 5.0);
        $this->postConvertJournals(
            entityId: self::ENTITY_SECONDARY,
            billId: $bill->id,
            receivableId: self::ACCOUNT_RECEIVABLE_SECONDARY,
            revenueId: self::ACCOUNT_REVENUE_SECONDARY,
            taxLiabilityId: self::ACCOUNT_TAX_LIABILITY_SECONDARY,
            salesDiscountId: self::ACCOUNT_SALES_DISCOUNT_SECONDARY,
            grossPriceQty: 200.0,
            taxAmount: 9.0,
            discountAmount: 20.0,
            postDate: $issued,
            documentPrefix: 'ORACLE-9902',
        );
    }

    // -- Row builders -------------------------------------------------------

    private function customer(int $id, int $entityId, int $currencyId, string $name): Customer
    {
        return Customer::withoutEvents(fn () => Customer::factory()->create([
            'id' => $id,
            'entity_id' => $entityId,
            'currency_id' => $currencyId,
            'name' => $name,
            'email' => "customer-{$id}@oracle.test",
            'created_at' => $this->recentTimestamp(15),
            'updated_at' => $this->recentTimestamp(15),
        ]));
    }

    private function account(int $id, int $entityId, int $currencyId, string $accountType, string $name): Account
    {
        return Account::withoutEvents(fn () => Account::factory()->create([
            'id' => $id,
            'entity_id' => $entityId,
            'currency_id' => $currencyId,
            'account_type' => $accountType,
            'name' => $name,
            'isSystemAccount' => true,
        ]));
    }

    private function order(int $id, int $entityId, int $customerId, string $orderNumber, string $status): CustomerOrder
    {
        $timestamp = $this->recentTimestamp(10);

        return CustomerOrder::withoutEvents(fn () => CustomerOrder::factory()->create([
            'id' => $id,
            'entity_id' => $entityId,
            'customer_id' => $customerId,
            'order_number' => $orderNumber,
            'status' => $status,
            'created_at' => $timestamp,
            'updated_at' => $timestamp,
        ]));
    }

    /** @param  array<string, mixed>  $attributes */
    private function bill(int $id, int $entityId, array $attributes, string $createdAt, ?string $dueDate): Bill
    {
        $customerId = $entityId === self::ENTITY_PRIMARY ? self::CUSTOMER_PRIMARY : self::CUSTOMER_SECONDARY;
        $currencyId = $attributes['currency_id'] ?? ($entityId === self::ENTITY_PRIMARY ? self::CURRENCY_PRIMARY : self::CURRENCY_SECONDARY);

        return Bill::withoutEvents(fn () => Bill::factory()->create([
            ...$attributes,
            'id' => $id,
            'entity_id' => $entityId,
            'customer_id' => $customerId,
            'currency_id' => $currencyId,
            'control_number_id' => 0,
            'tax' => 0,
            'due_date' => $dueDate,
            'created_at' => $createdAt,
            'updated_at' => $createdAt,
        ]));
    }

    private function billItem(int $billId, float $price, int $quantity, float $discount, float $tax): BillItem
    {
        return BillItem::create([
            'bill_id' => $billId,
            'product_id' => 1,
            'tax_id' => 1,
            'price' => $price,
            'quantity' => $quantity,
            'discount' => $discount,
            'tax' => $tax,
            'description' => 'Oracle fixture line item',
        ]);
    }

    /** Single-row qualifying payment (mirrors AiProjectionContractSeeder::payment()). */
    private function cashPayment(int $accountId, int $entityId, int $billId, float $debit, string $postDate): void
    {
        Journal::withoutEvents(fn () => Journal::create([
            'entity_id' => $entityId,
            'period_id' => 1,
            'currency_id' => $entityId === self::ENTITY_PRIMARY ? self::CURRENCY_PRIMARY : self::CURRENCY_SECONDARY,
            'document_no' => "ORACLE-PAY-{$billId}",
            'post_date' => $postDate,
            'account_id' => $accountId,
            'bill_id' => $billId,
            'debit' => $debit,
            'credit' => 0,
            'foreign_debit' => 0,
            'foreign_credit' => 0,
            'exchange_rate' => 1,
            'description' => "Oracle fixture payment for bill {$billId}",
        ]));
    }

    /**
     * Three double-entry pairs mirroring BillController::convert()'s posting
     * shape (BillController.php:130-183): revenue (gross price*qty), tax
     * (on the discounted base), discount (debits the OPERATING_EXPENSE
     * "Sales Discount" account, never revenue).
     */
    private function postConvertJournals(
        int $entityId,
        int $billId,
        int $receivableId,
        int $revenueId,
        int $taxLiabilityId,
        int $salesDiscountId,
        float $grossPriceQty,
        float $taxAmount,
        float $discountAmount,
        string $postDate,
        string $documentPrefix,
    ): void {
        $currencyId = $entityId === self::ENTITY_PRIMARY ? self::CURRENCY_PRIMARY : self::CURRENCY_SECONDARY;
        $pair = function (string $doc, float $amount, int $debitAccount, int $creditAccount, string $description) use ($entityId, $currencyId, $billId, $postDate): void {
            Journal::withoutEvents(function () use ($entityId, $currencyId, $billId, $postDate, $doc, $amount, $debitAccount, $creditAccount, $description): void {
                Journal::create([
                    'entity_id' => $entityId,
                    'period_id' => 1,
                    'currency_id' => $currencyId,
                    'document_no' => $doc,
                    'post_date' => $postDate,
                    'account_id' => $debitAccount,
                    'bill_id' => $billId,
                    'debit' => $amount,
                    'credit' => 0,
                    'foreign_debit' => $amount,
                    'foreign_credit' => 0,
                    'exchange_rate' => 1,
                    'description' => $description,
                ]);
                Journal::create([
                    'entity_id' => $entityId,
                    'period_id' => 1,
                    'currency_id' => $currencyId,
                    'document_no' => $doc,
                    'post_date' => $postDate,
                    'account_id' => $creditAccount,
                    'bill_id' => $billId,
                    'debit' => 0,
                    'credit' => $amount,
                    'foreign_debit' => 0,
                    'foreign_credit' => $amount,
                    'exchange_rate' => 1,
                    'description' => $description,
                ]);
            });
        };

        // revenue leg — see BillController.php:151-158, credits gross price*qty.
        $pair("{$documentPrefix}-REV", $grossPriceQty, $receivableId, $revenueId, "Oracle sales posting for bill {$billId}");
        // tax leg — excluded from ledger_revenue (not OPERATING_REVENUE/NON_OPERATING_REVENUE).
        $pair("{$documentPrefix}-TAX", $taxAmount, $receivableId, $taxLiabilityId, "Oracle tax posting for bill {$billId}");
        // discount leg — debits Sales Discount (OPERATING_EXPENSE), never revenue.
        $pair("{$documentPrefix}-DISC", $discountAmount, $salesDiscountId, $receivableId, "Oracle discount posting for bill {$billId}");
    }

    // -- Date helpers ---------------------------------------------------------

    /** Fixed relative timestamp derived from the paired oracle's Business Date. */
    private function recentTimestamp(int $daysAgo): string
    {
        return (new DateTimeImmutable(self::BUSINESS_DATE.' 09:00:00'))
            ->modify("-{$daysAgo} days")
            ->format('Y-m-d H:i:s');
    }

    /**
     * due_date exactly N days before the paired request Business Date.
     */
    private function dueDateDaysAgo(int $days): string
    {
        return $this->sessionDateOffset(-$days);
    }

    private function dueDateDaysAhead(int $days): string
    {
        return $this->sessionDateOffset($days);
    }

    /** @param  int  $offsetDays  positive = future, negative = past, 0 = today */
    private function sessionDateOffset(int $offsetDays): string
    {
        $modifier = ($offsetDays >= 0 ? '+' : '').$offsetDays.' days';

        return (new DateTimeImmutable(self::BUSINESS_DATE))->modify($modifier)->format('Y-m-d');
    }
}
