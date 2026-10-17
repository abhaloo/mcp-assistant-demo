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
        Schema::create('bill_items', function (Blueprint $table) {
            $table->id();
            $table->foreignId('bill_id')->constrained()->cascadeOnDelete();
            $table->foreignId('product_id');
            $table->foreignId('tax_id');
            $table->decimal('price', 12);
            $table->decimal('discount', 12);
            $table->decimal('tax')->default(0);
            $table->text('description')->nullable();
            $table->integer('quantity');
            $table->integer('quantity_returned')->default(0);
            $table->integer('quantity_production')->default(0);
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
        Schema::dropIfExists('bill_items');
    }
};
