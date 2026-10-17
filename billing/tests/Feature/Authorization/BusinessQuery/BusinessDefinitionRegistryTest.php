<?php

declare(strict_types=1);

namespace Tests\Feature\Authorization\BusinessQuery;

use App\Authorization\BusinessQuery\BusinessDefinitionRegistry;
use App\Authorization\BusinessQuery\BusinessDefinitionsExporter;
use App\Authorization\BusinessQuery\DimensionDefinition;
use App\Authorization\BusinessQuery\MeasureDefinition;
use App\Authorization\BusinessQuery\ResourceBinding;
use App\Authorization\BusinessQuery\SemanticViews;
use App\Authorization\BusinessRecordRegistry;
use App\Models\Account;
use Database\Seeders\PermissionTableSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use PHPUnit\Framework\Attributes\Test;
use ReflectionClass;
use ReflectionMethod;
use Tests\TestCase;

final class BusinessDefinitionRegistryTest extends TestCase
{
    use RefreshDatabase;

    /** @test */
    public function test_registry_validates_clean(): void
    {
        $this->assertSame([], BusinessDefinitionRegistry::default()->validate());
        $this->assertSame([], (new BusinessDefinitionsExporter)->validateAll());
    }

    /** @test */
    public function test_every_join_side_declares_a_primary_key(): void
    {
        $registry = BusinessDefinitionRegistry::default();
        foreach ($registry->definitions()['joins'] as $join) {
            $this->assertNotNull($registry->primaryKeyFor($join->fromResource), $join->fromResource);
            $this->assertNotNull($registry->primaryKeyFor($join->toResource), $join->toResource);
        }
    }

    #[Test]
    public function join_graph_declares_the_full_edge_set_with_columns_verified_against_semantic_views(): void
    {
        // one_to_many from invoice means invoice-side MEASURES fan-out-multiply across these edges; refusing that combination is the RAG compiler's job, not this test's.
        $registry = BusinessDefinitionRegistry::default();
        $bindings = collect($registry->definitions()['resources'])->keyBy('name');

        $expected = [
            ['customer', 'customer_order', 'one_to_many', 'id = customer_id', false],
            ['customer', 'invoice', 'one_to_many', 'id = customer_id', false],
            // work_orders.customer_order_id IS nullable
            // (migrations/2025_12_16_170310_create_customer_orders_table.php:23-28 —
            // foreignId('customer_order_id')->nullable()->constrained(...)->nullOnDelete()):
            // bill-less/order-less direct jobs exist by design (AiBusinessQueryOracleSeeder's
            // JOB_DIRECT_INVOICE/JOB_DIRECT_QUOTATION/JOB_DIRECT_DELIVERED fixtures). An INNER
            // join from customer_order would silently exclude them from any job-owned measure
            // aggregate — this edge is optional.
            ['customer_order', 'job', 'one_to_many', 'id = customer_order_id', true],
            // bill_items.bill_id is NOT NULL (migrations/2022_07_24_152701_create_bill_items_table.php:18
            // — foreignId('bill_id')->constrained(), no ->nullable()): every invoice_item row has a parent
            // invoice, so the INNER join never drops a row. Stays non-optional.
            ['invoice', 'invoice_item', 'one_to_many', 'id = bill_id', false],
            // journals.bill_id (ledger_entry's backing table) IS nullable
            // (migrations/2022_11_13_022900_create_journals_table.php:25 — foreignId('bill_id')->nullable()):
            // manual, bill-less journal postings exist by design. An INNER join from invoice would silently
            // exclude them from any ledger-owned measure aggregate — this edge is optional.
            ['invoice', 'ledger_entry', 'one_to_many', 'id = bill_id', true],
            // work_orders.customer_id is NOT NULL (migrations/2022_09_11_095642_create_work_orders_table.php:19
            // — foreignId('customer_id'), no ->nullable(); never altered by any later migration touching
            // work_orders). A direct edge exists so "jobs by customer" is answerable via this 1-hop path
            // (BFS shortest-path prefers it over the 2-hop customer->customer_order->job route) instead of
            // losing direct/orderless jobs to the optional customer_order->job edge above. Non-optional.
            ['customer', 'job', 'one_to_many', 'id = customer_id', false],
            // work_order_items.work_order_id is NOT NULL
            // (migrations/2022_09_11_095659_create_work_order_items_table.php:18 —
            // foreignId('work_order_id'), no ->nullable()): every consumption line has a
            // parent job, so the INNER join never drops a line. Non-optional, and it has
            // to be: internal_adapter refuses a measure owned by an OPTIONAL join child
            // (optional_join_unsupported), which would make product_units_used
            // unusable. The 3 orphaned rows in the 2026-08 snapshot exist because the
            // column carries no FK constraint; ai_v1_bq_work_order_item_fact's INNER
            // JOIN excludes them before the planner ever sees them.
            ['job', 'work_order_item', 'one_to_many', 'id = work_order_id', false],
            // bills.customer_order_id IS nullable
            // (migrations/2025_12_16_170310_create_customer_orders_table.php:30-33 —
            // foreignId('customer_order_id')->nullable()): direct, order-less invoices
            // exist by design; an INNER join from customer_order would silently exclude
            // them from any invoice-owned measure aggregate — this edge is optional.
            ['customer_order', 'invoice', 'one_to_many', 'id = customer_order_id', true],
            // Linked-record edges the semantic layer walks. Quotation and payable-quotation
            // lines share bill_items (bill_id NOT NULL), so those two are required; every
            // other edge below lands on a nullable foreign key and stays optional.
            ['customer', 'quotation', 'one_to_many', 'id = customer_id', true],
            ['customer', 'payable_quotation', 'one_to_many', 'id = customer_id', true],
            ['customer_order', 'quotation', 'one_to_many', 'id = customer_order_id', true],
            ['quotation', 'quotation_item', 'one_to_many', 'id = bill_id', false],
            ['payable_quotation', 'payable_quotation_item', 'one_to_many', 'id = bill_id', false],
            ['invoice', 'job', 'one_to_many', 'id = effective_bill_id', true],
            ['supplier', 'inventory', 'one_to_many', 'id = supplier_id', true],
            ['job', 'inventory', 'one_to_many', 'id = job_id', true],
        ];

        $joins = $registry->definitions()['joins'];
        $this->assertCount(count($expected), $joins);

        foreach ($expected as $i => [$fromResource, $toResource, $relationship, $onSql, $optional]) {
            $join = $joins[$i];
            $this->assertSame($fromResource, $join->fromResource);
            $this->assertSame($toResource, $join->toResource);
            $this->assertSame($relationship, $join->relationship);
            $this->assertSame($onSql, $join->onSql);
            $this->assertSame($optional, $join->optional, "join [{$fromResource}->{$toResource}] optional flag");

            [$fromColumn, $toColumn] = array_map('trim', explode('=', $onSql));
            $fromView = $bindings[$fromResource]->projectionView;
            $toView = $bindings[$toResource]->projectionView;

            $this->assertContains(
                $fromColumn,
                SemanticViews::COLUMNS[$fromView],
                "join [{$fromResource}->{$toResource}] fromColumn [{$fromColumn}] not declared on [{$fromView}]",
            );
            $this->assertContains(
                $toColumn,
                SemanticViews::COLUMNS[$toView],
                "join [{$fromResource}->{$toResource}] toColumn [{$toColumn}] not declared on [{$toView}]",
            );
        }
    }

