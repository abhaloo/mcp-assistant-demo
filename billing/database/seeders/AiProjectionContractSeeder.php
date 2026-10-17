<?php

declare(strict_types=1);

namespace Database\Seeders;

use App\Models\Account;
use App\Models\Bill;
use App\Models\BillItem;
use App\Models\CreditNote;
use App\Models\Customer;
use App\Models\Department;
use App\Models\Entity;
use App\Models\Journal;
use App\Models\WorkOrder;
use Illuminate\Database\Seeder;

/**
 * Task 3B (Phase 3B, docs/superpowers/reports/2026-07-23-ask-ai-context-simplification-implementation-plan.md,
 * SS8) — an INDEPENDENTLY AUTHORED fixture for the paired real-view
 * projection contract test (RAG-side: tests/integration/test_billing_projection_contract.py
 * + tests/integration/fixtures/billing_projection_expected.json in the
 * mcp-rag-qa repo).
 *
 * NEVER derive rows, fields, or expected visibility from the manifest or
 * from PolicyManifestExporter — every entity, department, and
 * discriminator value below is hand-picked here, independently of the
 * RAG-side expected-IDs fixture ("mirroring the seeder's stable IDs by
 * DESIGN AGREEMENT, not by import" — task-3B-brief.md). A fixture built
 * FROM the manifest can only prove the manifest agrees with itself; this
 * seeder exists specifically so the consumer is tested against data whose
 * shape was decided independently of that artifact.
 *
 * Explicit stable IDs (501/502 entities, 601/602 departments, 7xx
 * customers, 6xx jobs, 8xx bills, 9xx credit notes) plus explicit
 * created_at/updated_at timestamps on every row make every downstream
 * assertion — including the exact ascending/descending sort-order
 * sequence — exact rather than dependent on auto-increment/`now()`
 * ordering.
 *
 * withoutEvents() wraps every Bill/Customer/WorkOrder create() call:
 * App\Traits\EntityTrait::booted() unconditionally overwrites
 * `entity_id = auth()->user()->entity_id ?? 1` on creating() — with no
 * authenticated user in a seeder/CI run, every entity_id (INCLUDING the
 * "allowed" 501) would silently collapse to 1 without this, discarding
 * this fixture's explicit entity assignment (mirrors
 * tests/Feature/Authorization/Projections/ProjectionViewIsolationTest.php's
 * established customerFor()/billFor() pattern).
 *
 * NOT wired into DatabaseSeeder::run() — this seeder is invoked standalone,
 * by class name, both by its own Billing-side seeder test
 * (tests/Feature/Database/Seeders/AiProjectionContractSeederTest.php) and
 * by the cross-repo release workflow
 * (.github/workflows/ai-projection-contract.yml), never as part of the
 * default full-application seed.
 */
final class AiProjectionContractSeeder extends Seeder
{
    // -- Entities (the "entity" authorization stratum) ----------------------
    public const ENTITY_ALLOWED = 501;

    public const ENTITY_FORBIDDEN = 502;

    // -- Departments (the "department" authorization stratum, job resource) -
    public const DEPARTMENT_ALLOWED = 601;

    public const DEPARTMENT_FORBIDDEN = 602;

    // -- Customers (entity stratum + search/get/sort-order strata) ----------
    public const CUSTOMER_ALLOWED_PRIMARY = 701;

    public const CUSTOMER_FORBIDDEN_ENTITY = 702;

    public const CUSTOMER_ALLOWED_SECOND = 703;

    public const CUSTOMER_ALLOWED_THIRD = 704;

    // -- Jobs (department stratum + Task B2 status/priority/created_at strata) -
    public const JOB_ALLOWED_DEPARTMENT = 611;

    public const JOB_FORBIDDEN_DEPARTMENT = 612;

