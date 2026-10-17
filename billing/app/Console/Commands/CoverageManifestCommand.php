<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Support\Coverage\FeatureCoverageMap;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\File;

final class CoverageManifestCommand extends Command
{
    protected $signature = 'coverage:manifest
                            {--check             : Exit 1 if any cell has status GAP}
                            {--fail-on-ungated   : Exit 1 if any cell has status UNGATED}
                            {--proof-path=       : Path to a single proven-cells.json (default: merge storage/coverage/proven-cells*.json)}
                            {--json-path=        : Path for output feature-coverage.json (default: storage/coverage/feature-coverage.json)}
                            {--md-path=          : Path for output feature-coverage.md (default: docs/coverage/feature-coverage.md)}';

    protected $description = 'Build the feature-coverage manifest (JSON + Markdown) from FeatureCoverageMap';

    private const STATUS_UNGATED = 'UNGATED';

    private const STATUS_COVERED = 'enforced+covered';

    private const STATUS_GAP = 'GAP';

    public function handle(): int
    {
        $cells = $this->buildCells();
        $summary = $this->summarise($cells);

        $this->writeJson($cells, $summary);
        $this->writeMarkdown($cells);

        $this->line(
            "{$summary['enforced_covered']} enforced+covered, "
            ."{$summary['ungated']} ungated, "
            ."{$summary['gap']} gaps"
        );

        $exitCode = 0;

        if ($this->option('check') && $summary['gap'] > 0) {
            $exitCode = 1;
        }

        if ($this->option('fail-on-ungated') && $summary['ungated'] > 0) {
            $exitCode = 1;
        }

        return $exitCode;
    }

    /** @return list<array{feature:string,action:string,permission:string,gate:string,denial:string,status:string}> */
    private function buildCells(): array
    {
        $provenKeys = $this->provenKeys();
        $result = [];

        foreach (FeatureCoverageMap::all() as $cell) {
            $result[] = array_merge($cell, ['status' => $this->resolveStatus($cell, $provenKeys)]);
        }

        return $result;
    }

    /**
     * The exact set of cell keys proven by a passing denial test in the most recent
     * matrix run. A cell is certified only if its own key is recorded — not merely
     * because some test class exists for its feature.
     *
     * Default path merges sequential proven-cells.json with parallel worker shards
     * (proven-cells_test_{N}.json) written by CoverageProof under --parallel.
     *
     * @return list<string>
     */
    private function provenKeys(): array
    {
        if ($this->option('proof-path')) {
            return $this->keysFromProofFile($this->option('proof-path'));
        }

        $dir = storage_path('coverage');
        if (! File::isDirectory($dir)) {
            return [];
        }

        $paths = File::glob($dir.DIRECTORY_SEPARATOR.'proven-cells*.json') ?: [];
        $keys = [];

        foreach ($paths as $path) {
            foreach ($this->keysFromProofFile($path) as $key) {
                $keys[$key] = true;
            }
        }

        return array_keys($keys);
    }

    /**
     * @return list<string>
     */
    private function keysFromProofFile(string $path): array
    {
        if (! File::exists($path)) {
            return [];
        }

        $decoded = json_decode(File::get($path), true);
        if (! is_array($decoded)) {
            return [];
        }

        $keys = [];
        foreach ($decoded as $key) {
            if (is_string($key) && $key !== '') {
                $keys[] = $key;
            }
        }

        return $keys;
    }

    /**
     * @param  array{feature:string,action:string,permission:string,gate:string,denial:string}  $cell
     * @param  list<string>  $provenKeys
     */
    private function resolveStatus(array $cell, array $provenKeys): string
    {
        if ($cell['gate'] === 'none') {
            return self::STATUS_UNGATED;
        }

        if (in_array(FeatureCoverageMap::cellKey($cell), $provenKeys, true)) {
            return self::STATUS_COVERED;
        }

        return self::STATUS_GAP;
    }

    /** @param list<array<string,string>> $cells */
    private function summarise(array $cells): array
    {
        $counts = [
            'enforced_covered' => 0,
            'ungated' => 0,
            'gap' => 0,
            'total' => count($cells),
        ];

        foreach ($cells as $cell) {
            match ($cell['status']) {
                self::STATUS_COVERED => $counts['enforced_covered']++,
                self::STATUS_UNGATED => $counts['ungated']++,
                self::STATUS_GAP => $counts['gap']++,
            };
        }

        return $counts;
    }

    /** @param list<array<string,string>> $cells */
    private function writeJson(array $cells, array $summary): void
    {
        $path = $this->option('json-path') ?: storage_path('coverage/feature-coverage.json');
        File::ensureDirectoryExists(dirname($path));

        $payload = [
            'summary' => $summary,
            'cells' => $cells,
        ];

        File::put(
            $path,
            json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)."\n"
        );
    }

    /** @param list<array<string,string>> $cells */
    private function writeMarkdown(array $cells): void
    {
        $mdPath = $this->option('md-path') ?: base_path('docs/coverage/feature-coverage.md');
        File::ensureDirectoryExists(dirname($mdPath));

        $lines = [];
        $lines[] = '# Feature Coverage';
        $lines[] = '';
        $lines[] = '## Status legend';
        $lines[] = '';
        $lines[] = '| Status | Meaning |';
        $lines[] = '|--------|---------|';
        $lines[] = '| `enforced+covered` | Gate is present AND this cell was proven by a passing denial test (recorded in `storage/coverage/proven-cells.json`) |';
        $lines[] = '| `UNGATED` | No gate (`gate: none`) — permission is not enforced at all |';
        $lines[] = '| `GAP` | Gate present but this cell has no recorded execution proof |';
        $lines[] = '';
        $lines[] = '## Coverage by feature';
        $lines[] = '';
        $lines[] = '| Feature | Action | Permission | Gate | Status |';
        $lines[] = '|---------|--------|------------|------|--------|';

        // Group by feature, preserving original ordering within each feature.
        /** @var array<string,list<array<string,string>>> $byFeature */
        $byFeature = [];

        foreach ($cells as $cell) {
            $byFeature[$cell['feature']][] = $cell;
        }

        ksort($byFeature);

        foreach ($byFeature as $rows) {
            foreach ($rows as $row) {
                $lines[] = sprintf(
                    '| %s | %s | %s | %s | `%s` |',
                    $row['feature'],
                    $row['action'],
                    $row['permission'],
                    $row['gate'],
                    $row['status']
                );
            }
        }

        $ungated = array_values(array_filter($cells, fn (array $c): bool => $c['status'] === self::STATUS_UNGATED));

        $lines[] = '';
        $lines[] = '## Security gaps (UNGATED)';
        $lines[] = '';

        if ($ungated === []) {
            $lines[] = '_None — all features have an enforced gate._';
        } else {
            $lines[] = 'These features have **no enforced permission gate** and must be addressed:';
            $lines[] = '';

            foreach ($ungated as $cell) {
                $lines[] = sprintf(
                    '- **%s / %s** — permission `%s` is declared but never gated',
                    $cell['feature'],
                    $cell['action'],
                    $cell['permission']
                );
            }
        }

        $lines[] = '';

        File::put($mdPath, implode("\n", $lines));
    }
}