    /** @test */
    public function test_every_capability_entry_resolves_to_a_declared_definition(): void
    {
        $definitions = BusinessDefinitionRegistry::default()->definitions();
        $names = collect($definitions['measures'])->pluck('name')
            ->merge(collect($definitions['dimensions'])->pluck('name'))
            ->merge(collect($definitions['bucket_sets'])->pluck('name'))
            ->merge(collect($definitions['segments'])->pluck('name'));
        foreach ($definitions['capabilities'] as $entry) {
            $this->assertTrue($names->contains($entry->resolvesTo), $entry->name);
        }
    }

    /** @test */
    public function test_decision_gated_entries_are_marked_and_ungranted(): void
    {
        $pending = collect(BusinessDefinitionRegistry::default()->definitions()['capabilities'])
            ->firstWhere('name', 'invoice_payment_status');
        $this->assertNotNull($pending);
        $this->assertSame('decision_pending', $pending->capabilityState);
        $this->assertSame([], $pending->requiredPermissions);
    }

    /** @test */
    public function test_financial_definitions_declare_native_currency_and_payment_meanings(): void
    {
        $registry = BusinessDefinitionRegistry::default();

        $monetaryDimensions = [
            'bill_amount' => 'invoice.currency_code',
            'bill_amount_avg' => 'invoice.currency_code',
            'cash_received' => 'invoice.currency_code',
            'receivable_settlement' => 'invoice.currency_code',
            'bill_outstanding' => 'invoice.currency_code',
            'overdue_outstanding' => 'invoice.currency_code',
            'quotation_amount' => 'quotation.currency_code',
            'invoiced_revenue' => 'invoice.currency_code',
            'ledger_revenue' => 'ledger_entry.currency_code',
            'product_revenue' => 'invoice_item.currency_code',
        ];
        foreach ($monetaryDimensions as $measure => $currencyDimension) {
            $this->assertSame($currencyDimension, $registry->measure($measure)->currencyDimension, $measure);
        }
        $this->assertSame('cash_received', $registry->measure('cash_received')->name);
        $this->assertSame('receivable_settlement', $registry->measure('receivable_settlement')->name);
    }

    /** @test */
    public function test_job_status_exports_authoritative_allowed_values(): void
    {
        // Full work_orders.status domain. DELIVERED is not a literal any current
        // write path assigns to work_orders.status (all status-setting call sites
        // write FINISHED/IN PROGRESS/CANCELLED/INVOICED/SUBMITTED; 'DELIVERED' is
        // only ever written to the sibling work_messages.status audit log — see
        // WorkDataComponent.php:387, WorkOrderComponent.php:465, ApprovalRequest.php:360).
        // But the app's own read/filter sites treat work_orders.status == 'DELIVERED'
        // as a live, expected value: Bill.php:135-136, CustomerOrderController.php:77,
        // CustomerOrderShowComponent.php:532, CustomerOrderComponent.php:241. The
        // exported domain must not refuse a value the app itself filters on.
        $status = collect(BusinessDefinitionRegistry::default()->definitions()['dimensions'])
            ->firstWhere('name', 'job.status');

        $this->assertNotNull($status);
        $this->assertSame(
            ['CREATED', 'IN PROGRESS', 'SUBMITTED', 'PENDING MOVE APPROVAL', 'INVOICED', 'FINISHED', 'CANCELLED', 'DELIVERED'],
            $status->allowedValues,
        );
    }

