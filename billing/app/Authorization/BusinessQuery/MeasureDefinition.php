<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

use InvalidArgumentException;

/**
 * Typed measure over an ai_v1_bq_* projection view.
 *
 * Grain and age are set through factories so illegal combinations cannot be built.
 */
readonly class MeasureDefinition
{
    /**
     * @param  'sum'|'avg'|'min'|'max'|'count'|'count_distinct'|'derived'  $aggType
     * @param  'currency'|'number'|'percent'|null  $format
     * @param  'enabled'|'decision_pending'|'unsupported'  $capabilityState
     * @param  list<string>  $allowedTimeAxes
     * @param  'ratio'|'share'|null  $expressionKind
     * @param  list<string>  $derivedFrom
     */
    public function __construct(
        public string $name,
        public string $owningResource,
        public string $sqlExpression,
        public string $aggType,
        public ?string $filterSql,
        public string $description,
        public ?string $format,
        public ?string $timeDimension,
        public string $capabilityState = 'enabled',
        public ?string $currencyDimension = null,
        public array $allowedTimeAxes = [],
        public bool $snapshot = false,
        public bool $relativeToBusinessDate = false,
        public ?int $overdueAfterDays = null,
        public array $exampleQuestions = [],
        public ?string $expressionKind = null,
        public array $derivedFrom = [],
    ) {
        if ($snapshot && ($timeDimension !== null || $allowedTimeAxes !== [])) {
            throw new InvalidArgumentException("measure [{$name}] snapshot cannot declare a time axis");
        }
        if ($overdueAfterDays !== null) {
            if (! $relativeToBusinessDate) {
                throw new InvalidArgumentException("measure [{$name}] overdueAfterDays requires relativeToBusinessDate");
            }
            if ($overdueAfterDays <= 0) {
                throw new InvalidArgumentException("measure [{$name}] overdueAfterDays must be greater than 0");
            }
            $filter = $filterSql ?? '';
            if (str_contains($filter, 'days_past_due') || str_contains($filter, 'CURRENT_DATE')) {
                throw new InvalidArgumentException(
                    "measure [{$name}] overdueAfterDays filterSql must not encode session date",
                );
            }
        }
        if ($snapshot) {
            $filter = $filterSql ?? '';
            if (str_contains($filter, 'created_at') || str_contains($filter, 'CURRENT_DATE')) {
                throw new InvalidArgumentException(
                    "measure [{$name}] snapshot filterSql must not encode created_at or CURRENT_DATE",
                );
            }
        }
    }

    /**
     * @param  'sum'|'avg'|'min'|'max'|'count'|'count_distinct'  $aggType
     * @param  'currency'|'number'|'percent'|null  $format
     * @param  'enabled'|'decision_pending'|'unsupported'  $capabilityState
     * @param  list<string>  $allowedTimeAxes
     */
    public static function timed(
        string $name,
        string $owningResource,
        string $sqlExpression,
        string $aggType,
        ?string $filterSql,
        string $description,
        ?string $format,
        string $timeDimension,
        string $capabilityState = 'enabled',
        ?string $currencyDimension = null,
        array $allowedTimeAxes = [],
        array $exampleQuestions = [],
    ): self {
        return new self(
            name: $name,
            owningResource: $owningResource,
            sqlExpression: $sqlExpression,
            aggType: $aggType,
            filterSql: $filterSql,
            description: $description,
            format: $format,
            timeDimension: $timeDimension,
            capabilityState: $capabilityState,
            currencyDimension: $currencyDimension,
            allowedTimeAxes: $allowedTimeAxes,
            exampleQuestions: $exampleQuestions,
        );
    }

    /**
     * @param  'sum'|'avg'|'min'|'max'|'count'|'count_distinct'  $aggType
     * @param  'currency'|'number'|'percent'|null  $format
     * @param  'enabled'|'decision_pending'|'unsupported'  $capabilityState
     */
    public static function snapshot(
        string $name,
        string $owningResource,
        string $sqlExpression,
        string $aggType,
        ?string $filterSql,
        string $description,
        ?string $format,
        string $capabilityState = 'enabled',
        ?string $currencyDimension = null,
    ): self {
        return new self(
            name: $name,
            owningResource: $owningResource,
            sqlExpression: $sqlExpression,
            aggType: $aggType,
            filterSql: $filterSql,
            description: $description,
            format: $format,
            timeDimension: null,
            capabilityState: $capabilityState,
            currencyDimension: $currencyDimension,
            snapshot: true,
        );
    }

    /**
     * @param  'sum'|'avg'|'min'|'max'|'count'|'count_distinct'  $aggType
     * @param  'currency'|'number'|'percent'|null  $format
     * @param  'enabled'|'decision_pending'|'unsupported'  $capabilityState
     * @param  list<string>  $allowedTimeAxes
     */
    public static function ungrained(
        string $name,
        string $owningResource,
        string $sqlExpression,
        string $aggType,
        ?string $filterSql,
        string $description,
        ?string $format,
        string $capabilityState = 'enabled',
        ?string $currencyDimension = null,
        array $allowedTimeAxes = [],
    ): self {
        return new self(
            name: $name,
            owningResource: $owningResource,
            sqlExpression: $sqlExpression,
            aggType: $aggType,
            filterSql: $filterSql,
            description: $description,
            format: $format,
            timeDimension: null,
            capabilityState: $capabilityState,
            currencyDimension: $currencyDimension,
            allowedTimeAxes: $allowedTimeAxes,
        );
    }

    /**
     * @param  'sum'|'avg'|'min'|'max'|'count'|'count_distinct'  $aggType
     * @param  'currency'|'number'|'percent'|null  $format
     * @param  'enabled'|'decision_pending'|'unsupported'  $capabilityState
     */
    public static function overdue(
        string $name,
        string $owningResource,
        string $sqlExpression,
        string $aggType,
        ?string $filterSql,
        string $description,
        ?string $format,
        string $timeDimension,
        int $afterDays,
        string $capabilityState = 'enabled',
        ?string $currencyDimension = null,
    ): self {
        return new self(
            name: $name,
            owningResource: $owningResource,
            sqlExpression: $sqlExpression,
            aggType: $aggType,
            filterSql: $filterSql,
            description: $description,
            format: $format,
            timeDimension: $timeDimension,
            capabilityState: $capabilityState,
            currencyDimension: $currencyDimension,
            relativeToBusinessDate: true,
            overdueAfterDays: $afterDays,
        );
    }

    /**
     * @param  'ratio'|'share'  $expressionKind
     * @param  list<string>  $derivedFrom
     * @param  'currency'|'number'|'percent'|null  $format
     * @param  'enabled'|'decision_pending'|'unsupported'  $capabilityState
     * @param  list<string>  $allowedTimeAxes
     * @param  list<string>  $exampleQuestions
     */
    public static function derived(
        string $name,
        string $owningResource,
        string $expressionKind,
        array $derivedFrom,
        string $description,
        ?string $format = null,
        ?string $timeDimension = null,
        string $capabilityState = 'enabled',
        ?string $currencyDimension = null,
        array $allowedTimeAxes = [],
        array $exampleQuestions = [],
    ): self {
        return new self(
            name: $name,
            owningResource: $owningResource,
            sqlExpression: '',
            aggType: 'derived',
            filterSql: null,
            description: $description,
            format: $format,
            timeDimension: $timeDimension,
            capabilityState: $capabilityState,
            currencyDimension: $currencyDimension,
            allowedTimeAxes: $allowedTimeAxes,
            exampleQuestions: $exampleQuestions,
            expressionKind: $expressionKind,
            derivedFrom: $derivedFrom,
        );
    }

    /** @return array<string, mixed> */
    public function toExportArray(): array
    {
        return self::withAdditiveKeys([
            'name' => $this->name,
            'owning_resource' => $this->owningResource,
            'sql_expression' => $this->sqlExpression,
            'agg_type' => $this->aggType,
            'filter_sql' => $this->filterSql,
            'description' => $this->description,
            'format' => $this->format,
            'time_dimension' => $this->timeDimension,
            'capability_state' => $this->capabilityState,
            'currency_dimension' => $this->currencyDimension,
        ], [
            'allowed_time_axes' => $this->allowedTimeAxes,
            'snapshot' => $this->snapshot,
            'relative_to_business_date' => $this->relativeToBusinessDate,
            'overdue_after_days' => $this->overdueAfterDays,
            'expression_kind' => $this->expressionKind,
            'derived_from' => $this->derivedFrom,
        ]);
    }

    /**
     * @param  array<string, mixed>  $payload
     * @param  array<string, mixed>  $additive
     * @return array<string, mixed>
     */
    public static function withAdditiveKeys(array $payload, array $additive): array
    {
        foreach ($additive as $key => $value) {
            if ($value === [] || $value === false || $value === null) {
                continue;
            }
            $payload[$key] = $value;
        }

        return $payload;
    }
}
