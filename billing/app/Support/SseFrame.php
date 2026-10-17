<?php

declare(strict_types=1);

namespace App\Support;

use Illuminate\Support\Str;

/**
 * Pure formatter for Server-Sent Events (SSE) frames.
 *
 * Produces the exact wire shape that {@see SseUsageObserver} parses and the
 * frontend `mcp-global-search.js` consumes:
 *
 *   event: <name>\n
 *   data: <json>\n
 *   \n
 *
 * Used by AiSearchController to synthesise an SSE stream from a buffered JSON
 * Answer when the upstream cannot stream a database question (see
 * docs/superpowers/specs/2026-06-15-sql-answers-sse-fallback-design.md).
 *
 * Stateless and side-effect-free: no pacing, no echo, no I/O — the controller
 * owns the playback cadence so this stays trivially unit-testable.
 */
final class SseFrame
{
    /**
     * Maximum number of token frames a single answer is split into. Long
     * answers grow their per-chunk word count so the controller's paced
     * playback stays bounded (≤ ~1.5s at 50ms/frame).
     */
    private const MAX_TOKEN_FRAMES = 30;

    /**
     * Approximate words per token chunk for short answers.
     */
    private const WORDS_PER_CHUNK = 4;

    /**
     * Format one SSE frame: an `event:` line, a `data:` JSON line, and the
     * terminating blank line.
     */
    public static function event(string $event, mixed $data): string
    {
        return "event: {$event}\n"
            .'data: '.json_encode(
                $data,
                JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR
            )
            ."\n\n";
    }

    /**
     * Build the ordered list of synthetic frames for a buffered Answer:
     * token frames (chunked, whitespace-preserving), an optional sources
     * frame (only when sources are present), then a done frame.
     *
     * @param  array{answer?: string, sources?: array<int, array<string, mixed>>, model?: mixed, follow_up_suggestions?: mixed, business_query?: array<string, mixed>}  $answer
     * @return list<string>
     */
    public static function framesForAnswer(array $answer): array
    {
        $frames = [
            self::event('status', ['phase' => 'understanding', 'label' => 'Understanding your question']),
        ];

        $queryType = (string) ($answer['query_type'] ?? '');
        if ($queryType === 'structured') {
            $frames[] = self::event('status', ['phase' => 'searching_database', 'label' => 'Finding relevant records']);
            $frames[] = self::event('status', ['phase' => 'inspecting_access', 'label' => 'Checking access']);
            $frames[] = self::event('status', ['phase' => 'writing_query', 'label' => 'Preparing the lookup']);
            $frames[] = self::event('status', ['phase' => 'running_query', 'label' => 'Reading current data']);
        }
        $frames[] = self::event('status', ['phase' => 'writing_answer', 'label' => 'Writing the answer']);

        $sources = $answer['sources'] ?? [];
        $mappedSources = is_array($sources) ? self::mapSources($sources) : [];
        $text = (string) ($answer['answer'] ?? '');
        $citations = self::mapCitations($answer['citations'] ?? null, $mappedSources, $text);
        $text = self::stripUnboundCitationMarkers($text, $citations);
        foreach (self::chunkAnswer($text) as $chunk) {
            $frames[] = self::event('token', ['d' => $chunk]);
        }

        if ($mappedSources !== []) {
            $frames[] = self::event('sources', $mappedSources);
        }

        $frames[] = self::event('citations', $citations);

        $provenance = $answer['sql_provenance'] ?? null;
        if (is_array($provenance) && self::hasSqlProvenancePayload($provenance)) {
            $frames[] = self::event('sql_provenance', self::mapSqlProvenance($provenance));
        }

        $done = [
            'model' => (string) ($answer['model'] ?? 'default'),
            'thread_id' => $answer['thread_id'] ?? null,
            'exchange_id' => $answer['exchange_id'] ?? null,
            'context_mode' => (string) ($answer['context_mode'] ?? 'none'),
            'trace_id' => $answer['trace_id'] ?? null,
            'feedback_token' => $answer['feedback_token'] ?? null,
            'operation' => (string) ($answer['operation'] ?? 'ask'),
            'follow_up_suggestions' => self::mapFollowUpSuggestions(
                $answer['follow_up_suggestions'] ?? []
            ),
        ];

        // Progressive enhancement: older RAG responses do not carry a
        // business_query envelope, while newer responses use it as the single
        // source of counts, coverage, provenance and pagination. Preserve the
        // canonical object verbatim so additive fields remain forward
        // compatible and the typed next_page_action is not reconstructed from
        // answer prose or legacy SQL explanation fields.
        if (is_array($answer['business_query'] ?? null)) {
            $done['business_query'] = $answer['business_query'];
        }

        $frames[] = self::event('done', $done);

        return $frames;
    }