    #[Test]
    public function closed_domain_dimensions_all_declare_allowed_values(): void
    {
        $dimensions = collect(BusinessDefinitionRegistry::default()->definitions()['dimensions'])->keyBy('name');

        foreach (['job.status', 'customer_order.status', 'inventory.status', 'ledger_entry.account_type'] as $name) {
            $this->assertNotEmpty($dimensions[$name]->allowedValues, $name);
        }
    }

    #[Test]
    public function customer_order_status_exports_the_stored_domain(): void
    {
        // Census of every write to customer_orders.status: schema default CREATED;
        // CustomerOrderShowComponent.php:282 ACTIVE (reopenOrder), :256 FINISHED,
        // :200/:210/:219 CANCELLED. NEW/IN_PROGRESS are computed by
        // CustomerOrder::getComputedStatusAttribute(), never stored.
        $status = collect(BusinessDefinitionRegistry::default()->definitions()['dimensions'])
            ->firstWhere('name', 'customer_order.status');

        $this->assertNotNull($status);
        $this->assertSame(['CREATED', 'ACTIVE', 'FINISHED', 'CANCELLED'], $status->allowedValues);
    }

    #[Test]
    public function inventory_status_exports_the_stored_domain(): void
    {
        // Inventory::booted() auto-approves every row on create (status=APPROVED);
        // CREATED is the pre-approval schema default, still read defensively at
        // InventoryComponent.php:122.
        $status = collect(BusinessDefinitionRegistry::default()->definitions()['dimensions'])
            ->firstWhere('name', 'inventory.status');

        $this->assertNotNull($status);
        $this->assertSame(['CREATED', 'APPROVED'], $status->allowedValues);
    }

    #[Test]
    public function ledger_entry_account_type_exports_every_account_model_constant(): void
    {
        // Independent oracle: reflect Account::class directly rather than reuse
        // whatever helper the registry uses internally. getConstants() would also
        // pull in inherited string constants (e.g. Model::CREATED_AT) that are not
        // account types, so restrict to constants Account itself declares.
        $expected = [];
        foreach ((new ReflectionClass(Account::class))->getReflectionConstants() as $constant) {
            if ($constant->getDeclaringClass()->getName() !== Account::class) {
                continue;
            }
            if (is_string($constant->getValue())) {
                $expected[] = $constant->getValue();
            }
        }
        sort($expected);

        $status = collect(BusinessDefinitionRegistry::default()->definitions()['dimensions'])
            ->firstWhere('name', 'ledger_entry.account_type');

        $this->assertNotNull($status);
        $this->assertCount(19, $expected);
        $this->assertSame($expected, $status->allowedValues);
    }

    #[Test]
    public function permission_catalog_equals_the_seeders_seeded_names(): void
    {
        $method = new ReflectionMethod(BusinessDefinitionRegistry::class, 'permissionCatalog');
        $method->setAccessible(true);

        $this->assertSame(PermissionTableSeeder::permissionNames(), $method->invoke(null));
    }

    /** @test */
    public function test_ar_aging_declares_request_business_date_calculation_over_raw_due_dates(): void
    {
        $set = BusinessDefinitionRegistry::default()->bucketSet('ar_aging');

        $this->assertSame('due_date', $set->dimension);
        $this->assertTrue($set->relativeToBusinessDate);
        $this->assertSame('outstanding > 0 AND due_date IS NOT NULL', $set->appliesFilterSql);
        $this->assertStringNotContainsString('days_past_due', $set->description);
        $this->assertStringNotContainsString('CURRENT_DATE', $set->description);
    }

    /** @test */
    public function test_validate_rejects_duplicate_member_names(): void
    {
        $defs = BusinessDefinitionRegistry::default()->definitions();
        $mutated = new BusinessDefinitionRegistry([
            'resources' => $defs['resources'],
            'measures' => $defs['measures'],
            'dimensions' => array_merge($defs['dimensions'], [
                new DimensionDefinition(
                    name: $defs['measures'][0]->name,
                    owningResource: 'invoice',
                    sqlExpression: 'id',
                    type: 'number',
                    isPrimaryKey: false,
                    description: 'deliberate duplicate of a measure name',
                ),
            ]),
            'joins' => $defs['joins'],
            'bucket_sets' => $defs['bucket_sets'],
            'segments' => $defs['segments'],
            'capabilities' => $defs['capabilities'],
        ]);

        $violations = $mutated->validate();
        $this->assertNotEmpty($violations);
        $this->assertTrue(
            collect($violations)->contains(fn (string $v) => str_contains($v, 'duplicate member')),
            implode('; ', $violations),
        );
    }

