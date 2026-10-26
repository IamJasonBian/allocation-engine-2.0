package com.apollo.email;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/** What the routing layer decided to do — drives the responder. */
public enum NextStep {
    SEND_REPLY("send_reply"),
    DRAFT_REPLY("draft_reply"),
    SCHEDULE("schedule"),
    FORWARD("forward"),
    HUMAN_REVIEW("human_review"),
    NO_ACTION("no_action");

    private final String value;

    NextStep(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    @JsonCreator
    public static NextStep from(String v) {
        for (NextStep e : values()) {
            if (e.value.equals(v)) {
                return e;
            }
        }
        return NO_ACTION;
    }
}
