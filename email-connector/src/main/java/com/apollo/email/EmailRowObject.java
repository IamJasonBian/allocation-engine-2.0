package com.apollo.email;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * An email message carried through three layers: the raw email + payload, a
 * derived classifier layer, and an action layer mutated by the responder.
 *
 * <p>Immutable (a record): classification and response updates return a new
 * instance via {@link #withClassification} / {@link #withResponse}. JSON
 * round-trips with the Python {@code EmailRowObject} dataclass (snake_case).
 */
public record EmailRowObject(
        // ── RAW ──────────────────────────────────────────────────────────
        String rowId,
        String threadId,
        String messageId,
        String inReplyTo,
        String sender,
        List<String> recipients,
        String subject,
        String body,
        Instant receivedAt,
        Map<String, Object> payload,
        // ── CLASSIFIERS (derived, re-computable) ─────────────────────────
        EmailIntent intent,
        double intentConfidence,
        ThreadContext threadContext,
        NextStep nextStep,
        String interactionId,
        int classifierVersion,
        Instant classifiedAt,
        // ── ACTION (mutated by responder) ────────────────────────────────
        ResponseStatus responseStatus,
        Responder responder,
        Map<String, Object> responsePayload) {

    public static final int CLASSIFIER_VERSION = 1;

    /**
     * Deterministic id = thread + intent. The same conversational intent
     * collapses to one interaction, giving natural dedup / idempotency across
     * boxes (the dedup key is the id itself, not a per-process map).
     */
    public static String deriveInteractionId(String threadId, EmailIntent intent) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-1");
            byte[] d = md.digest((threadId + ":" + intent.value()).getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder("ixn_");
            for (int i = 0; i < 8; i++) {              // 8 bytes → 16 hex chars
                sb.append(String.format("%02x", d[i]));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-1 unavailable", e);
        }
    }

    /** Build the RAW layer with classifier/action fields at their defaults. */
    public static EmailRowObject raw(
            String rowId, String threadId, String messageId, String inReplyTo,
            String sender, List<String> recipients, String subject, String body,
            Instant receivedAt, Map<String, Object> payload) {
        return new EmailRowObject(
                rowId, threadId, messageId, inReplyTo, sender,
                recipients == null ? List.of() : recipients, subject, body, receivedAt,
                payload == null ? Map.of() : payload,
                EmailIntent.UNKNOWN, 0.0, null, NextStep.NO_ACTION, "",
                CLASSIFIER_VERSION, null,
                ResponseStatus.PENDING, null, Map.of());
    }

    /** Overlay the classifier layer; derives interactionId from thread+intent. */
    public EmailRowObject withClassification(
            EmailIntent intent, double confidence, ThreadContext ctx, NextStep step) {
        return new EmailRowObject(
                rowId, threadId, messageId, inReplyTo, sender, recipients, subject,
                body, receivedAt, payload,
                intent, confidence, ctx, step, deriveInteractionId(threadId, intent),
                CLASSIFIER_VERSION, Instant.now(),
                responseStatus, responder, responsePayload);
    }

    /** Overlay the action layer after the responder fires. */
    public EmailRowObject withResponse(
            ResponseStatus status, Responder by, Map<String, Object> resultPayload) {
        return new EmailRowObject(
                rowId, threadId, messageId, inReplyTo, sender, recipients, subject,
                body, receivedAt, payload,
                intent, intentConfidence, threadContext, nextStep, interactionId,
                classifierVersion, classifiedAt,
                status, by, resultPayload == null ? Map.of() : resultPayload);
    }

    public String toJson() {
        return Json.write(this);
    }

    public static EmailRowObject fromJson(String raw) {
        return Json.read(raw, EmailRowObject.class);
    }
}
