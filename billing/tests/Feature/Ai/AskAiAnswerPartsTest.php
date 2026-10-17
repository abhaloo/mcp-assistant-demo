<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Tests\Feature\Ai\Concerns\RendersAppLayout;
use Tests\TestCase;

final class AskAiAnswerPartsTest extends TestCase
{
    use RefreshDatabase;
    use RendersAppLayout;

    protected User $user;

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'authz-test-secret',
        ]);
    }

    public function test_answer_parts_config_defines_record_refs(): void
    {
        $parts = config('ask_ai.answer_parts');

        $this->assertIsArray($parts);
        $this->assertArrayHasKey('record_refs', $parts);
        $this->assertIsBool($parts['record_refs']);
    }

    public function test_panel_renders_record_refs_on_by_default(): void
    {
        $html = $this->renderAppLayout();

        $this->assertSame('1', $this->panelAttribute($html, 'data-record-refs'));
    }

    public function test_panel_reflects_disabled_record_refs(): void
    {
        config(['ask_ai.answer_parts.record_refs' => false]);

        $html = $this->renderAppLayout();

        $this->assertSame('0', $this->panelAttribute($html, 'data-record-refs'));
    }

    public function test_answer_parts_config_defines_collapsible_sources(): void
    {
        $parts = config('ask_ai.answer_parts');

        $this->assertIsArray($parts);
        $this->assertArrayHasKey('collapsible_sources', $parts);
        $this->assertIsBool($parts['collapsible_sources']);
    }

    public function test_panel_renders_collapsible_sources_on_by_default(): void
    {
        $html = $this->renderAppLayout();

        $this->assertSame('1', $this->panelAttribute($html, 'data-collapsible-sources'));
    }

    public function test_panel_reflects_disabled_collapsible_sources(): void
    {
        config(['ask_ai.answer_parts.collapsible_sources' => false]);

        $html = $this->renderAppLayout();

        $this->assertSame('0', $this->panelAttribute($html, 'data-collapsible-sources'));
    }
}