    /** @test */
    public function test_validate_rejects_shared_dimension_labels_without_display_of_link(): void
    {
        $defs = BusinessDefinitionRegistry::default()->definitions();
        $mutated = new BusinessDefinitionRegistry([
            'resources' => $defs['resources'],
            'measures' => $defs['measures'],
            'dimensions' => array_merge($defs['dimensions'], [
                new DimensionDefinition(
                    name: 'invoice.thing_one',
                    owningResource: 'invoice',
                    sqlExpression: 'id',
                    type: 'string',
                    isPrimaryKey: false,
                    description: 'first dimension with shared label',
                    label: 'Thing',
                ),
                new DimensionDefinition(
                    name: 'invoice.thing_two',
                    owningResource: 'invoice',
                    sqlExpression: 'id',
                    type: 'string',
                    isPrimaryKey: false,
                    description: 'second dimension with shared label',
                    label: 'Thing',
                ),
            ]),
            'joins' => $defs['joins'],
            'bucket_sets' => $defs['bucket_sets'],
            'segments' => $defs['segments'],
            'capabilities' => $defs['capabilities'],
        ]);

        $violations = $mutated->validate();
        $this->assertNotEmpty($violations);
        $this->assertTrue(
            collect($violations)->contains(fn (string $v) => str_contains($v, 'share label')),
            implode('; ', $violations),
        );

        $withDisplayOf = new BusinessDefinitionRegistry([
            'resources' => $defs['resources'],
            'measures' => $defs['measures'],
            'dimensions' => array_merge($defs['dimensions'], [
                new DimensionDefinition(
                    name: 'invoice.thing_one',
                    owningResource: 'invoice',
                    sqlExpression: 'id',
                    type: 'string',
                    isPrimaryKey: false,
                    description: 'first dimension with shared label and displayOf',
                    label: 'Thing',
                    displayOf: 'invoice.thing_two',
                ),
                new DimensionDefinition(
                    name: 'invoice.thing_two',
                    owningResource: 'invoice',
                    sqlExpression: 'id',
                    type: 'string',
                    isPrimaryKey: false,
                    description: 'second dimension with shared label',
                    label: 'Thing',
                ),
            ]),
            'joins' => $defs['joins'],
            'bucket_sets' => $defs['bucket_sets'],
            'segments' => $defs['segments'],
            'capabilities' => $defs['capabilities'],
        ]);

        $violationsWithDisplayOf = $withDisplayOf->validate();
        $this->assertFalse(
            collect($violationsWithDisplayOf)->contains(fn (string $v) => str_contains($v, 'share label')),
            implode('; ', $violationsWithDisplayOf),
        );
    }

    #[Test]
    public function customer_order_exposes_its_human_facing_order_number(): void
    {
        // customer_orders.order_number is NOT the primary key: id 744 carries
        // order_number '740'. Without this dimension the planner could only filter
        // on customer_order.id, so "order 744" silently answered about another
        // order (2026-08-11 accuracy run, bq-04).
        $dimensions = collect(BusinessDefinitionRegistry::default()->definitions()['dimensions']);
        $orderNumber = $dimensions->firstWhere('name', 'customer_order.order_number');

        $this->assertNotNull($orderNumber, 'customer_order.order_number must be exposed');
        $this->assertSame('customer_order', $orderNumber->owningResource);
        $this->assertSame('order_number', $orderNumber->sqlExpression);
    }

    /** @test */
    public function snapshot_measure_cannot_declare_a_time_axis(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->expectExceptionMessage('snapshot');

        new MeasureDefinition(
            name: 'illegal_snapshot',
            owningResource: 'job',
            sqlExpression: 'COUNT(id)',
            aggType: 'count',
            filterSql: null,
            description: 'illegal',
            format: 'number',
            timeDimension: 'job.created_at',
            snapshot: true,
        );
    }

    /** @test */
    public function overdue_after_days_requires_relative_to_business_date_and_must_be_positive(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->expectExceptionMessage('relativeToBusinessDate');

        new MeasureDefinition(
            name: 'illegal_overdue',
            owningResource: 'invoice',
            sqlExpression: 'SUM(outstanding)',
            aggType: 'sum',
            filterSql: 'outstanding > 0',
            description: 'illegal',
            format: 'currency',
            timeDimension: 'invoice.due_date',
            overdueAfterDays: 30,
        );
    }

    /** @test */
    public function overdue_after_days_must_be_greater_than_zero(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->expectExceptionMessage('overdueAfterDays');

        new MeasureDefinition(
            name: 'illegal_overdue_zero',
            owningResource: 'invoice',
            sqlExpression: 'SUM(outstanding)',
            aggType: 'sum',
            filterSql: 'outstanding > 0',
            description: 'illegal',
            format: 'currency',
            timeDimension: 'invoice.due_date',
            relativeToBusinessDate: true,
            overdueAfterDays: 0,
        );
    }

