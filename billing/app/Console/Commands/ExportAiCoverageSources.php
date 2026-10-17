<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Support\Coverage\BladeFieldScanner;
use App\Support\Coverage\CoverageExportSeed;
use App\Support\Coverage\ModelRelationReflector;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\File;
use Illuminate\Support\Facades\Route;
use Symfony\Component\Process\Exception\ProcessFailedException;
use Symfony\Component\Process\Process;

final class ExportAiCoverageSources extends Command
{
    protected $signature = 'ai:coverage-export
                            {--out=       : Output path (default storage/coverage/ai-coverage-export.json)}
                            {--views-root= : Root that seeded blade paths are relative to (default base_path())}';

    protected $description = 'Export routes, models, blade field references and page-context profiles for the coverage-map generator';

    private const RELATION_DEPTH = 3;

    public function handle(BladeFieldScanner $scanner, ModelRelationReflector $reflector): int
    {
        $root = rtrim((string) ($this->option('views-root') ?: base_path()), '/\\');
        $routes = [];
        $blades = [];
        $modelQueue = [];
        foreach (CoverageExportSeed::routes() as $seed) {
            $route = Route::getRoutes()->getByName($seed['name']);
            if ($route === null) {
                $this->error("route not registered: {$seed['name']}");

                return self::FAILURE;
            }
            if (! class_exists($seed['root_model'])) {
                $this->error("root model not found: {$seed['root_model']}");

                return self::FAILURE;
            }
            foreach ($seed['blades'] as $relative) {
                $path = $root.DIRECTORY_SEPARATOR.$relative;
                if (! is_readable($path)) {
                    $this->error("blade not readable: {$relative}");

                    return self::FAILURE;
                }
                $blades[] = [
                    'file' => $relative,
                    'root_variable' => $seed['root_variable'],
                    'root_model' => $seed['root_model'],
                    'view_variables' => $seed['view_variables'] ?? [],
                ] + $scanner->scan((string) file_get_contents($path));
            }
            $routes[] = [
                'name' => $seed['name'],
                'uri' => $route->uri(),
                'controller' => $route->getActionName(),
                'middleware' => array_values($route->gatherMiddleware()),
                'root_variable' => $seed['root_variable'],
                'root_model' => $seed['root_model'],
                'view_variables' => $seed['view_variables'] ?? [],
                'blades' => $seed['blades'],
            ];
            $modelQueue[] = $seed['root_model'];
            foreach ($seed['view_variables'] ?? [] as $varModel) {
                $modelQueue[] = $varModel;
            }
        }

        $models = [];
        $depth = 0;
        while ($modelQueue !== [] && $depth <= self::RELATION_DEPTH) {
            $next = [];
            foreach (array_unique($modelQueue) as $class) {
                if (isset($models[$class]) || ! class_exists($class)) {
                    continue;
                }
                $models[$class] = $reflector->describe($class);
                foreach ($models[$class]['relations'] as $relation) {
                    if ($relation['status'] === 'ok') {
                        $next[] = $relation['related_model'];
                    }
                }
            }
            $modelQueue = $next;
            $depth++;
        }

        $payload = [
            'schema_version' => 1,
            'billing_commit' => $this->billingCommit(),
            'generated_utc' => gmdate('Y-m-d\TH:i:s\Z'),
            'routes' => $routes,
            'blades' => $blades,
            'models' => array_values($models),
            'page_context_profiles' => CoverageExportSeed::pageContextProfiles(),
        ];
        $out = (string) ($this->option('out') ?: storage_path('coverage/ai-coverage-export.json'));
        File::ensureDirectoryExists(dirname($out));
        File::put($out, json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR)."\n");
        $this->info("wrote {$out}: ".count($routes).' routes, '.count($blades).' blades, '.count($models).' models');

        return self::SUCCESS;
    }

    private function billingCommit(): string
    {
        try {
            $sha = trim((string) Process::fromShellCommandline('git rev-parse --short HEAD', base_path())->mustRun()->getOutput());
        } catch (ProcessFailedException) {
            return 'unknown';
        }

        return $sha === '' ? 'unknown' : $sha;
    }
}
