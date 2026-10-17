<?php

declare(strict_types=1);

namespace Tests\Feature\AskAi\DetailAttributes;

use App\Models\AiJobDetailValue;
use App\Models\Department;
use App\Models\Entity;
use App\Models\User;
use App\Models\WorkOrder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

class JobPrintSidesBackfillTest extends TestCase
{
    use RefreshDatabase;

    protected Entity $entity;

    protected Department $department;

    protected User $user;

    protected function setUp(): void
    {
        parent::setUp();

        $this->entity = Entity::factory()->create(['id' => 1]);
        $this->department = Department::factory()->create([
            'name' => 'Printing',
        ]);
        $this->user = User::factory()->create([
            'role' => 'admin',
            'entity_id' => $this->entity->id,
        ]);
    }

    /** @test */
    public function it_migrates_approved_literal_shapes_and_preserves_raw_json(): void
    {
        $job1 = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode(['Printing Sides' => '1 Side Ptg', 'Paper' => 'Art Paper 150gsm']),
        ]);

        $job2 = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode(['Printing Sides' => '1 side']),
        ]);

        $job3 = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode(['Printing Sides' => 'one side']),
        ]);

        $this->artisan('ai:backfill-detail-attributes', ['--family' => 'print.sides'])
            ->assertExitCode(0);

        $this->assertDatabaseHas('ai_job_detail_values', [
            'work_order_id' => $job1->id,
            'value_text' => 'one_side',
            'source' => 'legacy_work_order_specifications',
        ]);

        $attr1 = AiJobDetailValue::where('work_order_id', $job1->id)
            ->first();
        $this->assertNotNull($attr1);
        $this->assertSame('one_side', $attr1->value_text);
        $this->assertStringContainsString('Art Paper 150gsm', (string) $attr1->raw_value);

        $this->assertDatabaseHas('ai_job_detail_values', [
            'work_order_id' => $job2->id,
            'value_text' => 'one_side',
        ]);

        $this->assertDatabaseHas('ai_job_detail_values', [
            'work_order_id' => $job3->id,
            'value_text' => 'one_side',
        ]);

        $this->assertDatabaseHas('ai_detail_migration_ledger', [
            'resource_type' => 'job',
            'resource_id' => $job1->id,
            'attribute_key' => 'print.sides',
            'status' => 'applied',
        ]);
    }

    /** @test */
    public function it_migrates_the_production_top_level_one_side_ptg_boolean_shape(): void
    {
        $job = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode([
                '1 Side Ptg' => true,
                'Type of Printing' => ['Digital' => true],
            ]),
        ]);
        $twoSidedJob = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode(['2 Side Duplex Ptg' => true]),
        ]);

        $this->artisan('ai:backfill-detail-attributes', ['--family' => 'print.sides'])
            ->assertExitCode(0);

        $this->assertDatabaseHas('ai_job_detail_values', [
            'work_order_id' => $job->id,
            'value_text' => 'one_side',
            'raw_value' => json_encode([
                '1 Side Ptg' => true,
                'Type of Printing' => ['Digital' => true],
            ]),
        ]);
        $this->assertDatabaseHas('ai_job_detail_values', [
            'work_order_id' => $twoSidedJob->id,
            'value_text' => 'two_side',
        ]);
    }

    /** @test */
    public function it_does_not_confuse_one_sided_lamination_with_one_sided_printing(): void
    {
        $job = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode([
                'Lamination' => ['1 Side' => true],
            ]),
        ]);

        $this->artisan('ai:backfill-detail-attributes', ['--family' => 'print.sides'])
            ->assertExitCode(0);

        $this->assertDatabaseMissing('ai_job_detail_values', [
            'work_order_id' => $job->id,
        ]);
        $this->assertDatabaseHas('ai_detail_migration_ledger', [
            'resource_type' => 'job',
            'resource_id' => $job->id,
            'attribute_key' => 'print.sides',
            'status' => 'conflict',
            'reason' => 'unapproved_or_ambiguous_shape',
        ]);
    }

    /** @test */
    public function it_rejects_conflicting_top_level_print_side_flags(): void
    {
        $job = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode([
                '1 Side Ptg' => true,
                '2 Side Duplex Ptg' => true,
            ]),
        ]);

        $this->artisan('ai:backfill-detail-attributes', ['--family' => 'print.sides'])
            ->assertExitCode(0);

        $this->assertDatabaseMissing('ai_job_detail_values', [
            'work_order_id' => $job->id,
        ]);
        $this->assertDatabaseHas('ai_detail_migration_ledger', [
            'resource_type' => 'job',
            'resource_id' => $job->id,
            'attribute_key' => 'print.sides',
            'status' => 'conflict',
            'reason' => 'unapproved_or_ambiguous_shape',
        ]);
    }

    /** @test */
    public function it_detects_stale_fingerprint_and_skips_write_with_conflict_log(): void
    {
        $job = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode(['Printing Sides' => '1 Side Ptg']),
        ]);

        // Pre-populate an existing typed value with an old fingerprint

        // When a stale condition is detected during backfill (e.g. source row updated timestamp is in the future or expected fingerprint mismatch)
        $this->artisan('ai:backfill-detail-attributes', [
            '--family' => 'print.sides',
            '--expected-fingerprint' => 'mismatched-fingerprint',
            '--job-id' => $job->id,
        ])->assertExitCode(0);

        $this->assertDatabaseHas('ai_detail_migration_ledger', [
            'resource_type' => 'job',
            'resource_id' => $job->id,
            'attribute_key' => 'print.sides',
            'status' => 'conflict',
        ]);
    }

    /** @test */
    public function unapproved_literal_shapes_are_logged_as_conflicts_and_not_migrated(): void
    {
        $job = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
            'specifications' => json_encode(['Printing Sides' => 'ambiguous three side custom fold']),
        ]);

        $this->artisan('ai:backfill-detail-attributes', ['--family' => 'print.sides'])
            ->assertExitCode(0);

        $this->assertDatabaseMissing('ai_job_detail_values', [
            'work_order_id' => $job->id,
        ]);

        $this->assertDatabaseHas('ai_detail_migration_ledger', [
            'resource_type' => 'job',
            'resource_id' => $job->id,
            'attribute_key' => 'print.sides',
            'status' => 'conflict',
        ]);
    }
}