    /** @test */
    public function validate_rejects_direct_relationships_that_are_not_dimensions(): void
    {
        $defs = BusinessDefinitionRegistry::default()->definitions();
        $targetMeasure = $defs['measures'][0]->name;
        $mutatedDimensions = $defs['dimensions'];
        $mutatedDimensions[0] = new DimensionDefinition(
            name: $mutatedDimensions[0]->name,
            owningResource: $mutatedDimensions[0]->owningResource,
            sqlExpression: $mutatedDimensions[0]->sqlExpression,
            type: $mutatedDimensions[0]->type,
            isPrimaryKey: $mutatedDimensions[0]->isPrimaryKey,
            description: $mutatedDimensions[0]->description,
            allowedValues: $mutatedDimensions[0]->allowedValues,
            directRelationships: [$targetMeasure],
        );

        $mutated = new BusinessDefinitionRegistry([
            'resources' => $defs['resources'],
            'measures' => $defs['measures'],
            'dimensions' => $mutatedDimensions,
            'joins' => $defs['joins'],
            'bucket_sets' => $defs['bucket_sets'],
            'segments' => $defs['segments'],
            'capabilities' => $defs['capabilities'],
        ]);

        $violations = $mutated->validate();
        $this->assertTrue(
            collect($violations)->contains(
                fn (string $v) => str_contains($v, 'directRelationships') && str_contains($v, $targetMeasure),
            ),
            implode('; ', $violations),
        );
    }

    /**
     * Consumption lines inherit BOTH scope columns from their parent job. Copying
     * invoice_item's shape instead would set departmentScopeMode 'none', and
     * scoping.py nulls department_id under that mode before building the
     * predicate -- a department-scoped user would read every department's
     * material consumption.
     *
     * @test
     */
    public function work_order_item_resource_carries_the_job_scope_shape_not_the_invoice_item_one(): void
    {
        $resources = collect(BusinessDefinitionRegistry::default()->definitions()['resources'])
            ->keyBy('name');

        $line = $resources->get('work_order_item');

        $this->assertNotNull($line, 'work_order_item resource is not declared');
        $this->assertSame('ai_v1_bq_work_order_item_fact', $line->projectionView);
        $this->assertSame('filter_when_present', $line->departmentScopeMode);
        $this->assertSame('entity_id', $line->scopeColumns['entity']);
        $this->assertSame('department_id', $line->scopeColumns['department']);
        $this->assertSame(
            $resources->get('job')->departmentScopeMode,
            $line->departmentScopeMode,
            'consumption lines must scope exactly as the job they belong to',
        );
    }

    /**
     * The RAG compiler covers a plan with a BFS bounded at 2 hops, so a measure
     * further than that from its filter dimensions is unreachable however well
     * it is declared.
     *
     * @test
     */
    public function consumption_is_reachable_from_a_job_in_one_hop(): void
    {
        $joins = BusinessDefinitionRegistry::default()->definitions()['joins'];

        $direct = collect($joins)->first(
            fn ($join) => $join->fromResource === 'job' && $join->toResource === 'work_order_item'
        );

        $this->assertNotNull($direct, 'no job -> work_order_item edge');
        $this->assertFalse(
            $direct->optional,
            'an optional child cannot own a measure (optional_join_unsupported)',
        );
    }

    /**
     * A capability with no required permission is readable by any caller. The
     * mapping used to fall through to [] for an unlisted resource, so adding a
     * resource and forgetting its permission published every one of its
     * measures and dimensions to everyone -- silently, because the only symptom
     * is a capability card showing more than it should.
     *
     * @test
     */
    public function every_capability_requires_at_least_one_permission(): void
    {
        // decision_pending members are placeholders that no principal can reach,
        // so an empty permission list is meaningless rather than open.
        $unguarded = collect(BusinessDefinitionRegistry::default()->definitions()['capabilities'])
            ->filter(fn ($capability) => $capability->capabilityState === 'enabled')
            ->filter(fn ($capability) => $capability->requiredPermissions === [])
            ->map(fn ($capability) => $capability->name)
            ->values()
            ->all();

        $this->assertSame([], $unguarded, 'enabled capabilities readable without any permission');
    }

    #[Test]
    public function derived_measure_components_must_be_declared_on_the_same_resource(): void
    {
        $definitions = BusinessDefinitionRegistry::default()->definitions();
        $definitions['measures'][] = MeasureDefinition::derived(
            name: 'broken_rate',
            owningResource: 'invoice',
            expressionKind: 'ratio',
            derivedFrom: ['cash_received', 'jobs_count'],
            description: 'test-only',
            format: 'percent',
            timeDimension: 'invoice.created_at',
            currencyDimension: 'invoice.currency_code',
        );
        $violations = (new BusinessDefinitionRegistry($definitions))->validate();
        $this->assertContains(
            'derived measure [broken_rate] component [jobs_count] is not owned by [invoice]',
            $violations,
        );
    }

    #[Test]
    public function segments_export_with_epoch_three(): void
    {
        $export = (new BusinessDefinitionsExporter)->export();
        $this->assertSame(3, $export['compatibility_epoch']);
        $this->assertSame('invoice.unpaid', $export['segments'][0]['name']);
        $this->assertSame('outstanding > 0', $export['segments'][0]['predicate_sql']);
        $rate = collect($export['measures'])->firstWhere('name', 'collection_rate');
        $this->assertSame('derived', $rate['agg_type']);
        $this->assertSame('', $rate['sql_expression']);
        $this->assertSame('ratio', $rate['expression_kind']);
        $this->assertSame(['cash_received', 'bill_amount'], $rate['derived_from']);
    }

