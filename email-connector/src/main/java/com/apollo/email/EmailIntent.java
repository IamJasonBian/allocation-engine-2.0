package com.apollo.email;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/** What the inbound email is asking for — the primary classifier flag. */
public enum EmailIntent {
    REPLY_NEEDED("reply_needed"),
    ACTION_REQUEST("action_request"),
    MEETING_REQUEST("meeting_request"),
    INFO_ONLY("info_only"),
    ESCALATION("escalation"),
    AUTOMATED("automated"),
    SPAM("spam"),
    UNKNOWN("unknown");

    private final String value;

    EmailIntent(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    @JsonCreator
    public static EmailIntent from(String v) {
        for (EmailIntent e : values()) {
            if (e.value.equals(v)) {
                return e;
            }
        }
        return UNKNOWN;
    }
}
