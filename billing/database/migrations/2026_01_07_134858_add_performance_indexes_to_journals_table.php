<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Performance optimization indexes for account statement queries.
 * 
 * These indexes optimize:
 * - Account statement queries (account_id + post_date)
 * - Double-entry lookup queries (document_no)
 * - Customer/Supplier statement queries
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('journals', function (Blueprint $table) {
            // Primary optimization: Account statements filter by account_id AND post_date
            // This composite index dramatically improves both opening balance and transaction queries
            $table->index(['account_id', 'post_date'], 'idx_journals_account_postdate');

            // For otherTransaction() lookups - finding the other side of double-entry
            $table->index('document_no', 'idx_journals_document_no');

            // For customer statement queries
            $table->index(['customer_id', 'post_date'], 'idx_journals_customer_postdate');

            // For supplier statement queries
            $table->index(['supplier_id', 'post_date'], 'idx_journals_supplier_postdate');
        });
    }

    public function down(): void
    {
        Schema::table('journals', function (Blueprint $table) {
            $table->dropIndex('idx_journals_account_postdate');
            $table->dropIndex('idx_journals_document_no');
            $table->dropIndex('idx_journals_customer_postdate');
            $table->dropIndex('idx_journals_supplier_postdate');
        });
    }
};
