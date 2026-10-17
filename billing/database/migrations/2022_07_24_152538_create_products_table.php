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
        Schema::create('products', function (Blueprint $table) {
            $table->id();
            $table->foreignId('entity_id')->constrained()->cascadeOnDelete();
            $table->foreignId('category_id')->constrained()->cascadeOnDelete();
            $table->foreignId('unit_id')->constrained()->cascadeOnDelete();
            $table->string('type')->default('Service');
            $table->string('name');
            $table->json('option')->nullable();
            $table->foreignId('buy_account')->nullable();
            $table->foreignId('sell_account')->nullable();
            $table->string('sku')->nullable();
            $table->decimal('price', 12)->nullable();
            $table->decimal('reorder', 10)->default(0);
            $table->boolean('inventory')->default(true);
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
        Schema::dropIfExists('products');
    }
};