    /**
     * Task B2 / §requirement 4 — the "unassigned department" stratum:
     * department_id IS NULL exercises WorkOrderVisibilityScope's
     * filter_when_present semantic (a null-department PRINCIPAL sees every
     * same-entity job regardless of department; a null-department RECORD
     * like this one is visible to any same-entity principal) and proves
     * ai_v1_job's `department_name` LEFT JOIN degrades to NULL rather than
     * failing when there is no department to join.
     */
    public const JOB_UNASSIGNED_DEPARTMENT = 613;

    // Deliberately adversarial: this job shares department 601 with 611 but
    // belongs to entity 502. The paired RAG contract must prove its entity
    // predicate excludes it before the matching department can matter.
    public const JOB_SAME_DEPARTMENT_FOREIGN_ENTITY = 614;

    // -- Bills (Invoice/Quotation/Payable Quotation subtype strata) ---------
    public const BILL_INVOICE = 801;

    public const BILL_QUOTATION = 802;

    public const BILL_PAYABLE_QUOTATION_VALID = 803;

    /** type='Payable Quotation', payable='No' — discriminator mismatch #1. */
    public const BILL_PAYABLE_QUOTATION_MISMATCH_PAYABLE_NO = 804;

    /** type='Quotation', payable='Yes' — discriminator mismatch #2. */
    public const BILL_QUOTATION_MISMATCH_PAYABLE_YES = 805;

    /** Invoice under the FORBIDDEN entity — the credit note's foreign parent. */
    public const BILL_FOREIGN_ENTITY_INVOICE = 811;

    // -- Credit notes (via_parent entity-scope stratum) ----------------------
    public const CREDIT_NOTE_VALID = 901;

    public const CREDIT_NOTE_FOREIGN_PARENT = 902;

    public function run(): void
    {
        $this->seedEntities();
        $this->seedDepartments();
        $this->seedCustomers();
        $this->seedJobs();
        $this->seedBills();
        $this->seedPayments();
        $this->seedCreditNotes();
    }

    private function seedEntities(): void
    {
        Entity::factory()->create(['id' => self::ENTITY_ALLOWED, 'name' => 'Task 3B Contract Entity — Allowed']);
        Entity::factory()->create(['id' => self::ENTITY_FORBIDDEN, 'name' => 'Task 3B Contract Entity — Forbidden']);
    }

    private function seedDepartments(): void
    {
        Department::factory()->create([
            'id' => self::DEPARTMENT_ALLOWED,
            'name' => 'Task 3B Contract Department — Allowed',
        ]);
        Department::factory()->create([
            'id' => self::DEPARTMENT_FORBIDDEN,
            'name' => 'Task 3B Contract Department — Forbidden',
        ]);
    }

    /**
     * Deterministic created_at values, one day apart and strictly increasing
     * with id (701 < 703 < 704), so ascending/descending sort-order
     * assertions have one unambiguous correct sequence. 702 (forbidden
     * entity) shares 701's created_at deliberately — it must never appear
     * in ANY result regardless of sort order, so its timestamp is
     * irrelevant to the sort proof.
     *
     * 701/702 share the "Northwind" name substring on purpose: a
     * free-text search for "Northwind" scoped to entity 501 must return
     * ONLY 701 — proving the entity predicate applies even when the text
     * match alone would also hit the forbidden-entity row.
     */
    private function seedCustomers(): void
    {
        $this->customer(
            self::CUSTOMER_ALLOWED_PRIMARY,
            self::ENTITY_ALLOWED,
            'Northwind Traders Print',
            'Northwind Print Ltd',
            '500-701-0001',
            'c701@example.test',
            '2020-01-01 08:00:00',
        );
        $this->customer(
            self::CUSTOMER_FORBIDDEN_ENTITY,
            self::ENTITY_FORBIDDEN,
            'Northwind Traders Overseas',
            'Northwind Overseas Ltd',
            '500-702-0002',
            'c702@example.test',
            '2020-01-01 08:00:00',
        );
        $this->customer(
            self::CUSTOMER_ALLOWED_SECOND,
            self::ENTITY_ALLOWED,
            'Beta Print Co',
            'Beta Print Holdings',
            '500-703-0003',
            'c703@example.test',
            '2020-01-02 08:00:00',
        );
        $this->customer(
            self::CUSTOMER_ALLOWED_THIRD,
            self::ENTITY_ALLOWED,
            'Gamma Print Co',
            'Gamma Print Holdings',
            '500-704-0004',
            'c704@example.test',
            '2020-01-03 08:00:00',
        );
    }

