<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * @return void
     */
    public function up()
    {
        Schema::create('journals', function (Blueprint $table) {
            $table->id();
            $table->foreignId('entity_id');
            $table->foreignId('period_id');
            $table->foreignId('currency_id');
            $table->string('document_no');
            $table->date('post_date')->default(date('Y-m-d'));
            $table->foreignId('account_id');
            $table->foreignId('bill_id')->nullable();
            $table->foreignId('customer_id')->nullable();
            $table->foreignId('supplier_id')->nullable();
            $table->foreignId('item_id')->nullable();
            $table->decimal('debit', 13, 4);
            $table->decimal('credit', 13, 4);
            $table->decimal('foreign_debit', 13, 4);
            $table->decimal('foreign_credit', 13, 4);
            $table->decimal('exchange_rate', 12, 4);
            $table->string('description', 1000);
            $table->timestamps();
        });
    }

    /**
     * Reverse the migrations.
     *
     * @return void
     */
    public function down()
    {
        Schema::dropIfExists('journals');
    }
};
