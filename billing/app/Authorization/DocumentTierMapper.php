<?php

declare(strict_types=1);

namespace App\Authorization;

/**
 * Billing-side authority for the `record_access.document_tiers` v2 claim
 * (Task B3 / R1) — a byte-for-byte algorithmic port of the RAG service's
 * `get_access_tiers()` (mcp-rag-qa-ask-uplift\app\rag\access_tiers.py,
 * read-only reference).
 *
 * Parity holds because both sides consume the IDENTICAL `role`
 * (User::primaryRoleName()) and `permissions` (User::getAllPermissions()
 * names) values already carried by the v1 JWT claims — this class does not
 * invent a new role/permission resolution path, it re-derives the same
 * tiers RAG would compute from those same two inputs. See
 * DocumentTierMapperTest for the fixture-by-fixture parity proof (each
 * case's expected output derived BY HAND from the RAG source, not by
 * re-running it).
 *
 * Deliberately reproduces the RAG source's "pinned quirk" (its own
 * comment: "Role-level grants applied AFTER dependency resolution"):
 * ROLE_TIER_GRANTS is unioned in AFTER TIER_DEPENDENCIES resolves, and
 * dependency resolution is never re-run afterward. A bare 'manager' (zero
 * explicit finance/warehouse/printing permissions) therefore does NOT
 * pick up the 'customers' tier that finance/warehouse/printing normally
 * carry as a dependency, even though 'finance' and 'printing' end up
 * granted via the role grant.
 */
final class DocumentTierMapper
{
    /** @var list<string> */
    private const ALL_TIERS = [
        'all', 'sales', 'finance', 'admin', 'printing', 'graphic design', 'warehouse', 'customers',
    ];

    /** @var array<string, list<string>> */
    private const TIER_PERMISSIONS = [
        'sales' => [
            'view invoice', 'add invoice', 'edit invoice', 'delete invoice',
            'view quotation', 'add quotation', 'edit quotation', 'delete quotation',
            'view customer', 'add customer', 'edit customer',
            'view customer order', 'add customer order', 'edit customer order',
            'delete customer order', 'cancel customer order',
            'view product', 'add product', 'edit product',
        ],
        'finance' => [
            'add journal entry', 'view journal entries', 'profit and loss', 'balance sheet',
            'account statement', 'trial balance', 'cash flow statement',
            'view account', 'add account', 'edit account',
            'summary', 'outstanding receivable', 'payment', 'bill', 'bill overdue',
            'customer statement', 'supplier statement', 'full dashboard',
        ],
        'admin' => [
            'system audit', 'setting', 'view user', 'add user', 'edit user', 'delete user',
            'change permission', 'view department', 'add department', 'edit department', 'delete department',
        ],
        'printing' => [
            'view job', 'add job', 'edit job', 'delete job', 'move job',
            'approve job requests', 'job dashboard',
        ],
        'graphic design' => [
            'view specification', 'add specification', 'edit specification', 'delete specification',
            'view specification item', 'add specification item', 'edit specification item',
            'delete specification item',
        ],
        'warehouse' => [
            'view inventory', 'issue item', 'receive item', 'approve issue', 'approve receive',
            'edit inventory', 'delete inventory', 'inventory dashboard',
        ],
    ];

    /** @var array<string, list<string>> */
    private const TIER_DEPENDENCIES = [
        'finance' => ['customers'],
        'warehouse' => ['customers'],
        'printing' => ['customers'],
    ];

    /**
     * Applied AFTER dependency resolution — the pinned quirk (see class docblock).
     *
     * @var array<string, list<string>>
     */
    private const ROLE_TIER_GRANTS = [
        'manager' => ['sales', 'finance', 'printing', 'graphic design'],
    ];

    /**
     * @param  list<string>  $permissions
     * @return list<string> sorted, de-duplicated tier names
     */
    public static function tiersFor(string $role, array $permissions): array
    {
        if ($role === 'admin') {
            $tiers = self::ALL_TIERS;
            sort($tiers);

            return array_values($tiers);
        }

        $permissionSet = array_fill_keys($permissions, true);

        /** @var array<string, true> $tiers */
        $tiers = ['all' => true];
        foreach (self::TIER_PERMISSIONS as $tier => $tierPermissions) {
            foreach ($tierPermissions as $permission) {
                if (isset($permissionSet[$permission])) {
                    $tiers[$tier] = true;
                    break;
                }
            }
        }

        // Dependency resolution runs ONLY over the tiers earned above — mirrors
        // _resolve_dependencies() iterating the ORIGINAL (pre-role-grant) set.
        foreach (array_keys($tiers) as $tier) {
            foreach (self::TIER_DEPENDENCIES[$tier] ?? [] as $dependency) {
                $tiers[$dependency] = true;
            }
        }

        // Role grants are unioned in AFTER dependency resolution — never re-run.
        foreach (self::ROLE_TIER_GRANTS[$role] ?? [] as $grant) {
            $tiers[$grant] = true;
        }

        $result = array_keys($tiers);
        sort($result);

        return array_values($result);
    }
}