    /**
     * @return list<string>
     */
    private static function mapFollowUpSuggestions(mixed $raw): array
    {
        if (! is_array($raw)) {
            return [];
        }

        $suggestions = [];
        $seen = [];
        foreach ($raw as $value) {
            if (! is_string($value)) {
                continue;
            }

            $suggestion = (string) Str::of($value)->squish()->limit(160, '');
            $key = Str::lower($suggestion);
            if (Str::length($suggestion) < 2 || isset($seen[$key])) {
                continue;
            }

            $seen[$key] = true;
            $suggestions[] = $suggestion;
            if (count($suggestions) === 3) {
                break;
            }
        }

        return $suggestions;
    }

    /**
     * @return array{parsed: bool, cited: list<array{marker: string, id: string}>}
     */
    private static function mapCitations(mixed $raw, array $sources, string $answer): array
    {
        if (! is_array($raw)) {
            return ['parsed' => false, 'cited' => []];
        }

        $parsed = ($raw['parsed'] ?? null) === true;
        if (! $parsed) {
            return ['parsed' => false, 'cited' => []];
        }

        $sourceIds = array_fill_keys(array_column($sources, 'id'), true);
        $cited = [];
        foreach ($raw['cited'] ?? [] as $citation) {
            if (! is_array($citation)) {
                continue;
            }
            $marker = trim((string) ($citation['marker'] ?? ''));
            $id = trim((string) ($citation['id'] ?? ''));
            $markerPattern = '/\[(?:Source\s+)?'.preg_quote($marker, '/').'\]/i';
            if (
                ctype_digit($marker)
                && (int) $marker > 0
                && isset($sourceIds[$id])
                && preg_match($markerPattern, $answer) === 1
            ) {
                $cited[] = ['marker' => $marker, 'id' => $id];
            }
        }

        return $cited !== []
            ? ['parsed' => true, 'cited' => $cited]
            : ['parsed' => false, 'cited' => []];
    }

    /**
     * @param  array{parsed: bool, cited: list<array{marker: string, id: string}>}  $citations
     */
    private static function stripUnboundCitationMarkers(string $text, array $citations): string
    {
        $boundMarkers = array_fill_keys(array_column($citations['cited'], 'marker'), true);

        return (string) preg_replace_callback(
            '/\[(?:Source\s+)?(\d+)\]/i',
            static fn (array $matches): string => isset($boundMarkers[$matches[1]]) ? $matches[0] : '',
            $text
        );
    }

    /**
     * @param  array<string, mixed>  $provenance
     */
    private static function hasSqlProvenancePayload(array $provenance): bool
    {
        $queries = $provenance['queries'] ?? [];
        $links = $provenance['record_links'] ?? [];

        return (is_array($queries) && $queries !== [])
            || (is_array($links) && $links !== []);
    }

