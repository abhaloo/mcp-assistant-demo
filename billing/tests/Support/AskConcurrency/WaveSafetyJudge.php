<?php

declare(strict_types=1);

namespace Tests\Support\AskConcurrency;

/**
 * Track S safety verdict for one C=3 wave (Success Contract criteria).
 */
final class WaveSafetyJudge
{
    /**
     * @param  list<StreamObservation>  $observations
     * @return list<string> failure reasons (empty = pass)
     */
    public function failures(AskConcurrencyFixture $fixture, array $observations, bool $unavailableArmed): array
    {
        $failures = [];

        if (count($observations) !== count($fixture->assignments())) {
            $failures[] = 'wave observation count does not match fixture assignment';
        }

        $fiveXx = 0;
        foreach ($observations as $obs) {
            if ($obs->httpStatus >= 500) {
                $fiveXx++;
            }
            if ($obs->httpStatus === 429) {
                $failures[] = "setup: ai-api 429 on role {$obs->role}";
            }
            if (in_array($obs->httpStatus, [401, 403, 404, 422], true)) {
                $failures[] = "setup: HTTP {$obs->httpStatus} on role {$obs->role}";
            }

            $pastDeadline = $obs->elapsedSeconds >= $fixture->proxyDeadlineSeconds();
            $setupOrStormHttp = $obs->httpStatus >= 500 || $obs->httpStatus === 429;
            if (! $obs->reachedTerminal() && ($pastDeadline || ! $setupOrStormHttp)) {
                $failures[] = "no terminal done/error within deadline for role {$obs->role}";
            }

            if ($obs->hangSuspected) {
                $failures[] = "hang: only heartbeat/silence past window for role {$obs->role}";
            }
        }

        if ($fiveXx >= $fixture->stormMinStreams5xx()) {
            $failures[] = "5xx storm: {$fiveXx} streams";
        }

        if ($unavailableArmed) {
            $failures[] = 'cascade: rag unavailable cooldown armed';
        }

        return array_values(array_unique([
            ...$failures,
            ...$this->crossTalkFailures($observations),
        ]));
    }

    /**
     * @param  list<StreamObservation>  $observations
     * @return list<string>
     */
    private function crossTalkFailures(array $observations): array
    {
        $failures = [];
        $allExchange = [];
        $allRun = [];
        $threadOwner = [];

        foreach ($observations as $obs) {
            if ($obs->exchangeId !== null) {
                if (isset($allExchange[$obs->exchangeId])) {
                    $failures[] = "cross-talk or overlap: duplicate exchange_id {$obs->exchangeId}";
                }
                $allExchange[$obs->exchangeId] = $obs->role;
            }

            if ($obs->threadId !== null) {
                $owner = $threadOwner[$obs->threadId] ?? null;
                if ($owner !== null && $owner !== $obs->principalKey) {
                    $failures[] = "cross-talk: thread_id {$obs->threadId} shared across principals";
                }
                $threadOwner[$obs->threadId] = $obs->principalKey;
            }

            if ($obs->doneRunId !== null) {
                if (isset($allRun[$obs->doneRunId]) && $allRun[$obs->doneRunId] !== $obs->role) {
                    $failures[] = "cross-talk: run_id {$obs->doneRunId} on multiple roles";
                }
                $allRun[$obs->doneRunId] = $obs->role;
            }

            if ($obs->doneRunId !== null && $obs->doneRunId !== $obs->clientRunId) {
                $failures[] = "run_id mismatch for role {$obs->role}: client {$obs->clientRunId} vs done {$obs->doneRunId}";
            }
        }

        return $failures;
    }
}
