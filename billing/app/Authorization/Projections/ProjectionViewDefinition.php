<?php

declare(strict_types=1);

namespace App\Authorization\Projections;

/**
 * One versioned `ai_v1_*` read-only SQL view definition (Task B5 / R1).
 *
 * `selectSql` is a fully portable ANSI-ish SQL body (SELECT ... FROM ...
 * [JOIN ...] [WHERE ...]) — no CREATE VIEW wrapper, no engine-specific
 * syntax (no DEFINER/SQL SECURITY, which is a deploy-time concern SQLite
 * cannot parse anyway). It runs unmodified on both file-based SQLite (local
 * suite) and MySQL 8.4 (CI): every arithmetic expression is explicitly
 * `CAST(... AS DOUBLE)`, which both engines resolve to an 8-byte IEEE-754
 * float — the same representation PHP's native float uses — so no
 * per-driver DDL variant was needed for this task's three payment-status
 * views (see the implementation report's portability-decision note).
 *
 * `columns` is the exact, ordered SELECT list: the resource's manifest
 * `readable_fields` (dotted relation fields flattened to `relation_column`,
 * see ProjectionDefinitions::outputColumnFor()) followed by its
 * ProjectionScopeColumns::forDefinition() scope columns. No `SELECT *`.
 *
 * Task B2 / §3.7 — `derivedTextColumns` names this view's columns that are
 * NOT a plain single-table passthrough (a CASE expression, a joined-table
 * column) and therefore carry an explicit canonical collation cast in
 * `selectSql` (ProjectionDefinitions::collatedText()) rather than inheriting
 * a source column's collation implicitly. Empty for every view except the
 * three Bill subtypes (`payment_status`) and job (`department_name`). Used
 * by ApplyAiProjectionViews' `--check` to target its collation/queryability
 * probe at exactly these columns, never every column.
 *
 * Task B2 / §3.7 — `atomicReplace` is TRUE when this definition's SQL was
 * built for a driver that supports `CREATE OR REPLACE VIEW` as one atomic
 * statement (no drop-before-create gap for a concurrent reader). MySQL
 * supports this; SQLite does not (`CREATE OR REPLACE VIEW` is a syntax
 * error there — verified empirically, task-B2-brief.md) and stays on the
 * legacy drop-then-create pair `applyStatements()` already used before this
 * task. See ProjectionDefinitions' `$driver` constructor param, which sets
 * this flag per view.
 */
final readonly class ProjectionViewDefinition
{
    /**
     * @param  list<string>  $columns
     * @param  list<string>  $derivedTextColumns
     */
    public function __construct(
        public string $resourceType,
        public string $viewName,
        public array $columns,
        public string $selectSql,
        public array $derivedTextColumns = [],
        public bool $atomicReplace = false,
    ) {}

    /** Idempotent: safe to run before every CREATE (run twice == run once). */
    public function dropStatement(): string
    {
        return "DROP VIEW IF EXISTS {$this->viewName}";
    }

    /**
     * Legacy, portable form: valid on every engine this app targets
     * (including SQLite), but requires a preceding dropStatement() — the
     * observable gap `applyStatements()` avoids when `atomicReplace` is true.
     */
    public function createStatement(): string
    {
        return "CREATE VIEW {$this->viewName} AS {$this->selectSql}";
    }

    /**
     * Task B2 / §3.7 — one atomic statement, no drop-before-create gap.
     * MySQL-only syntax (verified empirically not to parse on SQLite); only
     * ever emitted when `atomicReplace` is true — see that property's own
     * docblock.
     */
    public function createOrReplaceStatement(): string
    {
        return "CREATE OR REPLACE VIEW {$this->viewName} AS {$this->selectSql}";
    }

    /**
     * The statement(s) ApplyAiProjectionViews::apply() must run, IN ORDER,
     * to (re)create this view on the connection this definition was built
     * for: one atomic CREATE OR REPLACE when the target engine supports it,
     * otherwise the legacy drop-then-create pair.
     *
     * @return list<string>
     */
    public function applyStatements(): array
    {
        return $this->atomicReplace
            ? [$this->createOrReplaceStatement()]
            : [$this->dropStatement(), $this->createStatement()];
    }
}
