<?php

declare(strict_types=1);

namespace Tests\Feature\AskAi;

use App\Models\Department;
use App\Models\Entity;
use App\Models\User;
use App\Models\WorkOrder;
use App\Models\WorkOrderAttachment;
use App\Services\AskAi\ArtworkMetadataResolver;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Spatie\Permission\Models\Permission;
use Tests\TestCase;

class ArtworkMetadataScopingTest extends TestCase
{
    use RefreshDatabase;

    protected Entity $entity1;

    protected Entity $entity2;

    protected Department $deptPrinting;

    protected Department $deptAccounts;

    protected User $userPrinting;

    protected User $userOtherEntity;

    protected function setUp(): void
    {
        parent::setUp();

        $this->entity1 = Entity::factory()->create(['id' => 1]);
        $this->entity2 = Entity::factory()->create(['id' => 2]);

        $this->deptPrinting = Department::factory()->create([
            'name' => 'Printing',
        ]);
        $this->deptAccounts = Department::factory()->create([
            'name' => 'Accounts',
        ]);

        Permission::firstOrCreate(
            ['name' => 'view job', 'guard_name' => 'web'],
            ['group' => 'job']
        );

        $this->userPrinting = User::factory()->create([
            'role' => 'user',
            'entity_id' => $this->entity1->id,
            'department_id' => $this->deptPrinting->id,
        ]);
        $this->userPrinting->givePermissionTo('view job');

        $this->userOtherEntity = User::factory()->create([
            'role' => 'user',
            'entity_id' => $this->entity2->id,
            'department_id' => $this->deptPrinting->id,
        ]);
        $this->userOtherEntity->givePermissionTo('view job');
    }

    /** @test */
    public function it_returns_artwork_metadata_while_hiding_raw_filesystem_paths(): void
    {
        $job = WorkOrder::factory()->create([
            'entity_id' => $this->entity1->id,
            'department_id' => $this->deptPrinting->id,
            'user_id' => $this->userPrinting->id,
        ]);

        $attachment = WorkOrderAttachment::create([
            'work_order_id' => $job->id,
            'user_id' => $this->userPrinting->id,
            'path' => 'private/s3-internal-bucket/uploads/2026/sensitive_filename_raw.pdf',
            'description' => 'Cover artwork final proof',
            'extension' => 'pdf',
            'size' => 1048576, // 1 MB
        ]);

        $resolver = new ArtworkMetadataResolver;
        $metadata = $resolver->getArtworkMetadataForJob($job, $this->userPrinting);

        $this->assertCount(1, $metadata);
        $item = $metadata[0];

        $this->assertSame($attachment->id, $item['id']);
        $this->assertSame('pdf', $item['format']);
        $this->assertSame(1048576, $item['size_bytes']);
        $this->assertSame('Cover artwork final proof', $item['description']);
        $this->assertSame('active', $item['status']);
        $this->assertNotNull($item['created_at']);

        // SENSITIVITY CHECK: Raw internal path must NEVER be present
        $this->assertArrayNotHasKey('path', $item);
        $encoded = json_encode($item);
        $this->assertStringNotContainsString('private/s3-internal-bucket', (string) $encoded);
    }

    /** @test */
    public function it_denies_artwork_metadata_for_unauthorized_cross_entity_user(): void
    {
        $job = WorkOrder::factory()->create([
            'entity_id' => $this->entity1->id,
            'department_id' => $this->deptPrinting->id,
            'user_id' => $this->userPrinting->id,
        ]);

        WorkOrderAttachment::create([
            'work_order_id' => $job->id,
            'user_id' => $this->userPrinting->id,
            'path' => 'uploads/test.png',
            'description' => 'Test PNG',
            'extension' => 'png',
            'size' => 50000,
        ]);

        $resolver = new ArtworkMetadataResolver;
        $metadata = $resolver->getArtworkMetadataForJob($job, $this->userOtherEntity);

        $this->assertEmpty($metadata);
    }
}
