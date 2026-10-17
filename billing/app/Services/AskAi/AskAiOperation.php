<?php

declare(strict_types=1);

namespace App\Services\AskAi;

enum AskAiOperation: string
{
    case NewQuestion = 'new_question';
    case Regenerate = 'regenerate';
    case ClarificationReply = 'clarification_reply';
    case ResultPage = 'result_page';
}
