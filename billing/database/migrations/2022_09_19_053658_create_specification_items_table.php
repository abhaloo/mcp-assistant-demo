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
        Schema::create('specification_items', function (Blueprint $table) {
            $table->id();
            $table->foreignId('specification_id');
            $table->string('name');
            $table->string('value')->nullable();
            $table->string('type');
            $table->string('columns')->nullable();
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
        Schema::dropIfExists('specification_items');
    }
};