    /**
     * The two original jobs (611/612) share the SAME entity (501) and
     * overlapping title text ("... department contract job") on purpose —
     * the department stratum must be proven by department_id scoping
     * alone, not by an entity or text-search difference a weaker fixture
     * could hide behind.
     *
     * Task B2 / §requirement 4 additions: 611/612/613 now carry distinct
     * `status`/`priority` values (WorkOrder::STATUS_COLORS/priority enum —
     * app/Models/WorkOrder.php) and strictly increasing `created_at`
     * (one day apart, mirroring the customer stratum's own boundary-value
     * pattern), so a future typed range/eq filter or group_count/top_groups
     * proof has real, unambiguous strata to assert against — not just
     * three rows that all happen to share the factory default
     * ('CREATED'/'normal'). 613 adds the "unassigned department" stratum
     * (see its own constant docblock) and a title carrying a diacritic +
     * mixed-case run (`CAFÉ`) specifically to exercise the collation path
     * a plain ASCII title never would — a case-insensitive
     * utf8mb4_unicode_ci search for "café" must still match it.
     */
    private function seedJobs(): void
    {
        $this->job(
            self::JOB_ALLOWED_DEPARTMENT,
            self::ENTITY_ALLOWED,
            self::DEPARTMENT_ALLOWED,
            'WO-CONTRACT-ALLOWED',
            'Allowed department contract job',
            'Task 3B department-allow stratum',
            status: 'IN PROGRESS',
            priority: 'high',
            createdAt: '2020-01-05 08:00:00',
        );
        $this->job(
            self::JOB_FORBIDDEN_DEPARTMENT,
            self::ENTITY_ALLOWED,
            self::DEPARTMENT_FORBIDDEN,
            'WO-CONTRACT-FORBIDDEN',
            'Forbidden department contract job',
            'Task 3B department-deny stratum',
            status: 'FINISHED',
            priority: 'normal',
            createdAt: '2020-01-06 08:00:00',
        );
        $this->job(
            self::JOB_UNASSIGNED_DEPARTMENT,
            self::ENTITY_ALLOWED,
            departmentId: null,
            workNumber: 'WO-CONTRACT-UNASSIGNED',
            title: 'CAFÉ Rebrand — unassigned department, collation search probe',
            description: 'Task B2 unassigned-department + collation-search stratum',
            status: 'CREATED',
            priority: 'normal',
            createdAt: '2020-01-07 08:00:00',
        );
        $this->job(
            self::JOB_SAME_DEPARTMENT_FOREIGN_ENTITY,
            self::ENTITY_FORBIDDEN,
            self::DEPARTMENT_ALLOWED,
            'WO-CONTRACT-FOREIGN-SAME-DEPARTMENT',
            'Foreign entity department contract job',
            'Adversarial entity-before-department scope stratum',
            status: 'CREATED',
            priority: 'normal',
            createdAt: '2020-01-08 08:00:00',
        );
    }

