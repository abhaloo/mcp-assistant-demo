<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Spatie\Permission\Models\Permission;
use Tests\Feature\Ai\Concerns\RendersAppLayout;
use Tests\TestCase;

final class AskAiStartersTest extends TestCase
{
    use RefreshDatabase;
    use RendersAppLayout;

    private User $invoiceUser;

    private User $plainUser;

    protected function setUp(): void
    {
        parent::setUp();

        config(['app.key' => 'base64:uS6Jrcd+EGVrTqlJQDAQgOnA9/akdl5FUdDgnY7sJ8c=']);

        Entity::factory()->create(['id' => 1]);

        Permission::firstOrCreate(['name' => 'view invoice', 'guard_name' => 'web'], ['group' => 'bill']);
        Permission::firstOrCreate(['name' => 'view job', 'guard_name' => 'web'], ['group' => 'job']);

        $this->invoiceUser = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->invoiceUser->givePermissionTo('view invoice');

        $this->plainUser = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
    }

    public function test_user_holding_view_invoice_gets_invoice_starters_and_no_job_starters(): void
    {
        $html = $this->actingAs($this->invoiceUser)->renderAppLayout();
        $starters = json_decode($this->panelAttribute($html, 'data-starters'), true, 512, JSON_THROW_ON_ERROR);

        $this->assertContains('Compare invoiced revenue in Q1 2026 vs Q4 2025', $starters);
        $this->assertContains('Which customers owe us the most right now?', $starters);
        $this->assertNotContains('How many finished jobs have no invoice?', $starters);
        $this->assertCount(3, $starters);
    }

    public function test_user_holding_no_permissions_gets_default_starters(): void
    {
        $html = $this->actingAs($this->plainUser)->renderAppLayout();
        $starters = json_decode($this->panelAttribute($html, 'data-starters'), true, 512, JSON_THROW_ON_ERROR);

        $this->assertSame([
            'What are your business hours?',
            'How do I create a new quotation?',
            'Explain our offset printing process',
        ], $starters);
    }
}
