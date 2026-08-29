package com.apollo.email;

import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * End-to-end demo. Classifies a sample email, then:
 *   - if Redis is configured, ingests it and drains one batch through a stub
 *     responder (the multi-box path);
 *   - otherwise just prints the classified row JSON (works offline).
 */
public final class Main {

    public static void main(String[] args) {
        EmailClassifier classifier = new RuleBasedClassifier();

        EmailRowObject raw = EmailRowObject.raw(
                "msg_1001", "thread_42", "<abc@mail.gmail.com>", null,
                "alice@example.com", List.of("ops@apollo.dev"),
                "Can you approve the allocation change?",
                "Hi — could you please review and approve the change ASAP? Thanks.",
                Instant.now(), Map.of("labels", List.of("INBOX", "IMPORTANT")));

        EmailRowObject classified = classifier.classify(raw, ThreadContext.fresh("thread_42"));

        System.out.println("intent=" + classified.intent().value()
                + " confidence=" + classified.intentConfidence()
                + " next_step=" + classified.nextStep().value()
                + " interaction_id=" + classified.interactionId());
        System.out.println("row JSON: " + classified.toJson());

        try (RedisConnection conn = RedisConnection.fromEnv()) {
            if (!conn.isConfigured()) {
                System.out.println("[redis] not configured — skipping ingest/consume demo");
                return;
            }
            EmailRowConnector connector = new EmailRowConnector(conn);
            connector.ingest(classified);
            System.out.println("[redis] ingested " + classified.rowId());

            // Stub responder stands in for the interface / agent firing path.
            ResponderRouter stub = row -> {
                System.out.println("[respond] firing for interaction " + row.interactionId()
                        + " (" + row.nextStep().value() + ")");
                return Map.of("sent_message_id", "<reply-" + row.rowId() + ">");
            };
            int n = connector.runOnce("box-1", stub, 16, 2_000);
            System.out.println("[redis] processed " + n + " row(s)");

            EmailRowObject after = connector.load(classified.rowId());
            System.out.println("[redis] status=" + after.responseStatus().value()
                    + " responder=" + (after.responder() == null ? "-" : after.responder().value()));
        }
    }

    private Main() {}
}
