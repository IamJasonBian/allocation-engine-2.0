package com.apollo.email;

import java.util.Map;

/**
 * The firing path selected per row. An implementation actually sends the
 * reply / schedules / forwards — via the interface (human-in-the-loop) or an
 * autonomous agent. Returns a result payload stored on the row's action layer
 * (e.g. {@code {"sent_message_id": "..."}}).
 */
@FunctionalInterface
public interface ResponderRouter {
    Map<String, Object> fire(EmailRowObject row) throws Exception;
}
