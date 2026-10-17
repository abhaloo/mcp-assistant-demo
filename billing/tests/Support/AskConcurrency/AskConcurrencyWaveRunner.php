<?php

declare(strict_types=1);

namespace Tests\Support\AskConcurrency;

use App\Models\User;
use Illuminate\Support\Facades\Cache;
use Illuminate\Testing\TestResponse;
use Tests\Support\CapturedStreamOutput;

/**
 * Runs a Track S wave through billing POST /ai-search/stream.
 *
 * PR / CI path: {@see runSerialWave} posts assignments one-after-another.
 * That proves canned SSE id isolation and terminal/hang/storm judge wiring;
 * it is NOT synchronized C=3 overlap proof. Live Track S must use a parallel
 * invoker (separate seam) against a paired env-card.
 *
 * Feature tests supply a `$postStream` invoker (`actingAs` + `post`) so this
 * stays free of TestCase inheritance. PR path binds a canned RagClientInterface.
 */
final class AskConcurrencyWaveRunner
{
    public function __construct(
        private readonly SseStreamParser $parser = new SseStreamParser,
        private readonly WaveSafetyJudge $judge = new WaveSafetyJudge,
    ) {}

    /**
     * Serial PR/CI wave (canned RAG). Not concurrent overlap proof.
     *
     * @param  array<string, User>  $principals  keys user_a / user_b
     * @param  callable(User $user, array $payload): TestResponse  $postStream
     */
    public function runSerialWave(
        AskConcurrencyFixture $fixture,
        array $principals,
        callable $postStream,
    ): WaveSafetyReport {
        $observations = [];

        foreach ($fixture->assignments() as $assignment) {
            $principalKey = $assignment['principal'];
            $user = $principals[$principalKey] ?? null;
            if (! $user instanceof User) {
                return new WaveSafetyReport([], ["missing principal {$principalKey}"]);
            }

            $runId = bin2hex(random_bytes(16));
            $question = $fixture->promptText($assignment['prompt']);

            $started = hrtime(true);
            $response = $postStream($user, [
                'question' => $question,
                'run_id' => $runId,
            ]);

            $status = $response->getStatusCode();
            $body = CapturedStreamOutput::from($response);
            $elapsedSeconds = (hrtime(true) - $started) / 1e9;
            $parsed = $this->parser->parse($body);

            $observations[] = new StreamObservation(
                role: $assignment['role'],
                principalKey: $principalKey,
                userId: (int) $user->id,
                clientRunId: $runId,
                httpStatus: $status,
                threadId: $parsed->threadId,
                exchangeId: $parsed->exchangeId,
                doneRunId: $parsed->runId,
                sawToken: $parsed->sawToken,
                sawDone: $parsed->sawDone,
                sawTerminalError: $parsed->sawTerminalError,
                hangSuspected: $this->hangSuspected($fixture, $parsed->namedEvents, $elapsedSeconds),
                namedEvents: $parsed->namedEvents,
                elapsedSeconds: $elapsedSeconds,
            );
        }

        $unavailable = Cache::get('rag:unavailable_until') !== null;

        return new WaveSafetyReport(
            $observations,
            $this->judge->failures($fixture, $observations, $unavailable),
        );
    }

    /**
     * @param  list<string>  $namedEvents
     */
    private function hangSuspected(AskConcurrencyFixture $fixture, array $namedEvents, float $elapsedSeconds): bool
    {
        return $elapsedSeconds >= $fixture->hangWindowSeconds() && $namedEvents === [];
    }
}
