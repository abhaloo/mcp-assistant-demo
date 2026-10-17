<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Authorization\PolicyManifestExporter;
use Illuminate\Console\Command;

/**
 * Task 2B / PR 2B — export the canonical AI policy-manifest artifact plus an
 * immutable, hash-addressed bundle: Billing's producer half of the shared
 * RAG contract. The RAG image mirrors the SAME shape
 * (`app/policy/manifest/index.json` + `bundles/<sha256>.json`) so a release
 * gate can compare a candidate Billing hash against RAG's accepted set
 * without either side inventing a second manifest format.
 *
 * Writes three things, reusing PolicyManifestExporter's EXISTING canonical
 * serialization verbatim (never a second canonicalization — the committed
 * `database/ai-manifest/policy-manifest.json`/`.hash` drift gate in
 * PolicyManifestExporterTest::committed_manifest_artifact_matches_a_fresh_export()
 * already pins this class's output; this command is simply what regenerates
 * those files instead of a manual/ad hoc step):
 *
 *  1. The mutable "current" pointer: `database/ai-manifest/policy-manifest.json`
 *     + `.hash` — always safe to overwrite, since it is entirely code-derived
 *     (no DB, no clock, no randomness — PolicyManifestExporter's own docblock)
 *     and simply reflects whatever the code currently computes.
 *  2. An IMMUTABLE, hash-addressed bundle: `database/ai-manifest/bundles/<hex>.json`
 *     (the hex digest ONLY — no `sha256:` prefix, since `:` is not a valid
 *     Windows filename character and this store must stay portable). Once a
 *     hash has a bundle file, that file's bytes may never legitimately
 *     change — two exports of the SAME code always produce byte-identical
 *     canonical output, so re-running this command against unchanged code is
 *     a no-op there. If a bundle file already exists under a given hash with
 *     DIFFERENT bytes, that is not a normal state to paper over with a
 *     silent overwrite — it means the hash-naming scheme's "hash uniquely
 *     determines content" assumption has been violated (a canonicalization
 *     change, a corrupted file, manual tampering). The command aborts with a
 *     non-zero exit BEFORE touching any file (bundle integrity is checked
 *     first, so a refusal leaves the whole `database/ai-manifest/` tree
 *     exactly as it was) rather than silently accepting either version.
 *  3. The mutable current/N-1 compatibility pointer:
 *     `database/ai-manifest/index.json`, retaining at most the prior valid
 *     immutable bundle so consumers can stage a bounded rollback window.
 */
final class ExportAiPolicyManifest extends Command
{
    protected $signature = 'ai-manifest:export';

    protected $description = 'Export the canonical AI policy-manifest artifact and its immutable hash-addressed bundle';

    private const ARTIFACT_RELATIVE_PATH = 'database/ai-manifest/policy-manifest.json';

    private const HASH_RELATIVE_PATH = 'database/ai-manifest/policy-manifest.hash';

    private const INDEX_RELATIVE_PATH = 'database/ai-manifest/index.json';

    private const BUNDLES_RELATIVE_DIR = 'database/ai-manifest/bundles';

    public function __construct(private readonly PolicyManifestExporter $exporter = new PolicyManifestExporter)
    {
        parent::__construct();
    }

