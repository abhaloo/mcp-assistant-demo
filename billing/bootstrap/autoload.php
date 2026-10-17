<?php

declare(strict_types=1);
use Composer\Autoload\ClassLoader;

/**
 * Load composer, then bind the application's own classes to THIS tree.
 *
 * A git worktree shares the parent checkout's vendor/ through a symlink, and
 * composer derives its base directory from the REAL location of that directory
 * (autoload_psr4.php: $baseDir = dirname($vendorDir)). Left alone, every App\
 * class resolves to the PARENT checkout no matter which worktree is running,
 * so a worktree silently runs and tests code it does not contain.
 *
 * That is quietly destructive for artisan. Console\Kernel::commands() calls
 * $this->load(__DIR__.'/Commands'), and Kernel::load() derives each class name
 * by stripping app_path() -- the worktree -- off the file's real path -- the
 * parent. Nothing matches, so EVERY custom command is dropped without warning:
 * `php artisan ai-business-definitions:export` reports that the namespace does
 * not exist, in a repo that plainly defines it.
 *
 * Both of composer's lookup paths need rebinding, and the classmap is the one
 * that bites: it is consulted FIRST and holds paths frozen at dump-autoload
 * time, so pre-existing classes keep resolving to the parent while classes
 * added on the branch fall through to PSR-4 and resolve here -- one process,
 * two trees, which is worse than either alone.
 *
 * Only remapped when the file actually exists in this tree, so a class the
 * branch deletes still resolves to the parent rather than fatally vanishing.
 *
 * In the main checkout every path is already identical, so this is a no-op.
 */

/** @var ClassLoader $loader */
$loader = require dirname(__DIR__).'/vendor/autoload.php';

$root = dirname(__DIR__);
$parent = dirname((string) realpath(dirname(__DIR__).'/vendor'));

if ($parent !== $root) {
    $loader->setPsr4('App\\', $root.'/app');
    $loader->setPsr4('Database\\Factories\\', $root.'/database/factories');
    $loader->setPsr4('Database\\Seeders\\', $root.'/database/seeders');
    $loader->setPsr4('Tests\\', $root.'/tests');

    $rebound = [];

    foreach ($loader->getClassMap() as $class => $file) {
        $file = (string) $file;

        if (! str_starts_with($file, $parent)) {
            continue;
        }

        // Composer stores these unnormalised -- literally
        // "<parent>\vendor\composer/../../app/Console/Kernel.php" -- so the
        // path has to be resolved before it can be compared to anything.
        $real = realpath($file);

        if ($real === false) {
            continue;
        }

        $relative = ltrim(substr($real, strlen($parent)), '\\/');

        if (preg_match('#^(app|database|tests)[\\\\/]#', $relative) !== 1) {
            continue;
        }

        $local = $root.DIRECTORY_SEPARATOR.$relative;

        if (is_file($local)) {
            $rebound[$class] = $local;
        }
    }

    $loader->addClassMap($rebound);
}

return $loader;
