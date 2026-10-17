<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Tests\Feature\Ai\Concerns\RendersAppLayout;
use Tests\TestCase;

final class AskV2ModuleFlagsTest extends TestCase
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

    public function test_v2_modules_config_defines_interaction_card(): void
    {
        $modules = config('ask_ai.v2_modules');

        $this->assertIsArray($modules);
        $this->assertArrayHasKey('interaction_card', $modules);
        $this->assertIsBool($modules['interaction_card']);
    }

    public function test_panel_advertises_the_interaction_card_module(): void
    {
        config([
            'services.rag.ask_v2_enabled' => true,
            'ask_ai.ask_v2_enabled' => true,
            'ask_ai.v2_modules.interaction_card' => true,
        ]);

        $html = $this->renderAppLayout();

        $modules = json_decode($this->panelAttribute($html, 'data-v2-modules'), true, flags: JSON_THROW_ON_ERROR);
        $this->assertIsArray($modules);
        $this->assertArrayHasKey('interaction_card', $modules);
        $this->assertTrue($modules['interaction_card']);
    }

    public function test_panel_reflects_disabled_interaction_card_module(): void
    {
        config([
            'services.rag.ask_v2_enabled' => true,
            'ask_ai.ask_v2_enabled' => true,
            'ask_ai.v2_modules.interaction_card' => false,
        ]);

        $html = $this->renderAppLayout();

        $modules = json_decode($this->panelAttribute($html, 'data-v2-modules'), true, flags: JSON_THROW_ON_ERROR);
        $this->assertIsArray($modules);
        $this->assertArrayHasKey('interaction_card', $modules);
        $this->assertFalse($modules['interaction_card']);
    }

    public function test_all_standard_v2_modules_are_present_in_config(): void
    {
        $modules = config('ask_ai.v2_modules');

        $this->assertIsArray($modules);
        $this->assertArrayHasKey('activity_spine', $modules);
        $this->assertArrayHasKey('result_table', $modules);
        $this->assertArrayHasKey('interaction_card', $modules);
    }
}
