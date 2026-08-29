package com.apollo.email;

/**
 * The classifier layer: takes a raw row + its message history and writes the
 * four derived fields (intent, confidence, thread context, next step) via
 * {@link EmailRowObject#withClassification}.
 *
 * <p>Pluggable on purpose — start rule-based, swap in a Claude-backed
 * implementation later without touching storage or routing.
 */
public interface EmailClassifier {
    EmailRowObject classify(EmailRowObject row, ThreadContext history);
}
