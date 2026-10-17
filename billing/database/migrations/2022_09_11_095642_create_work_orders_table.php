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
        Schema::create('work_orders', function (Blueprint $table) {
            $table->id();
            $table->foreignId('bill_id')->nullable();
            $table->foreignId('customer_id');
            $table->foreignId('specification_id')->nullable();
            $table->text('title')->nullable();
            $table->text('description')->nullable();
            $table->date('delivery_date')->nullable();
            $table->date('actual_delivery_date')->nullable();
            $table->json('specifications')->nullable();
            $table->string('ordered_qty')->default(1);
            $table->string('no_pages')->default(1);
            $table->string('status')->default('CREATED');
            $table->string('editing')->default('OPEN');
            $table->foreignId('entity_id')->constrained()->cascadeOnDelete();
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
        Schema::dropIfExists('work_orders');
    }
};
