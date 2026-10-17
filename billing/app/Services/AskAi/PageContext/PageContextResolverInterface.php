<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

use App\Models\User;

interface PageContextResolverInterface
{
    public function supports(ClientPageContext $client): bool;

    /**
     * @throws InvalidPageContextException
     * @throws StalePageContextException
     */
    public function resolve(ClientPageContext $client, User $user): TrustedPageContext;
}