    /**
     * All six bills share entity 501 + customer 701 except 811, which is
     * the foreign-entity invoice that becomes credit note 902's parent
     * below. 804/805 are the two Payable Quotation discriminator
     * mismatches Gate 3B names explicitly.
     */
    private function seedBills(): void
    {
        $this->bill(self::BILL_INVOICE, self::ENTITY_ALLOWED, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-CONTRACT-801',
            'description' => 'Task 3B invoice',
        ]);
        $this->bill(self::BILL_QUOTATION, self::ENTITY_ALLOWED, [
            'type' => 'Quotation',
            'invoice_number' => 'QUO-CONTRACT-802',
            'description' => 'Task 3B quotation',
        ]);
        $this->bill(self::BILL_PAYABLE_QUOTATION_VALID, self::ENTITY_ALLOWED, [
            'type' => 'Payable Quotation',
            'payable' => 'Yes',
            'invoice_number' => 'PQ-CONTRACT-803',
            'description' => 'Task 3B valid payable quotation',
        ]);
        $this->bill(self::BILL_PAYABLE_QUOTATION_MISMATCH_PAYABLE_NO, self::ENTITY_ALLOWED, [
            'type' => 'Payable Quotation',
            'payable' => 'No',
            'invoice_number' => 'PQ-CONTRACT-804-MISMATCH',
            'description' => "Task 3B discriminator mismatch #1: type='Payable Quotation', payable='No'",
        ]);
        $this->bill(self::BILL_QUOTATION_MISMATCH_PAYABLE_YES, self::ENTITY_ALLOWED, [
            'type' => 'Quotation',
            'payable' => 'Yes',
            'invoice_number' => 'QUO-CONTRACT-805-MISMATCH',
            'description' => "Task 3B discriminator mismatch #2: type='Quotation', payable='Yes'",
        ]);
        $this->bill(self::BILL_FOREIGN_ENTITY_INVOICE, self::ENTITY_FORBIDDEN, [
            'type' => 'Invoice',
            'invoice_number' => 'INV-CONTRACT-811-FOREIGN',
            'description' => 'Task 3B foreign-entity invoice (credit note 902 parent)',
        ]);
    }

    /**
     * Task B2 / §requirement 4 — payment-state diversity across the three
     * bills that reach a real ai_v1_* subtype view (the discriminator-
     * mismatch/foreign-entity bills above exist to prove predicate/entity
     * correctness, not payment state, and are left with zero items —
     * unchanged). Mirrors
     * tests/Feature/Authorization/Projections/PaymentStatusParityTest.php's
     * own BillItem+Journal fixture shape (a qualifying CASH-account debit —
     * see ProjectionDefinitions::billView()'s own QUALIFYING_ACCOUNT_TYPES)
     * so the SAME payment_status CASE expression this seeder feeds gets
     * real PAID/PARTIAL PAID/UNPAID diversity, not three bills that all
     * trivially report PAID by having zero items and zero payments.
     */
    private function seedPayments(): void
    {
        $cashAccount = Account::withoutEvents(
            fn () => Account::factory()->cash()->create(['entity_id' => self::ENTITY_ALLOWED])
        );

        // BILL_INVOICE (801): $100 of items, no qualifying payment -> UNPAID.
        $this->billItem(self::BILL_INVOICE, price: 100.0, quantity: 1.0, discount: 0.0, tax: 0.0);

        // BILL_QUOTATION (802): $100 of items, paid in full -> PAID.
        $this->billItem(self::BILL_QUOTATION, price: 100.0, quantity: 1.0, discount: 0.0, tax: 0.0);
        $this->payment($cashAccount->id, self::BILL_QUOTATION, 100.0);

        // BILL_PAYABLE_QUOTATION_VALID (803): $100 of items, $40 paid -> PARTIAL PAID.
        $this->billItem(self::BILL_PAYABLE_QUOTATION_VALID, price: 100.0, quantity: 1.0, discount: 0.0, tax: 0.0);
        $this->payment($cashAccount->id, self::BILL_PAYABLE_QUOTATION_VALID, 40.0);
    }

    private function billItem(int $billId, float $price, float $quantity, float $discount, float $tax): void
    {
        BillItem::create([
            'bill_id' => $billId,
            'product_id' => 1,
            'tax_id' => 1,
            'price' => $price,
            'quantity' => $quantity,
            'discount' => $discount,
            'tax' => $tax,
            'description' => 'Task B2 contract payment-state item',
        ]);
    }

    private function payment(int $accountId, int $billId, float $debit): void
    {
        Journal::withoutEvents(fn () => Journal::factory()->create([
            'entity_id' => self::ENTITY_ALLOWED,
            'account_id' => $accountId,
            'bill_id' => $billId,
            'debit' => $debit,
            'credit' => 0,
        ]));
    }

    /**
     * 901's parent (801) is entity 501 — a valid credit note. 902's parent
     * (811) is entity 502 — credit_note has no entity_id column of its
     * own, so this is the ONLY way to prove the via_parent scope actually
     * follows the join rather than defaulting open.
     */
    private function seedCreditNotes(): void
    {
        $timestamp = '2020-01-11 08:00:00';
        CreditNote::create([
            'id' => self::CREDIT_NOTE_VALID,
            'bill_id' => self::BILL_INVOICE,
            'description' => 'Task 3B valid credit note',
            'payment_id' => 0,
            'created_at' => $timestamp,
            'updated_at' => $timestamp,
        ]);
        CreditNote::create([
            'id' => self::CREDIT_NOTE_FOREIGN_PARENT,
            'bill_id' => self::BILL_FOREIGN_ENTITY_INVOICE,
            'description' => 'Task 3B foreign-parent credit note',
            'payment_id' => 0,
            'created_at' => $timestamp,
            'updated_at' => $timestamp,
        ]);
    }

    private function customer(
        int $id,
        int $entityId,
        string $name,
        string $businessName,
        string $phone,
        string $email,
        string $timestamp,
    ): Customer {
        return Customer::withoutEvents(fn () => Customer::factory()->create([
            'id' => $id,
            'entity_id' => $entityId,
            'name' => $name,
            'business_name' => $businessName,
            'phone' => $phone,
            'email' => $email,
            'created_at' => $timestamp,
            'updated_at' => $timestamp,
        ]));
    }

    /**
     * Task B2 / §requirement 4 — `departmentId` is now nullable (the
     * "unassigned department" stratum, JOB_UNASSIGNED_DEPARTMENT) and
     * `status`/`priority`/`createdAt` are explicit parameters instead of
     * always the factory default ('CREATED'/'normal') and one shared
     * timestamp, so every seeded job carries a real, deliberately-chosen
     * value for every Minimum Structured Recovery field (ADR 0030) rather
     * than an incidental default.
     */
    private function job(
        int $id,
        int $entityId,
        ?int $departmentId,
        string $workNumber,
        string $title,
        string $description,
        string $status = 'CREATED',
        string $priority = 'normal',
        string $createdAt = '2020-01-05 08:00:00',
    ): WorkOrder {
        return WorkOrder::withoutEvents(fn () => WorkOrder::factory()->create([
            'id' => $id,
            'entity_id' => $entityId,
            'department_id' => $departmentId,
            'customer_id' => self::CUSTOMER_ALLOWED_PRIMARY,
            'work_number' => $workNumber,
            'title' => $title,
            'description' => $description,
            'status' => $status,
            'priority' => $priority,
            'created_at' => $createdAt,
            'updated_at' => $createdAt,
        ]));
    }

    /** @param  array<string, mixed>  $attributes */
    private function bill(int $id, int $entityId, array $attributes): Bill
    {
        $timestamp = '2020-01-10 08:00:00';
        $customerId = $entityId === self::ENTITY_ALLOWED
            ? self::CUSTOMER_ALLOWED_PRIMARY
            : self::CUSTOMER_FORBIDDEN_ENTITY;

        return Bill::withoutEvents(fn () => Bill::factory()->create([
            ...$attributes,
            'id' => $id,
            'entity_id' => $entityId,
            'customer_id' => $customerId,
            'created_at' => $timestamp,
            'updated_at' => $timestamp,
        ]));
    }
}
