/**
 * Client-side fallback copy for a terminal reason code.
 *
 * The server owns refusal wording and sends it on the terminal event, so this
 * file deliberately does not mirror that vocabulary — a second copy of it would
 * drift. Only codes the browser itself raises, where no server message exists,
 * are spelled out here.
 */

const CLIENT_REASON_COPY = {
    // Raised by the client when its own deadline passes before the terminal
    // event arrives, so the server never got to send a message.
    deadline_exceeded: 'That took too long to answer, so I stopped waiting. Please try again.',
    stream_error: 'The connection to the answer stream dropped. Please try again.',
    superseded: 'This request was replaced before it finished.',
};

const FALLBACK = 'I could not complete that question, and I have no answer to give for it.';

/** Copy for a reason code the server did not caption. */
export function reasonCopy(reasonCode) {
    if (reasonCode && CLIENT_REASON_COPY[reasonCode]) {
        return CLIENT_REASON_COPY[reasonCode];
    }
    return FALLBACK;
}