    #[Test]
    public function catalog_declares_every_dimension_and_join_the_served_epoch_two_bundle_carries(): void
    {
        // Oracle: the bundle RAG has served since epoch 2, byte-identical in both repositories.
        $path = base_path('database/ai-business-definitions/bundles/472e1053ee70466cd7da1c8074956d0059ed44b3a2480fd56cd3eb13ab977e80.json');
        $served = json_decode((string) file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
        $registry = BusinessDefinitionRegistry::default();
        $declared = collect($registry->definitions()['dimensions'])->keyBy('name');
        foreach ($served['dimensions'] as $dimension) {
            $this->assertTrue($declared->has($dimension['name']), $dimension['name']);
            $this->assertSame($dimension['sql_expression'], $declared[$dimension['name']]->sqlExpression, $dimension['name']);
            $this->assertSame($dimension['owning_resource'], $declared[$dimension['name']]->owningResource, $dimension['name']);
        }
        $joins = collect($registry->definitions()['joins'])
            ->map(fn ($j) => implode('|', [$j->fromResource, $j->toResource, $j->relationship, $j->onSql, $j->optional ? '1' : '0']))
            ->all();
        foreach ($served['joins'] as $join) {
            $key = implode('|', [$join['from_resource'], $join['to_resource'], $join['relationship'], $join['on_sql'], $join['optional'] ? '1' : '0']);
            $this->assertContains($key, $joins, $key);
        }
    }

    #[Test]
    public function share_measure_declares_the_currency_dimension_of_its_base(): void
    {
        // A share is of the group's own currency, so the native-currency rule must see the
        // same dimension the base amount declares.
        $registry = BusinessDefinitionRegistry::default();
        $share = $registry->measure('invoice_share_of_amount');
        $this->assertSame('invoice.currency_code', $share->currencyDimension);
        $this->assertSame($registry->measure('bill_amount')->currencyDimension, $share->currencyDimension);
    }

    #[Test]
    public function segment_capability_carries_a_human_title(): void
    {
        $entry = collect(BusinessDefinitionRegistry::default()->definitions()['capabilities'])
            ->firstWhere('name', 'invoice.unpaid');
        $this->assertSame('Unpaid invoices', $entry->title);
    }

    #[Test]
    public function registry_refuses_a_partial_definition_set(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        new BusinessDefinitionRegistry(['measures' => []]);
    }

    #[Test]
    public function every_link_via_target_is_the_owning_resource_key_or_its_primary_key(): void
    {
        $violations = collect(BusinessDefinitionRegistry::default()->validate())
            ->filter(fn (string $v) => str_contains($v, 'linkVia'));

        $this->assertSame([], $violations->all(), implode('; ', $violations->all()));

        $dimensions = collect(BusinessDefinitionRegistry::default()->definitions()['dimensions'])->keyBy('name');
        // Oracle: links scope §3.1 — a look column's link_via names a sibling id
        // member that links (declares linkKey) or is the resource's primary key.
        $expected = [
            'invoice.invoice_number' => 'invoice.id',
            'quotation.invoice_number' => 'quotation.id',
            'payable_quotation.invoice_number' => 'payable_quotation.id',
            'job.work_number' => 'job.id',
            'customer_order.order_number' => 'customer_order.id',
            'customer.name' => 'customer.id',
            'supplier.name' => 'supplier.id',
            'job.customer_name' => 'job.customer_id',
            'invoice.customer_name' => 'invoice.customer_id',
            'customer_order.customer_name' => 'customer_order.customer_id',
            'invoice.customer_order_number' => 'invoice.customer_order_id',
            'job.customer_order_number' => 'job.customer_order_id',
            'inventory.supplier_name' => 'inventory.supplier_id',
        ];
        foreach ($expected as $look => $via) {
            $this->assertSame($via, $dimensions[$look]->linkVia, $look);
        }
    }

    #[Test]
    public function every_link_key_names_a_declared_resource(): void
    {
        $violations = collect(BusinessDefinitionRegistry::default()->validate())
            ->filter(fn (string $v) => str_contains($v, 'linkKey'));

        $this->assertSame([], $violations->all(), implode('; ', $violations->all()));

        $resourceNames = collect(BusinessDefinitionRegistry::default()->definitions()['resources'])
            ->pluck('name')->all();
        foreach (BusinessDefinitionRegistry::default()->definitions()['dimensions'] as $dimension) {
            if ($dimension->linkKey !== null) {
                $this->assertContains($dimension->linkKey, $resourceNames, $dimension->name);
            }
        }
    }

    #[Test]
    public function resources_with_a_record_route_declare_permissions_a_registered_route_and_a_label(): void
    {
        $violations = collect(BusinessDefinitionRegistry::default()->validate())
            ->filter(fn (string $v) => str_contains($v, 'recordRoute'));

        $this->assertSame([], $violations->all(), implode('; ', $violations->all()));

        $resources = collect(BusinessDefinitionRegistry::default()->definitions()['resources'])->keyBy('name');
        // Oracle: links scope §3.1 — one declaration per resource for route +
        // grant + label; ledger_entry and the *_item resources have no
        // per-record route and declare nothing.
        $expected = [
            'invoice' => ['bill.show', 'bill', ['view invoice'], 'Invoice'],
            'job' => ['work-order.show', 'work_order', ['view job'], 'Job'],
            'customer' => ['customer.show', 'customer', ['view customer'], 'Customer'],
        ];
        foreach ($expected as $name => [$route, $param, $permissions, $label]) {
            $binding = $resources[$name];
            $this->assertSame($route, $binding->recordRoute, $name);
            $this->assertSame($param, $binding->recordRouteParam, $name);
            $this->assertSame($permissions, $binding->linkPermissions, $name);
            $this->assertSame('any', $binding->linkPermissionMode, $name);
            $this->assertSame($label, $binding->label, $name);
        }
        foreach (['quotation', 'payable_quotation', 'customer_order', 'supplier', 'inventory', 'ledger_entry', 'invoice_item', 'quotation_item', 'payable_quotation_item', 'work_order_item', 'job_print'] as $routeless) {
            $binding = $resources[$routeless];
            $this->assertNull($binding->recordRoute, $routeless);
            $this->assertSame([], $binding->linkPermissions, $routeless);
            $this->assertNull($binding->label, $routeless);
        }
    }

    #[Test]
    public function validate_rejects_a_link_via_that_is_not_a_linking_id_of_the_same_resource(): void
    {
        $defs = BusinessDefinitionRegistry::default()->definitions();
        $dimensions = $defs['dimensions'];
        $index = collect($dimensions)->search(fn ($d) => $d->name === 'invoice.invoice_number');
        $dimensions[$index] = new DimensionDefinition(
            name: 'invoice.invoice_number',
            owningResource: 'invoice',
            sqlExpression: 'invoice_number',
            type: 'string',
            isPrimaryKey: false,
            description: 'deliberate link_via to a member outside its resource',
            linkVia: 'customer.name',
        );

        $violations = (new BusinessDefinitionRegistry([
            'resources' => $defs['resources'],
            'measures' => $defs['measures'],
            'dimensions' => $dimensions,
            'joins' => $defs['joins'],
            'bucket_sets' => $defs['bucket_sets'],
            'segments' => $defs['segments'],
            'capabilities' => $defs['capabilities'],
        ]))->validate();

        $this->assertTrue(
            collect($violations)->contains(fn (string $v) => str_contains($v, 'linkVia')),
            implode('; ', $violations),
        );
    }

    #[Test]
    public function validate_rejects_a_link_key_that_names_an_undeclared_resource(): void
    {
        $defs = BusinessDefinitionRegistry::default()->definitions();
        $dimensions = $defs['dimensions'];
        $index = collect($dimensions)->search(fn ($d) => $d->name === 'invoice.customer_order_id');
        $dimensions[$index] = new DimensionDefinition(
            name: 'invoice.customer_order_id',
            owningResource: 'invoice',
            sqlExpression: 'customer_order_id',
            type: 'number',
            isPrimaryKey: false,
            description: 'deliberate linkKey to an undeclared resource',
            format: 'id',
            linkKey: 'order',
        );

        $violations = (new BusinessDefinitionRegistry([
            'resources' => $defs['resources'],
            'measures' => $defs['measures'],
            'dimensions' => $dimensions,
            'joins' => $defs['joins'],
            'bucket_sets' => $defs['bucket_sets'],
            'segments' => $defs['segments'],
            'capabilities' => $defs['capabilities'],
        ]))->validate();

        $this->assertTrue(
            collect($violations)->contains(fn (string $v) => str_contains($v, 'linkKey [order] is not a declared resource')),
            implode('; ', $violations),
        );
    }

    #[Test]
    public function validate_rejects_a_record_route_that_is_not_a_registered_route(): void
    {
        $defs = BusinessDefinitionRegistry::default()->definitions();
        $resources = $defs['resources'];
        $index = collect($resources)->search(fn ($r) => $r->name === 'customer');
        $resources[$index] = new ResourceBinding(
            name: 'customer',
            projectionView: 'ai_v1_bq_customer_fact',
            scopeColumns: ['entity' => 'entity_id', 'department' => null],
            departmentScopeMode: 'none',
            primaryKey: 'id',
            recordPredicates: [],
            recordRoute: 'customer.missing',
            recordRouteParam: 'customer',
            linkPermissions: ['view customer'],
            label: 'Customer',
        );

        $violations = (new BusinessDefinitionRegistry([
            'resources' => $resources,
            'measures' => $defs['measures'],
            'dimensions' => $defs['dimensions'],
            'joins' => $defs['joins'],
            'bucket_sets' => $defs['bucket_sets'],
            'segments' => $defs['segments'],
            'capabilities' => $defs['capabilities'],
        ]))->validate();

        $this->assertTrue(
            collect($violations)->contains(fn (string $v) => str_contains($v, 'recordRoute [customer.missing] is not a registered route')),
            implode('; ', $violations),
        );
    }

    #[Test]
    public function validate_rejects_a_record_route_without_link_permissions(): void
    {
        $defs = BusinessDefinitionRegistry::default()->definitions();
        $resources = $defs['resources'];
        $index = collect($resources)->search(fn ($r) => $r->name === 'customer');
        $resources[$index] = new ResourceBinding(
            name: 'customer',
            projectionView: 'ai_v1_bq_customer_fact',
            scopeColumns: ['entity' => 'entity_id', 'department' => null],
            departmentScopeMode: 'none',
            primaryKey: 'id',
            recordPredicates: [],
            recordRoute: 'customer.show',
            recordRouteParam: 'customer',
            linkPermissions: [],
            label: 'Customer',
        );

        $violations = (new BusinessDefinitionRegistry([
            'resources' => $resources,
            'measures' => $defs['measures'],
            'dimensions' => $defs['dimensions'],
            'joins' => $defs['joins'],
            'bucket_sets' => $defs['bucket_sets'],
            'segments' => $defs['segments'],
            'capabilities' => $defs['capabilities'],
        ]))->validate();

        $this->assertTrue(
            collect($violations)->contains(fn (string $v) => str_contains($v, 'declares a recordRoute without linkPermissions')),
            implode('; ', $violations),
        );
    }

    #[Test]
    public function export_carries_record_route_link_permissions_mode_and_label_additively(): void
    {
        $payload = (new BusinessDefinitionsExporter)->export();
        $resources = collect($payload['resources'])->keyBy('name');

        $invoice = $resources['invoice'];
        $this->assertSame('/bills/show/{id}', $invoice['record_route']);
        $this->assertSame(['view invoice'], $invoice['link_permissions']);
        $this->assertSame('any', $invoice['link_permission_mode']);
        $this->assertSame('Invoice', $invoice['label']);
        $this->assertSame('/jobs/show/{id}', $resources['job']['record_route']);
        $this->assertSame('/customer/show/{id}', $resources['customer']['record_route']);

        foreach (['quotation', 'payable_quotation', 'customer_order', 'supplier', 'inventory', 'ledger_entry'] as $name) {
            $routeless = $resources[$name];
            $this->assertArrayNotHasKey('record_route', $routeless);
            $this->assertArrayNotHasKey('link_permissions', $routeless);
            $this->assertArrayNotHasKey('link_permission_mode', $routeless);
            $this->assertArrayNotHasKey('label', $routeless);
        }
    }

    #[Test]
    public function export_carries_link_via_on_declared_look_columns(): void
    {
        $payload = (new BusinessDefinitionsExporter)->export();
        $dimensions = collect($payload['dimensions'])->keyBy('name');

        $this->assertSame('invoice.id', $dimensions['invoice.invoice_number']['link_via']);
        $this->assertSame('quotation.id', $dimensions['quotation.invoice_number']['link_via']);
        $this->assertSame('payable_quotation.id', $dimensions['payable_quotation.invoice_number']['link_via']);
        $this->assertSame('job.id', $dimensions['job.work_number']['link_via']);
        $this->assertSame('customer_order.id', $dimensions['customer_order.order_number']['link_via']);
        $this->assertSame('customer.id', $dimensions['customer.name']['link_via']);
        $this->assertSame('supplier.id', $dimensions['supplier.name']['link_via']);
        $this->assertSame('job.customer_id', $dimensions['job.customer_name']['link_via']);
        $this->assertSame('invoice.customer_id', $dimensions['invoice.customer_name']['link_via']);
        $this->assertSame('customer_order.customer_id', $dimensions['customer_order.customer_name']['link_via']);
        $this->assertSame('invoice.customer_order_id', $dimensions['invoice.customer_order_number']['link_via']);
        $this->assertSame('job.customer_order_id', $dimensions['job.customer_order_number']['link_via']);
        $this->assertSame('inventory.supplier_id', $dimensions['inventory.supplier_name']['link_via']);

        $plain = $dimensions['invoice.status'];
        $this->assertArrayNotHasKey('link_via', $plain);
    }

    #[Test]
    public function catalog_route_and_grant_declarations_match_the_search_and_record_registries(): void
    {
        // Oracle: GlobalSearchService::registerDefaults()' urlBuilder output for a
        // model with id 1, and BusinessRecordRegistry::defaults()'
        // effectiveLinkPermissions() — the catalog pins both older half-registries
        // until they are retired.
        $registry = BusinessDefinitionRegistry::default();
        $resources = collect($registry->definitions()['resources'])->keyBy('name');
        $recordRegistry = new BusinessRecordRegistry;

        $expectedUrls = [
            'invoice' => '/bills/show/1',
            'job' => '/jobs/show/1',
            'customer' => '/customer/show/1',
        ];
        foreach ($expectedUrls as $name => $url) {
            $binding = $resources[$name];
            $this->assertNotNull($binding->recordRoute, $name);
            $this->assertSame(
                $url,
                str_replace('{id}', '1', $this->renderRecordRoute($binding)),
                $name,
            );
            $expectedPermissions = $name === 'invoice'
                ? ['view invoice']
                : $recordRegistry->get($name)->effectiveLinkPermissions();
            $this->assertSame(
                $expectedPermissions,
                $binding->linkPermissions,
                "{$name} linkPermissions must equal expected grant",
            );
            $this->assertSame(
                $recordRegistry->get($name)->effectiveLinkPermissionMode(),
                $binding->linkPermissionMode,
                "{$name} linkPermissionMode must equal the record registry's effective mode",
            );
        }
    }

    private function renderRecordRoute(ResourceBinding $binding): string
    {
        $exporter = new BusinessDefinitionsExporter;
        $method = new ReflectionMethod($exporter, 'serializeResource');
        $method->setAccessible(true);

        return $method->invoke($exporter, $binding)['record_route'];
    }
}
