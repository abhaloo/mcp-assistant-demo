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
        Schema::create('bills', function (Blueprint $table) {
            $table->id();
            $table->string('type')->default('Invoice');
            $table->foreignId('control_number_id');
            $table->foreignId('customer_id');
            $table->foreignId('currency_id');
            $table->text('description')->nullable();
            $table->foreignId('entity_id')->constrained()->cascadeOnDelete();
            $table->string('status')->default('CREATED');
            $table->dateTime('start_time')->nullable();
            $table->dateTime('end_time')->nullable();
            $table->date('due_date')->nullable();
            $table->foreignId('tax');
            $table->decimal('discount')->default(0);
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
        Schema::dropIfExists('bills');
    }
};
