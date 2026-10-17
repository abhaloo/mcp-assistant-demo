<?php

declare(strict_types=1);

namespace Tests\Feature\AskAi\DetailAttributes;

use App\Models\AiAttributeFamily;
use App\Models\AiDefinitionRevision;
use App\Models\AiJobDetailValue;
use App\Models\Department;
use App\Models\Entity;
use App\Models\WorkOrder;
use App\Services\AskAi\DetailAttributes\DefinitionWriter;
use App\Services\AskAi\DetailAttributes\TypedValueWriter;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

final class DefinitionWriterContractTest extends TestCase
{
    use RefreshDatabase;

    /** @test */
    public function customer_and_customer_order_families_register_without_exposing_orm_columns(): void
    {
        $writer = new DefinitionWriter;

        $customerFamily = $writer->findOrCreateFamily('customer.account_tier', [
            'label' => 'Account tier',
            'resource_type' => 'customer',
            'value_kind' => 'enum',
            'entity_id' => 1,
        ]);
        $customerRevision = $writer->registerRevision($customerFamily, 'customer.account_tier', 1, [
            'resource_type' => 'customer',
            'data_type' => 'enum',
            'value_kind' => 'enum',
            'aliases' => ['tier', 'customer level'],
            'options' => ['premium' => 'Premium'],
        ]);

        $orderFamily = $writer->findOrCreateFamily('customer_order.channel', [
            'label' => 'Order channel',
            'resource_type' => 'customer_order',
            'value_kind' => 'enum',
            'entity_id' => 1,
        ]);
        $orderRevision = $writer->registerRevision($orderFamily, 'customer_order.channel', 1, [
            'resource_type' => 'customer_order',
            'data_type' => 'enum',
            'value_kind' => 'enum',
            'options' => ['web' => 'Web'],
        ]);

        $this->assertInstanceOf(AiAttributeFamily::class, $customerFamily);
        $this->assertInstanceOf(AiDefinitionRevision::class, $customerRevision);
        $this->assertInstanceOf(AiAttributeFamily::class, $orderFamily);
        $this->assertInstanceOf(AiDefinitionRevision::class, $orderRevision);
        $this->assertSame('customer', $customerFamily->resource_type);
        $this->assertSame('customer', $customerRevision->resource_type);
        $this->assertSame('customer_order', $orderFamily->resource_type);
        $this->assertSame('customer_order', $orderRevision->resource_type);
        $this->assertSame(['tier', 'customer level'], $customerRevision->aliases);
        $this->assertSame(['premium' => 'Premium'], $customerRevision->options);

        // The registration contract carries semantic metadata only. It must not
        // turn arbitrary ORM columns or JSON paths into queryable definitions.
        $this->assertArrayNotHasKey('source_table', $customerRevision->toArray());
        $this->assertArrayNotHasKey('source_column', $customerRevision->toArray());
        $this->assertArrayNotHasKey('json_path', $customerRevision->toArray());
    }

    /** @test */
    public function tenant_owned_family_rows_can_share_a_global_immutable_revision_hash(): void
    {
        $entityOne = Entity::factory()->create();
        $entityTwo = Entity::factory()->create();
        $writer = new DefinitionWriter;

        $familyOne = $writer->findOrCreateFamily('ordered_qty', [
            'resource_type' => 'job',
            'value_kind' => 'string',
            'entity_id' => $entityOne->id,
        ]);
        $familyTwo = $writer->findOrCreateFamily('ordered_qty', [
            'resource_type' => 'job',
            'value_kind' => 'string',
            'entity_id' => $entityTwo->id,
        ]);
        $revisionOne = $writer->registerRevision($familyOne, 'ordered_qty', 1, [
            'data_type' => 'string',
            'value_kind' => 'string',
        ]);
        $revisionTwo = $writer->registerRevision($familyTwo, 'ordered_qty', 1, [
            'data_type' => 'string',
            'value_kind' => 'string',
        ]);

        $this->assertNotSame($familyOne->id, $familyTwo->id);
        $this->assertSame($entityOne->id, $familyOne->entity_id);
        $this->assertSame($entityTwo->id, $familyTwo->entity_id);
        $this->assertNotSame($revisionOne->id, $revisionTwo->id);
        $this->assertSame($revisionOne->revision_hash, $revisionTwo->revision_hash);
        $this->assertSame($entityOne->id, $revisionOne->entity_id);
        $this->assertSame($entityTwo->id, $revisionTwo->entity_id);
    }

    /** @test */
    public function job_value_keeps_the_exact_specification_revision_and_display_label(): void
    {
        $entity = Entity::factory()->create(['id' => 1]);
        $department = Department::factory()->create(['name' => 'Printing']);
        $job = WorkOrder::factory()->withDepartment($department)->create([
            'entity_id' => $entity->id,
            'specifications' => json_encode(['1 Side Ptg' => true]),
        ]);

        $writer = new DefinitionWriter;
        $family = $writer->findOrCreateFamily('print.sides', [
            'label' => 'Print sides',
            'resource_type' => 'job',
            'value_kind' => 'enum',
            'entity_id' => $entity->id,
        ]);
        $revisionOne = $writer->registerRevision($family, 'print.sides', 1, [
            'resource_type' => 'job',
            'data_type' => 'enum',
            'value_kind' => 'enum',
            'label' => '1 Side Ptg',
            'aliases' => ['one side'],
            'options' => ['one_side' => '1 Side Ptg'],
        ]);
        $revisionTwo = $writer->registerRevision($family, 'print.sides', 2, [
            'resource_type' => 'job',
            'data_type' => 'enum',
            'value_kind' => 'enum',
            'label' => 'Single-sided printing',
            'aliases' => ['single sided'],
            'options' => ['one_side' => 'Single-sided printing'],
        ]);

        $value = (new TypedValueWriter)->write(
            $job,
            $revisionOne,
            'one_side',
            source: 'legacy_work_order_specifications',
            provenance: ['source_column' => 'specifications', 'parser_version' => 'print-sides-v2'],
            rawValue: json_encode(['1 Side Ptg' => true]),
        );

        $this->assertInstanceOf(AiJobDetailValue::class, $value);
        $this->assertSame($revisionOne->id, $value->definition_revision_id);
        $this->assertSame($revisionOne->revision_hash, $value->revision_hash);
        $this->assertSame('1 Side Ptg', $value->display_value);
        $this->assertSame('legacy_work_order_specifications', $value->source);
        $this->assertSame(['source_column' => 'specifications', 'parser_version' => 'print-sides-v2'], $value->provenance);
        $this->assertSame(json_encode(['1 Side Ptg' => true]), $value->raw_value);

        // A later label/revision is a separate interpretation; it cannot
        // rewrite the value that was recorded against revision one.
        $this->assertNotSame($revisionOne->revision_hash, $revisionTwo->revision_hash);
        $this->assertSame(1, AiJobDetailValue::where('work_order_id', $job->id)->count());
        $this->assertSame($revisionOne->id, $value->fresh()->definition_revision_id);
    }
}