    /**
     * Pass through trusted sql_provenance for JSON→SSE fallback (records-only
     * may ship empty queries with record_links).
     *
     * @param  array<string, mixed>  $provenance
     * @return array{queries: list<string>, record_links: list<array<string, mixed>>}
     */
    private static function mapSqlProvenance(array $provenance): array
    {
        $queries = [];
        foreach ($provenance['queries'] ?? [] as $query) {
            if (is_string($query)) {
                $queries[] = $query;
            }
        }

        $links = [];
        foreach ($provenance['record_links'] ?? [] as $link) {
            if (! is_array($link)) {
                continue;
            }
            $links[] = [
                'table' => (string) ($link['table'] ?? ''),
                'record_id' => (int) ($link['record_id'] ?? 0),
                'label' => (string) ($link['label'] ?? ''),
                'url' => (string) ($link['url'] ?? ''),
            ];
        }

        return [
            'queries' => $queries,
            'record_links' => $links,
        ];
    }

    /**
     * Split the answer into ~WORDS_PER_CHUNK-word groups, capped at
     * MAX_TOKEN_FRAMES chunks. Whitespace is preserved: splitting keeps the
     * delimiters so the concatenation of every chunk equals the input exactly.
     *
     * @return list<string>
     */
    private static function chunkAnswer(string $text): array
    {
        if ($text === '') {
            return [];
        }

        // Split on whitespace but KEEP the delimiters as their own pieces, so
        // re-joining loses nothing. Pairs become [word, gap, word, gap, ...].
        $pieces = preg_split('/(\s+)/u', $text, -1, PREG_SPLIT_DELIM_CAPTURE | PREG_SPLIT_NO_EMPTY);
        if ($pieces === false || $pieces === []) {
            return [$text];
        }

        // Each "word + following gap" is two array entries; a chunk of N words
        // spans ~2N pieces. Grow the word count for long answers so the number
        // of chunks never exceeds MAX_TOKEN_FRAMES.
        $wordCount = (int) ceil(count($pieces) / 2);
        $wordsPerChunk = self::WORDS_PER_CHUNK;
        if ($wordCount > self::MAX_TOKEN_FRAMES * self::WORDS_PER_CHUNK) {
            $wordsPerChunk = (int) ceil($wordCount / self::MAX_TOKEN_FRAMES);
        }

        $piecesPerChunk = max(1, $wordsPerChunk * 2);

        $chunks = [];
        $buffer = '';
        $piecesInBuffer = 0;

        foreach ($pieces as $piece) {
            $buffer .= $piece;
            $piecesInBuffer++;

            if ($piecesInBuffer >= $piecesPerChunk) {
                $chunks[] = $buffer;
                $buffer = '';
                $piecesInBuffer = 0;
            }
        }

        if ($buffer !== '') {
            $chunks[] = $buffer;
        }

        return $chunks;
    }

    /**
     * Map FastAPI Source rows {content, source_file, chunk_index} to the SSE
     * shape the frontend reads {id, title, snippet}.
     *
     * @param  array<int, array<string, mixed>>  $sources
     * @return list<array{id: string, title: string, snippet: string}>
     */
    private static function mapSources(array $sources): array
    {
        $mapped = [];

        foreach (array_values($sources) as $index => $source) {
            $sourceFile = (string) ($source['source_file'] ?? '');
            $chunkIndex = $source['chunk_index'] ?? null;
            $upstreamId = trim((string) ($source['id'] ?? ''));

            // Adversarial review BLOCKER-11: PREFER the identifier RAG sent.
            // RAG binds its citations to Source.id, so re-deriving one here
            // silently broke every record citation (record sources carry an
            // explicit id and a null chunk_index, and the old fallback
            // appended the positional index to it). The derivation below is
            // kept only for sources that genuinely carry no id -- i.e. any
            // RAG build predating the Source.id field.
            $id = $upstreamId !== ''
                ? $upstreamId
                : ($chunkIndex !== null
                    ? $sourceFile.':'.$chunkIndex
                    : $sourceFile.':'.$index);

            $base = basename($sourceFile);

            $mapped[] = [
                'id' => $id,
                'title' => $base !== '' ? $base : $sourceFile,
                'snippet' => mb_substr((string) ($source['content'] ?? ''), 0, 200),
            ];
        }

        return $mapped;
    }
}