    public function handle(): int
    {
        $canonicalJson = $this->exporter->canonicalJson();
        $hash = $this->exporter->hash();
        $hex = substr($hash, strlen('sha256:'));

        $bundlesDir = base_path(self::BUNDLES_RELATIVE_DIR);
        $bundlePath = $bundlesDir.DIRECTORY_SEPARATOR.$hex.'.json';
        $indexPath = base_path(self::INDEX_RELATIVE_PATH);

        try {
            $index = $this->buildIndex($hash, $indexPath, $bundlesDir);
        } catch (\RuntimeException $exception) {
            $this->components->error($exception->getMessage());

            return self::FAILURE;
        }

        // Bundle-integrity check FIRST, before any write — a refusal here
        // must leave every file under database/ai-manifest/ untouched.
        if (is_file($bundlePath)) {
            $existing = file_get_contents($bundlePath);

            // A false return is an I/O read failure (permissions, transient
            // disk error) — distinct from a genuine byte mismatch. Reporting
            // it as "tampering/canonicalization change" would send whoever
            // reads the error chasing the wrong cause.
            if ($existing === false) {
                $this->components->error("Could not read existing bundle [bundles/{$hex}.json] — check file permissions.");

                return self::FAILURE;
            }

            if ($existing !== $canonicalJson) {
                $this->components->error(
                    "Refusing to overwrite existing bundle [bundles/{$hex}.json] with different bytes for ".
                    'the same hash. This means the hash no longer uniquely determines this file\'s content — '.
                    'investigate before re-running (canonicalization change, corrupted file, or manual edit).'
                );

                return self::FAILURE;
            }

            $this->components->twoColumnDetail("bundles/{$hex}.json", '<fg=green>already up to date</>');
        } else {
            // Race-safe create: another process may have created the dir
            // between the is_dir() check and mkdir() — only a genuine
            // failure (still no dir afterward) is fatal.
            if (! is_dir($bundlesDir) && ! mkdir($bundlesDir, 0755, true) && ! is_dir($bundlesDir)) {
                $this->components->error("Could not create bundles directory [{$bundlesDir}].");

                return self::FAILURE;
            }

            if (file_put_contents($bundlePath, $canonicalJson) === false) {
                $this->components->error("Could not write bundle [bundles/{$hex}.json].");

                return self::FAILURE;
            }

            $this->components->twoColumnDetail("bundles/{$hex}.json", '<fg=green>written</>');
        }

        if (file_put_contents(base_path(self::ARTIFACT_RELATIVE_PATH), $canonicalJson) === false) {
            $this->components->error('Could not write '.self::ARTIFACT_RELATIVE_PATH.'.');

            return self::FAILURE;
        }

        if (file_put_contents(base_path(self::HASH_RELATIVE_PATH), $hash) === false) {
            $this->components->error('Could not write '.self::HASH_RELATIVE_PATH.'.');

            return self::FAILURE;
        }

        $indexJson = json_encode(
            $index,
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR,
        )."\n";
        if (file_put_contents($indexPath, $indexJson) === false) {
            $this->components->error('Could not write '.self::INDEX_RELATIVE_PATH.'.');

            return self::FAILURE;
        }

        $this->components->twoColumnDetail(self::ARTIFACT_RELATIVE_PATH, '<fg=green>written</>');
        $this->components->twoColumnDetail(self::HASH_RELATIVE_PATH, '<fg=green>written</>');
        $this->components->twoColumnDetail(self::INDEX_RELATIVE_PATH, '<fg=green>written</>');
        $this->components->info("Exported manifest hash: {$hash}");

        return self::SUCCESS;
    }

    /**
     * @return array{schema_version: int, current: string, accepted: list<string>}
     */
    private function buildIndex(string $hash, string $indexPath, string $bundlesDir): array
    {
        $accepted = [$hash];

        if (is_file($indexPath)) {
            $raw = file_get_contents($indexPath);
            if ($raw === false || $raw === '') {
                throw new \RuntimeException('Could not read '.self::INDEX_RELATIVE_PATH.'.');
            }

            try {
                $prior = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
            } catch (\JsonException $exception) {
                throw new \RuntimeException(
                    "Published policy manifest index is invalid JSON: {$indexPath}",
                    previous: $exception,
                );
            }

            if (! is_array($prior)
                || ($prior['schema_version'] ?? null) !== 1
                || ! is_array($prior['accepted'] ?? null)
            ) {
                throw new \RuntimeException("Published policy manifest index has an invalid shape: {$indexPath}");
            }

            foreach (array_values($prior['accepted']) as $candidate) {
                if (! is_string($candidate)
                    || ! $this->isHash($candidate)
                    || $candidate === $hash
                    || in_array($candidate, $accepted, true)
                ) {
                    continue;
                }

                if ($this->hasValidBundle($candidate, $bundlesDir)) {
                    $accepted[] = $candidate;
                }

                if (count($accepted) === 2) {
                    break;
                }
            }
        }

        return [
            'schema_version' => 1,
            'current' => $hash,
            'accepted' => $accepted,
        ];
    }

    private function hasValidBundle(string $hash, string $bundlesDir): bool
    {
        $path = rtrim($bundlesDir, '/\\').DIRECTORY_SEPARATOR.substr($hash, strlen('sha256:')).'.json';
        if (! is_file($path)) {
            return false;
        }

        $bytes = file_get_contents($path);
        if ($bytes === false || hash('sha256', $bytes) !== substr($hash, strlen('sha256:'))) {
            return false;
        }

        try {
            json_decode($bytes, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            return false;
        }

        return true;
    }

    private function isHash(string $hash): bool
    {
        return preg_match('/^sha256:[0-9a-f]{64}$/', $hash) === 1;
    }
}
