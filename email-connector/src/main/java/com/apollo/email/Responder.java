package com.apollo.email;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/** Who fires the response. */
public enum Responder {
    INTERFACE("interface"),
    AGENT("agent"),
    HUMAN("human");

    private final String value;

    Responder(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    @JsonCreator
    public static Responder from(String v) {
        for (Responder e : values()) {
            if (e.value.equals(v)) {
                return e;
            }
        }
        return null;
    }
}
