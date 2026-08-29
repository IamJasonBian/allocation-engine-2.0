package com.apollo.email;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

class EmailRowObjectTest {

    private EmailRowObject sample() {
        return EmailRowObject.raw(
                "msg_1", "thread_1", "<m1@x>", null,
                "bob@example.com", List.of("ops@apollo.dev"),
                "Please review", "Could you review this ASAP?",
                Instant.parse("2026-06-18T12:00:00Z"),
                Map.of("k", "v"));
    }

    @Test
    void jsonRoundTrips() {
        EmailRowObject row = new RuleBasedClassifier()
                .classify(sample(), ThreadContext.fresh("thread_1"));
        EmailRowObject back = EmailRowObject.fromJson(row.toJson());
        assertEquals(row, back);
    }

    @Test
    void jsonUsesSnakeCaseForPythonCompat() {
        String json = sample().toJson();
        assertTrue(json.contains("\"row_id\""), json);
        assertTrue(json.contains("\"interaction_id\""), json);
        assertTrue(json.contains("\"next_step\""), json);
    }

    @Test
    void interactionIdIsDeterministicPerThreadAndIntent() {
        String a = EmailRowObject.deriveInteractionId("thread_1", EmailIntent.REPLY_NEEDED);
        String b = EmailRowObject.deriveInteractionId("thread_1", EmailIntent.REPLY_NEEDED);
        String c = EmailRowObject.deriveInteractionId("thread_1", EmailIntent.MEETING_REQUEST);
        assertEquals(a, b);                 // same thread+intent → same id (dedup)
        assertNotEquals(a, c);              // different intent → different interaction
        assertTrue(a.startsWith("ixn_"));
        assertEquals("ixn_".length() + 16, a.length());
    }

    @Test
    void routingIsDeterministicPolicyMap() {
        assertEquals(NextStep.SCHEDULE, RuleBasedClassifier.routeFor(EmailIntent.MEETING_REQUEST));
        assertEquals(NextStep.HUMAN_REVIEW, RuleBasedClassifier.routeFor(EmailIntent.ESCALATION));
        assertEquals(NextStep.NO_ACTION, RuleBasedClassifier.routeFor(EmailIntent.SPAM));
    }
}
