<?php

declare(strict_types=1);

namespace App\Http\Requests;

use App\Services\AskAi\Restore\RestoreEvidenceInput;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Validator;

class RestoreEvidenceRequest extends FormRequest
{
    private const ALLOWED_ROOT_KEYS = ['thread_id', 'references'];

    private const ALLOWED_REFERENCE_KEYS = ['restore_ref'];

    public function authorize(): bool
    {
        return $this->user() !== null;
    }

    /**
     * @return array<string, mixed>
     */
    public function rules(): array
    {
        return [
            'thread_id' => ['required', 'string', 'min:1', 'max:64'],
            'references' => ['required', 'array', 'min:1', 'max:40'],
            'references.*' => ['required', 'array'],
            'references.*.restore_ref' => ['required', 'string', 'size:43', 'regex:/^[A-Za-z0-9_-]{43}$/'],
        ];
    }

    public function withValidator(Validator $validator): void
    {
        $validator->after(function (Validator $validator): void {
            $data = $validator->getData();

            foreach ($data as $key => $value) {
                if (! in_array($key, self::ALLOWED_ROOT_KEYS, true)) {
                    $validator->errors()->add($key, "The {$key} field is prohibited.");
                }
            }

            if (isset($data['references']) && is_array($data['references'])) {
                foreach ($data['references'] as $index => $item) {
                    if (is_array($item)) {
                        foreach ($item as $itemKey => $itemValue) {
                            if (! in_array($itemKey, self::ALLOWED_REFERENCE_KEYS, true)) {
                                $validator->errors()->add(
                                    "references.{$index}.{$itemKey}",
                                    "The references.{$index}.{$itemKey} field is prohibited."
                                );
                            }
                        }
                    }
                }
            }
        });
    }

    public function toInput(): RestoreEvidenceInput
    {
        $validated = $this->validated();
        /** @var list<string> $references */
        $references = array_column($validated['references'], 'restore_ref');

        return new RestoreEvidenceInput(
            threadId: (string) $validated['thread_id'],
            references: $references,
        );
    }
}
