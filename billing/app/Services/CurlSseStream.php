<?php

declare(strict_types=1);

namespace App\Services;

use Psr\Http\Message\StreamInterface;
use RuntimeException;

/**
 * Read-only PSR-7 stream that pulls an HTTP response body incrementally from a
 * cURL multi handle.
 *
 * WHY THIS EXISTS: PHP's pull-based HTTP clients buffer a chunked/SSE body to
 * completion before the first read() returns — verified for both Guzzle's
 * `stream => true` and the native `fopen()` http wrapper (see the stream-probe
 * diagnostics). cURL's *push* WRITEFUNCTION is the only mechanism that delivers
 * chunks as they arrive off the socket. This class bridges that push source back
 * to the pull `read()` the SSE proxy controller consumes, so tokens reach the
 * browser one at a time instead of in a single end-of-stream burst.
 *
 * Contract preserved: {@see open()} pumps only until the response status line is
 * parsed, so the caller still learns the HTTP status *before* committing a
 * streamed response and can return a JSON error for a non-2xx upstream.
 */
class CurlSseStream implements StreamInterface
{
    /** @var \CurlMultiHandle|null */
    private $mh = null;

    /** @var \CurlHandle|null */
    private $ch = null;

    private string $buffer = '';

    private int $running = 1;

    private bool $closed = false;

    /**
     * @param  list<string>  $headers  raw "Name: value" header lines
     */
    public function __construct(
        private readonly string $url,
        private readonly array $headers,
        private readonly string $body,
        private readonly int $connectTimeout = 5,
        private readonly int $timeout = 90,
        private readonly ?int $readDeadlineAtMs = null,
    ) {}

    /**
     * Connect and pump until the HTTP status is known (headers parsed) or the
     * transfer ends. Returns the upstream status code, or 0 if no response was
     * received at all (DNS/TLS/connect failure — caller should treat as down).
     */
    public function open(): int
    {
        $this->mh = curl_multi_init();
        $this->ch = curl_init();

        curl_setopt_array($this->ch, [
            CURLOPT_URL => $this->url,
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $this->body,
            CURLOPT_HTTPHEADER => $this->headers,
            CURLOPT_CONNECTTIMEOUT => $this->connectTimeout,
            CURLOPT_TIMEOUT => $this->timeout,
            // Refuse compression: a decoding stream would re-introduce buffering.
            CURLOPT_ENCODING => 'identity',
            // Push every received chunk into the buffer read() drains.
            CURLOPT_WRITEFUNCTION => function ($ch, string $data): int {
                $this->buffer .= $data;

                return strlen($data);
            },
        ]);

        curl_multi_add_handle($this->mh, $this->ch);

        do {
            $mrc = curl_multi_exec($this->mh, $this->running);
            $status = (int) curl_getinfo($this->ch, CURLINFO_RESPONSE_CODE);
            if ($status !== 0) {
                return $status;
            }
            if ($this->running > 0 && curl_multi_select($this->mh, 1.0) === -1) {
                usleep(1000);
            }
        } while ($this->running > 0 && $mrc === CURLM_OK);

        return (int) curl_getinfo($this->ch, CURLINFO_RESPONSE_CODE);
    }

    /** cURL error string for the handle (empty when none / already closed). */
    public function errorMessage(): string
    {
        return $this->ch !== null ? curl_error($this->ch) : '';
    }

    public function read(int $length): string
    {
        if ($length <= 0 || $this->closed) {
            return '';
        }

        if ($this->readDeadlineAtMs !== null && $this->nowMs() >= $this->readDeadlineAtMs) {
            return '';
        }

        // Return empty after one select round so the caller can check for a
        // client abort between upstream chunks.
        while ($this->buffer === '' && $this->running > 0) {
            if ($this->readDeadlineAtMs !== null && $this->nowMs() >= $this->readDeadlineAtMs) {
                return '';
            }

            $selectTimeout = 0.5;
            if ($this->readDeadlineAtMs !== null) {
                $remainingMs = $this->readDeadlineAtMs - $this->nowMs();
                if ($remainingMs <= 0) {
                    return '';
                }
                $selectTimeout = min(0.5, max(0.001, $remainingMs / 1000));
            }

            if ($this->select($selectTimeout) === -1) {
                usleep(1000);
            }
            $this->exec();

            if ($this->buffer === '') {
                break;
            }
        }

        $chunk = substr($this->buffer, 0, $length);
        $this->buffer = substr($this->buffer, $length);

        return $chunk;
    }

    /** Wait for socket activity; overridable so tests can script a timeout. */
    protected function select(float $timeout): int
    {
        return curl_multi_select($this->mh, $timeout);
    }

    /** Pump the transfer once; overridable so tests can run without a handle. */
    protected function exec(): void
    {
        if ($this->mh !== null) {
            curl_multi_exec($this->mh, $this->running);
        }
    }

    private function nowMs(): int
    {
        return (int) floor(microtime(true) * 1000);
    }

    public function eof(): bool
    {
        return $this->closed || ($this->buffer === '' && $this->running <= 0);
    }

    public function close(): void
    {
        if ($this->closed) {
            return;
        }
        $this->closed = true;

        if ($this->ch !== null) {
            if ($this->mh !== null) {
                curl_multi_remove_handle($this->mh, $this->ch);
            }
            curl_close($this->ch);
            $this->ch = null;
        }
        if ($this->mh !== null) {
            curl_multi_close($this->mh);
            $this->mh = null;
        }
    }

    public function __destruct()
    {
        $this->close();
    }

    public function getContents(): string
    {
        $out = '';
        while (! $this->eof()) {
            $out .= $this->read(8192);
        }

        return $out;
    }

    public function __toString(): string
    {
        try {
            return $this->getContents();
        } catch (\Throwable) {
            return '';
        }
    }

    public function detach()
    {
        $this->close();

        return null;
    }

    public function getSize(): ?int
    {
        return null;
    }

    public function isReadable(): bool
    {
        return ! $this->closed;
    }

    public function isWritable(): bool
    {
        return false;
    }

    public function isSeekable(): bool
    {
        return false;
    }

    public function tell(): int
    {
        throw new RuntimeException('CurlSseStream: tell() is not supported.');
    }

    public function seek(int $offset, int $whence = SEEK_SET): void
    {
        throw new RuntimeException('CurlSseStream: not seekable.');
    }

    public function rewind(): void
    {
        throw new RuntimeException('CurlSseStream: not seekable.');
    }

    public function write(string $string): int
    {
        throw new RuntimeException('CurlSseStream: not writable.');
    }

    public function getMetadata(?string $key = null)
    {
        return $key === null ? [] : null;
    }
}
