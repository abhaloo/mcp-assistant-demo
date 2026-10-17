<?php

use App\Models\Account;

return [

    'accounts' => [

        // Balance Sheet: Asset Accounts
        Account::NON_CURRENT_ASSET => 'Non Current Asset',
        Account::CONTRA_ASSET => 'Contra Asset',
        Account::INVENTORY => 'Inventory',
        Account::BANK => 'Bank',
        Account::CASH => 'Cash',
        Account::CURRENT_ASSET => 'Current Asset',
        Account::RECEIVABLE => 'Receivable',

        // Balance Sheet: Liabilities Accounts
        Account::NON_CURRENT_LIABILITY => 'Non Current Liability',
        Account::CONTROL => 'Control',
        Account::CURRENT_LIABILITY => 'Current Liability',
        Account::PAYABLE => 'Payable',
        Account::RECONCILIATION => 'Reconciliation',

        // Balance Sheet: Equity Accounts
        Account::EQUITY => 'Equity',

        // Income Statement: Operations Accounts
        Account::OPERATING_REVENUE => 'Operating Revenue',
        Account::OPERATING_EXPENSE => 'Operating Expense',

        // Income Statement: Non Operations Accounts
        Account::NON_OPERATING_REVENUE => 'Non Operating Revenue',
        Account::DIRECT_EXPENSE => 'Direct Expense',
        Account::OVERHEAD_EXPENSE => 'Overhead Expense',
        Account::OTHER_EXPENSE => 'Other Expense',
    ],

    /*
     * Balance Sheet Sections
     */

    'asset_accounts' => [
        Account::NON_CURRENT_ASSET,
        Account::CONTRA_ASSET,
        Account::INVENTORY,
        Account::BANK,
        Account::CASH,
        Account::CURRENT_ASSET,
        Account::RECEIVABLE,
        Account::RECONCILIATION,
    ],

    'liability_accounts' => [
        Account::NON_CURRENT_LIABILITY,
        Account::CONTROL,
        Account::CURRENT_LIABILITY,
        Account::PAYABLE,
    ],

    'equity_accounts' => [
        Account::EQUITY,
    ],

    'recon_accounts' => [
        Account::RECONCILIATION,
    ],

    /*
     * PL Statement Sections
     */

    'operating_revenue_accounts' => [
        Account::OPERATING_REVENUE,
    ],

    'non_operating_revenue_accounts' => [
        Account::NON_OPERATING_REVENUE,
    ],

    'operating_expenses_accounts' => [
        Account::OPERATING_EXPENSE,
    ],

    'non_operating_expenses_accounts' => [
        Account::DIRECT_EXPENSE,
        Account::OVERHEAD_EXPENSE,
        Account::OTHER_EXPENSE,
    ],

];
