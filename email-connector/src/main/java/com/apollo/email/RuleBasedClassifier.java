package com.apollo.email;

import java.util.Locale;

/**
 * A cheap keyword classifier so the pipeline runs end-to-end without an LLM.
 * {@code nextStep} is a deterministic policy map over {@code intent}, so a
 * future Claude classifier only has to nail intent + confidence while routing
 * stays auditable.
 */
public final class RuleBasedClassifier implements EmailClassifier {

    @Override
    public EmailRowObject classify(EmailRowObject row, ThreadContext history) {
        String text = (row.subject() + " " + row.body()).toLowerCase(Locale.ROOT);
        String from = row.sender() == null ? "" : row.sender().toLowerCase(Locale.ROOT);

        EmailIntent intent;
        double confidence;

        if (from.contains("no-reply") || from.contains("noreply") || from.contains("notifications@")) {
            intent = EmailIntent.AUTOMATED;
            confidence = 0.95;
        } else if (containsAny(text, "unsubscribe", "viagra", "winner", "lottery")) {
            intent = EmailIntent.SPAM;
            confidence = 0.8;
        } else if (containsAny(text, "urgent", "asap", "immediately", "outage", "down")) {
            intent = EmailIntent.ESCALATION;
            confidence = 0.7;
        } else if (containsAny(text, "meet", "calendar", "schedule", "available", "invite")) {
            intent = EmailIntent.MEETING_REQUEST;
            confidence = 0.75;
        } else if (containsAny(text, "please", "can you", "could you", "request", "need")) {
            intent = EmailIntent.ACTION_REQUEST;
            confidence = 0.65;
        } else if (text.contains("?")) {
            intent = EmailIntent.REPLY_NEEDED;
            confidence = 0.6;
        } else {
            intent = EmailIntent.INFO_ONLY;
            confidence = 0.5;
        }

        ThreadContext ctx = history != null ? history : ThreadContext.fresh(row.threadId());
        return row.withClassification(intent, confidence, ctx, routeFor(intent));
    }

    /** Policy map: intent → next step. Deterministic and easy to audit. */
    public static NextStep routeFor(EmailIntent intent) {
        return switch (intent) {
            case REPLY_NEEDED -> NextStep.DRAFT_REPLY;
            case ACTION_REQUEST -> NextStep.DRAFT_REPLY;
            case MEETING_REQUEST -> NextStep.SCHEDULE;
            case ESCALATION -> NextStep.HUMAN_REVIEW;
            case INFO_ONLY, AUTOMATED, SPAM -> NextStep.NO_ACTION;
            case UNKNOWN -> NextStep.HUMAN_REVIEW;
        };
    }

    private static boolean containsAny(String haystack, String... needles) {
        for (String n : needles) {
            if (haystack.contains(n)) {
                return true;
            }
        }
        return false;
    }
}
