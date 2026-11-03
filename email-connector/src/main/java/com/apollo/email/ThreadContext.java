package com.apollo.email;

import java.time.Instant;
import java.util.List;

/**
 * Message history — the "previous threads" classifier. Captures the
 * conversation a row belongs to so the responder has context.
 *
 * @param threadId     provider thread id (e.g. Gmail threadId)
 * @param priorRowIds  earlier rows in the thread, oldest &rarr; newest
 * @param messageCount total messages seen in the thread
 * @param summary      rolling summary of the conversation so far
 * @param lastOutboundAt when we last replied (null if never)
 */
public record ThreadContext(
        String threadId,
        List<String> priorRowIds,
        int messageCount,
        String summary,
        Instant lastOutboundAt) {

    public ThreadContext {
        priorRowIds = priorRowIds == null ? List.of() : List.copyOf(priorRowIds);
    }

    /** An empty context for the first message in a thread. */
    public static ThreadContext fresh(String threadId) {
        return new ThreadContext(threadId, List.of(), 0, "", null);
    }
}
