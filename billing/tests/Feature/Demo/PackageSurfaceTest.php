<?php

namespace Tests\Feature\Demo;

use Tests\TestCase;

class PackageSurfaceTest extends TestCase
{
    public function test_dropped_packages_have_zero_references_in_app_and_resources(): void
    {
        $patterns = [
            'Maatwebsite\Excel',
            'LaravelDaily\Invoices',
            'Lab404\Impersonate',
            'NotificationChannels\WebPush',
            'Pusher\\',
        ];

        $violations = [];

        $dirs = [
            app_path(),
            resource_path(),
        ];

        foreach ($dirs as $dir) {
            $iterator = new \RecursiveIteratorIterator(
                new \RecursiveDirectoryIterator($dir, \FilesystemIterator::SKIP_DOTS)
            );

            foreach ($iterator as $file) {
                if ($file->isDir()) {
                    continue;
                }
                $ext = $file->getExtension();
                if (! in_array($ext, ['php', 'js', 'css', 'blade.php'], true) && ! str_ends_with($file->getFilename(), '.blade.php')) {
                    continue;
                }

                $content = file_get_contents($file->getPathname());
                foreach ($patterns as $pattern) {
                    if (str_contains($content, $pattern)) {
                        $violations[] = "{$file->getPathname()} contains {$pattern}";
                    }
                }
            }
        }

        $this->assertEmpty($violations, "Found package references:\n".implode("\n", $violations));
    }
}
