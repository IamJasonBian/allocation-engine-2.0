package com.apollo.email;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/** Lifecycle of the outbound interaction keyed by interaction_id. */
public enum ResponseStatus {
    PENDING("pending"),
    DRAFTED("drafted"),
    SENT("sent"),
    FAILED("failed"),
    SKIPPED("skipped");

    private final String value;

    ResponseStatus(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    @JsonCreator
    public static ResponseStatus from(String v) {
        for (ResponseStatus e : values()) {
            if (e.value.equals(v)) {
                return e;
            }
        }
        return PENDING;
    }
}
