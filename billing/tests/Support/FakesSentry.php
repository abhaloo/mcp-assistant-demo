<?php

declare(strict_types=1);

namespace Tests\Support;

use ArrayObject;
use Sentry\ClientBuilder;
use Sentry\Event;
use Sentry\SentrySdk;
use Sentry\State\Hub;
use Sentry\State\HubInterface;
use Sentry\Transport\Result;
use Sentry\Transport\ResultStatus;
use Sentry\Transport\TransportInterface;

/**
 * Swaps the active Sentry hub for one whose transport records events in memory
 * instead of sending them, so tests can assert on what would have been reported
 * without touching the network.
 */
trait FakesSentry
{
    /** @var ArrayObject<int, Event> */
    protected ArrayObject $sentryEvents;

    protected function fakeSentry(): void
    {
        $this->sentryEvents = new ArrayObject;

        $transport = new class($this->sentryEvents) implements TransportInterface
        {
            /** @param ArrayObject<int, Event> $events */
            public function __construct(private ArrayObject $events) {}

            public function send(Event $event): Result
            {
                $this->events->append($event);

                return new Result(ResultStatus::success(), $event);
            }

            public function close(?int $timeout = null): Result
            {
                return new Result(ResultStatus::success());
            }
        };

        $client = ClientBuilder::create([
            'dsn' => 'https://test@example.com/1',
            'default_integrations' => false,
        ])->setTransport($transport)->getClient();

        $hub = new Hub($client);

        SentrySdk::setCurrentHub($hub);
        $this->app->instance(HubInterface::class, $hub);
        $this->app->instance('sentry', $hub);
    }

    /** @return Event[] */
    protected function capturedSentryEvents(): array
    {
        return $this->sentryEvents->getArrayCopy();
    }

    protected function lastSentryEvent(): ?Event
    {
        $events = $this->capturedSentryEvents();

        return $events === [] ? null : end($events);
    }
}
