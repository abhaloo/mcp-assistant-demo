<?php

declare(strict_types=1);

namespace App\Http\Requests;

use App\Services\AskAi\AskAiOperation;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;
use Illuminate\Validation\Validator;

class AiSearchV2Request extends FormRequest
{
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
            'operation' => [
                'required',
                'string',
                Rule::in(array_column(AskAiOperation::cases(), 'value')),
            ],
            'run_id' => ['required', 'string'],
            'thread_id' => ['required', 'string'],
            'idempotency_key' => ['required', 'string'],
            'page_context' => ['nullable', 'array'],
            'record_context' => ['nullable', 'array'],
            'response_policy' => ['nullable', 'string', Rule::in(['strict', 'allow_partial'])],
            'protocol_version' => ['required', 'string', Rule::in(['2'])],
            'deadline_at_ms' => ['sometimes', 'nullable', 'integer'],

            // Prohibit mutation and client action fields
            'action' => ['prohibited'],
            'mutation' => ['prohibited'],
            'selector' => ['prohibited'],
            'ability' => ['prohibited'],

            // Operation-specific fields
            'question' => [
                'nullable',
                'string',
                'required_if:operation,new_question',
                'prohibited_if:operation,regenerate,clarification_reply,result_page',
            ],
            'continuation_ref' => [
                'nullable',
                'string',
                'required_if:operation,regenerate,clarification_reply',
                'prohibited_if:operation,new_question,result_page',
            ],
            'result_page_cursor' => [
                'nullable',
                'string',
                'required_if:operation,result_page',
                'prohibited_if:operation,new_question,regenerate,clarification_reply',
            ],
            'clarification_choice_id' => [
                'nullable',
                'string',
                'prohibited_if:operation,new_question,regenerate,result_page',
                'prohibits:clarification_free_text',
            ],
            'clarification_free_text' => [
                'nullable',
                'string',
                'prohibited_if:operation,new_question,regenerate,result_page',
                'prohibits:clarification_choice_id',
            ],
        ];
    }

    public function withValidator(Validator $validator): void
    {
        $validator->after(function (Validator $validator): void {
            $data = $validator->getData();
            $operation = $data['operation'] ?? null;

            $hasField = static fn (string $key): bool => array_key_exists($key, $data)
                && $data[$key] !== null
                && (! is_string($data[$key]) || trim($data[$key]) !== '');

            if ($operation === AskAiOperation::NewQuestion->value) {
                if (! $hasField('question')) {
                    $validator->errors()->add('question', 'The question field is required for new_question.');
                }
                if ($hasField('continuation_ref')) {
                    $validator->errors()->add('continuation_ref', 'The continuation_ref field is prohibited for new_question.');
                }
                if ($hasField('result_page_cursor')) {
                    $validator->errors()->add('result_page_cursor', 'The result_page_cursor field is prohibited for new_question.');
                }
                if ($hasField('clarification_choice_id')) {
                    $validator->errors()->add('clarification_choice_id', 'The clarification_choice_id field is prohibited for new_question.');
                }
                if ($hasField('clarification_free_text')) {
                    $validator->errors()->add('clarification_free_text', 'The clarification_free_text field is prohibited for new_question.');
                }
            } elseif ($operation === AskAiOperation::Regenerate->value) {
                if (! $hasField('continuation_ref')) {
                    $validator->errors()->add('continuation_ref', 'The continuation_ref field is required for regenerate.');
                }
                if ($hasField('question')) {
                    $validator->errors()->add('question', 'The question field is prohibited for regenerate.');
                }
                if ($hasField('result_page_cursor')) {
                    $validator->errors()->add('result_page_cursor', 'The result_page_cursor field is prohibited for regenerate.');
                }
                if ($hasField('clarification_choice_id')) {
                    $validator->errors()->add('clarification_choice_id', 'The clarification_choice_id field is prohibited for regenerate.');
                }
                if ($hasField('clarification_free_text')) {
                    $validator->errors()->add('clarification_free_text', 'The clarification_free_text field is prohibited for regenerate.');
                }
            } elseif ($operation === AskAiOperation::ClarificationReply->value) {
                if (! $hasField('continuation_ref')) {
                    $validator->errors()->add('continuation_ref', 'The continuation_ref field is required for clarification_reply.');
                }
                $hasChoice = $hasField('clarification_choice_id');
                $hasFreeText = $hasField('clarification_free_text');
                if ($hasChoice === $hasFreeText) {
                    $validator->errors()->add(
                        'clarification_choice_id',
                        'Exactly one of clarification_choice_id or clarification_free_text is required for clarification_reply.'
                    );
                }
                if ($hasField('question')) {
                    $validator->errors()->add('question', 'The question field is prohibited for clarification_reply.');
                }
                if ($hasField('result_page_cursor')) {
                    $validator->errors()->add('result_page_cursor', 'The result_page_cursor field is prohibited for clarification_reply.');
                }
            } elseif ($operation === AskAiOperation::ResultPage->value) {
                if (! $hasField('result_page_cursor')) {
                    $validator->errors()->add('result_page_cursor', 'The result_page_cursor field is required for result_page.');
                }
                if ($hasField('question')) {
                    $validator->errors()->add('question', 'The question field is prohibited for result_page.');
                }
                if ($hasField('continuation_ref')) {
                    $validator->errors()->add('continuation_ref', 'The continuation_ref field is prohibited for result_page.');
                }
                if ($hasField('clarification_choice_id')) {
                    $validator->errors()->add('clarification_choice_id', 'The clarification_choice_id field is prohibited for result_page.');
                }
                if ($hasField('clarification_free_text')) {
                    $validator->errors()->add('clarification_free_text', 'The clarification_free_text field is prohibited for result_page.');
                }
            }
        });
    }
}
