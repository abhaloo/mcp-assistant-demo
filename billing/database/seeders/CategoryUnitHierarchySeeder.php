<?php

namespace Database\Seeders;

use App\Models\Category;
use Illuminate\Database\Seeder;

/**
 * NOTE: This seeder is provided for reference and should NOT be run automatically.
 *
 * It seeds default unit hierarchy configurations for categories.
 * Admins should configure unit hierarchies manually through the UI in Category management,
 * or selectively run this seeder only for specific categories they want to set up.
 *
 * To use this seeder manually:
 * php artisan db:seed --class=CategoryUnitHierarchySeeder --force
 */
class CategoryUnitHierarchySeeder extends Seeder
{
    /**
     * Run the database seeder.
     *
     * @return void
     */
    public function run()
    {
        $this->command->info('Seeding category unit hierarchies...');

        // Papers category
        $this->seedCategory('Papers', [
            'invoice_unit' => ['label' => 'MT', 'type' => 'weight'],
            'count_levels' => [
                ['level' => 1, 'label' => 'Pallet', 'key' => 'pallet'],
                ['level' => 2, 'label' => 'Ream', 'key' => 'ream'],
                ['level' => 3, 'label' => 'Sheet', 'key' => 'sheet', 'is_stock_unit' => true],
            ],
            'attributes' => [
                [
                    'key' => 'gsm',
                    'label' => 'GSM',
                    'type' => 'number',
                    'required' => true,
                    'weight_factor' => true,
                    'show_in_list' => true,
                ],
                [
                    'key' => 'size',
                    'label' => 'Size (cm)',
                    'type' => 'dimensions',
                    'required' => true,
                    'weight_factor' => true,
                    'show_in_list' => true,
                    'placeholder' => '64x90',
                ],
            ],
        ]);

        // Art Papers (same as Papers)
        $this->seedCategory('Art Papers', [
            'invoice_unit' => ['label' => 'MT', 'type' => 'weight'],
            'count_levels' => [
                ['level' => 1, 'label' => 'Pallet', 'key' => 'pallet'],
                ['level' => 2, 'label' => 'Ream', 'key' => 'ream'],
                ['level' => 3, 'label' => 'Sheet', 'key' => 'sheet', 'is_stock_unit' => true],
            ],
            'attributes' => [
                [
                    'key' => 'gsm',
                    'label' => 'GSM',
                    'type' => 'number',
                    'required' => true,
                    'weight_factor' => true,
                    'show_in_list' => true,
                ],
                [
                    'key' => 'size',
                    'label' => 'Size (cm)',
                    'type' => 'dimensions',
                    'required' => true,
                    'weight_factor' => true,
                    'show_in_list' => true,
                    'placeholder' => '64x90',
                ],
                [
                    'key' => 'finish',
                    'label' => 'Finish',
                    'type' => 'select',
                    'required' => false,
                    'show_in_list' => true,
                    'options' => ['Glossy', 'Matt'],
                ],
            ],
        ]);

        // Boards category
        $this->seedCategory('Boards', [
            'invoice_unit' => ['label' => 'MT', 'type' => 'weight'],
            'count_levels' => [
                ['level' => 1, 'label' => 'Pallet', 'key' => 'pallet'],
                ['level' => 2, 'label' => 'Sheet', 'key' => 'sheet', 'is_stock_unit' => true],
            ],
            'attributes' => [
                [
                    'key' => 'gsm',
                    'label' => 'GSM',
                    'type' => 'number',
                    'required' => true,
                    'weight_factor' => true,
                    'show_in_list' => true,
                ],
                [
                    'key' => 'size',
                    'label' => 'Size (cm)',
                    'type' => 'dimensions',
                    'required' => true,
                    'weight_factor' => true,
                    'show_in_list' => true,
                ],
                [
                    'key' => 'board_type',
                    'label' => 'Board Type',
                    'type' => 'select',
                    'required' => false,
                    'show_in_list' => true,
                    'options' => ['Duplex Grey Back', 'Food Board', 'Straw Board', 'Moshi Board'],
                ],
            ],
        ]);

        // Bond Papers
        $this->seedCategory('Bond Papers', [
            'invoice_unit' => ['label' => 'MT', 'type' => 'weight'],
            'count_levels' => [
                ['level' => 1, 'label' => 'Pallet', 'key' => 'pallet'],
                ['level' => 2, 'label' => 'Ream', 'key' => 'ream'],
                ['level' => 3, 'label' => 'Sheet', 'key' => 'sheet', 'is_stock_unit' => true],
            ],
            'attributes' => [
                [
                    'key' => 'gsm',
                    'label' => 'GSM',
                    'type' => 'number',
                    'required' => true,
                    'weight_factor' => true,
                    'show_in_list' => true,
                ],
                [
                    'key' => 'size',
                    'label' => 'Size (cm)',
                    'type' => 'dimensions',
                    'required' => true,
                    'weight_factor' => true,
                    'show_in_list' => true,
                ],
            ],
        ]);

        // Chemicals category
        $this->seedCategory('Chemicals', [
            'invoice_unit' => ['label' => 'Drum', 'type' => 'count'],
            'count_levels' => [
                ['level' => 1, 'label' => 'Drum', 'key' => 'drum'],
                ['level' => 2, 'label' => 'Liter', 'key' => 'liter', 'is_stock_unit' => true],
            ],
            'attributes' => [
                [
                    'key' => 'concentration',
                    'label' => 'Concentration',
                    'type' => 'text',
                    'required' => false,
                    'placeholder' => 'e.g., 25%',
                ],
            ],
        ]);

        // Inks category
        $this->seedCategory('Inks', [
            'invoice_unit' => ['label' => 'Tin', 'type' => 'count'],
            'count_levels' => [
                ['level' => 1, 'label' => 'Tin', 'key' => 'tin'],
                ['level' => 2, 'label' => 'Kg', 'key' => 'kg', 'is_stock_unit' => true],
            ],
            'attributes' => [
                [
                    'key' => 'color',
                    'label' => 'Color',
                    'type' => 'select',
                    'required' => true,
                    'show_in_list' => true,
                    'options' => [
                        'Process Black',
                        'Process Cyan',
                        'Process Magenta',
                        'Process Yellow',
                        'Bright Orange',
                        'Gold',
                        'Silver',
                        'Royal Blue',
                        'Rust Brown',
                        'White',
                        'Custom',
                    ],
                ],
                [
                    'key' => 'tin_size',
                    'label' => 'Tin Size',
                    'type' => 'select',
                    'required' => false,
                    'options' => ['1kg', '2.5kg', '4kg', '6kg'],
                ],
            ],
        ]);

        // Glue category
        $this->seedCategory('Glue', [
            'invoice_unit' => ['label' => 'Bucket', 'type' => 'count'],
            'count_levels' => [
                ['level' => 1, 'label' => 'Bucket', 'key' => 'bucket'],
                ['level' => 2, 'label' => 'Kg', 'key' => 'kg', 'is_stock_unit' => true],
            ],
            'attributes' => [
                [
                    'key' => 'glue_type',
                    'label' => 'Type',
                    'type' => 'select',
                    'required' => false,
                    'show_in_list' => true,
                    'options' => ['Binding Glue - Bondex', 'Lamination Glue - Arobond', 'Perfect Binding - Hot Melt'],
                ],
            ],
        ]);

        // NCR Papers
        $this->seedCategory('NCR Papers', [
            'invoice_unit' => ['label' => 'Pallet', 'type' => 'count'],
            'count_levels' => [
                ['level' => 1, 'label' => 'Pallet', 'key' => 'pallet'],
                ['level' => 2, 'label' => 'Ream', 'key' => 'ream'],
                ['level' => 3, 'label' => 'Sheet', 'key' => 'sheet', 'is_stock_unit' => true],
            ],
            'attributes' => [
                [
                    'key' => 'ncr_type',
                    'label' => 'NCR Type',
                    'type' => 'select',
                    'required' => true,
                    'show_in_list' => true,
                    'options' => ['CB - white', 'CFB - pink', 'CFB - blue', 'CFB - green', 'CF - yellow'],
                ],
                [
                    'key' => 'gsm',
                    'label' => 'GSM',
                    'type' => 'number',
                    'required' => false,
                ],
                [
                    'key' => 'size',
                    'label' => 'Size (cm)',
                    'type' => 'dimensions',
                    'required' => true,
                    'placeholder' => '61x86',
                ],
            ],
        ]);

        $this->command->info('Category unit hierarchies seeded successfully!');
    }

    /**
     * Seed a category with unit hierarchy configuration.
     */
    private function seedCategory(string $name, array $unitHierarchy): void
    {
        $category = Category::where('name', 'like', "%{$name}%")->first();

        if ($category) {
            $category->update(['unit_hierarchy' => $unitHierarchy]);
            $this->command->info("  ✓ Updated category: {$category->name}");
        } else {
            $this->command->warn("  ✗ Category not found: {$name}");
        }
    }
}
