<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

use App\Models\User;

/**
 * Structural allowlist registry — no reflection, no fallback resolver.
 */
final class PageContextResolverRegistry
{
    /**
     * @param  list<PageContextResolverInterface>  $resolvers
     */
    public function __construct(
        private readonly array $resolvers,
    ) {}

    /**
     * @throws InvalidPageContextException
     * @throws StalePageContextException
     */
    public function resolve(ClientPageContext $client, User $user): TrustedPageContext
    {
        foreach ($this->resolvers as $resolver) {
            if ($resolver->supports($client)) {
                return $resolver->resolve($client, $user);
            }
        }

        throw new InvalidPageContextException('Unsupported page_context route tuple.');
    }
}
