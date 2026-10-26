package com.apollo.email;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import redis.clients.jedis.Jedis;
import redis.clients.jedis.StreamEntryID;
import redis.clients.jedis.resps.StreamEntry;
import redis.clients.jedis.exceptions.JedisDataException;
import redis.clients.jedis.params.SetParams;
import redis.clients.jedis.params.XReadGroupParams;

/**
 * The connector. Bridges {@link EmailRowObject} to the same Redis keys the
 * Python engine uses, so either language can produce or consume rows:
 *
 * <pre>
 *   emails          hash    rowId          → EmailRowObject JSON
 *   emails  field   _meta                  → {updated_at, last_row_id}
 *   email.inbound   stream  XADD           → {row_id, interaction_id, intent, next_step}
 *   fired:&lt;ixn&gt;       string  SET NX EX      → distributed dedup on interaction_id
 * </pre>
 *
 * <p>Producer side: {@link #ingest}. Consumer side: {@link #consume} reads the
 * stream as a group (one consumer per box), claims each interaction with
 * {@code SET NX}, fires via the {@link ResponderRouter}, and acks — at-least-once
 * delivery made idempotent by the deterministic interaction id.
 */
public final class EmailRowConnector {

    public static final String EMAILS_HASH = "emails";
    public static final String META_FIELD = "_meta";
    public static final String INBOUND_STREAM = "email.inbound";
    public static final String DEFAULT_GROUP = "responders";
    private static final String FIRED_PREFIX = "fired:";
    private static final int FIRED_TTL_SEC = 86_400;

    private final RedisConnection conn;
    private final String group;
    private volatile boolean running;

    public EmailRowConnector(RedisConnection conn) {
        this(conn, DEFAULT_GROUP);
    }

    public EmailRowConnector(RedisConnection conn, String group) {
        this.conn = conn;
        this.group = group;
    }

    // ── Producer side ────────────────────────────────────────────────────

    /** Persist a classified row and publish it to the inbound stream. */
    public void ingest(EmailRowObject row) {
        try (Jedis j = conn.jedis()) {
            save(j, row);
            if (row.nextStep() != NextStep.NO_ACTION) {
                Map<String, String> fields = new LinkedHashMap<>();
                fields.put("row_id", row.rowId());
                fields.put("interaction_id", row.interactionId());
                fields.put("intent", row.intent().value());
                fields.put("next_step", row.nextStep().value());
                j.xadd(INBOUND_STREAM, StreamEntryID.NEW_ENTRY, fields);
            }
        }
    }

    public EmailRowObject load(String rowId) {
        try (Jedis j = conn.jedis()) {
            String raw = j.hget(EMAILS_HASH, rowId);
            return raw == null ? null : EmailRowObject.fromJson(raw);
        }
    }

    private void save(Jedis j, EmailRowObject row) {
        j.hset(EMAILS_HASH, row.rowId(), row.toJson());
        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("updated_at", Instant.now().toString());
        meta.put("last_row_id", row.rowId());
        j.hset(EMAILS_HASH, META_FIELD, Json.write(meta));
    }

    // ── Consumer side ────────────────────────────────────────────────────

    /** Create the consumer group if absent (idempotent). */
    public void ensureGroup() {
        try (Jedis j = conn.jedis()) {
            try {
                j.xgroupCreate(INBOUND_STREAM, group, new StreamEntryID("0-0"), true);
            } catch (JedisDataException e) {
                if (!String.valueOf(e.getMessage()).contains("BUSYGROUP")) {
                    throw e;
                }
            }
        }
    }

    /**
     * Block until stopped, reading the stream as {@code consumerName} and firing
     * each row through {@code router}. Idempotent across boxes via {@code SET NX}
     * on the interaction id; rows are acked whether we win the claim or not, and
     * a failed send releases the claim so a retry can re-win.
     */
    public void consume(String consumerName, ResponderRouter router) {
        ensureGroup();
        running = true;
        while (running) {
            runOnce(consumerName, router, 16, 5_000);
        }
    }

    public void stop() {
        running = false;
    }

    /** One read batch. Returns the number of stream entries processed. */
    public int runOnce(String consumerName, ResponderRouter router, int count, int blockMs) {
        try (Jedis j = conn.jedis()) {
            var streams = Map.of(INBOUND_STREAM, StreamEntryID.UNRECEIVED_ENTRY);
            List<Map.Entry<String, List<StreamEntry>>> batch = j.xreadGroup(
                    group, consumerName,
                    XReadGroupParams.xReadGroupParams().count(count).block(blockMs),
                    streams);
            if (batch == null || batch.isEmpty()) {
                return 0;
            }
            int processed = 0;
            for (var stream : batch) {
                for (StreamEntry entry : stream.getValue()) {
                    handleEntry(j, entry, router);
                    j.xack(INBOUND_STREAM, group, entry.getID());
                    processed++;
                }
            }
            return processed;
        }
    }

    private void handleEntry(Jedis j, StreamEntry entry, ResponderRouter router) {
        String rowId = entry.getFields().get("row_id");
        if (rowId == null) {
            return;
        }
        String raw = j.hget(EMAILS_HASH, rowId);
        if (raw == null) {
            return;
        }
        EmailRowObject row = EmailRowObject.fromJson(raw);

        if (row.nextStep() == NextStep.NO_ACTION) {
            save(j, row.withResponse(ResponseStatus.SKIPPED, null, Map.of()));
            return;
        }

        // Distributed dedup: whoever wins the SET NX fires; others skip.
        String firedKey = FIRED_PREFIX + row.interactionId();
        String won = j.set(firedKey, consumerTag(), SetParams.setParams().nx().ex(FIRED_TTL_SEC));
        if (won == null) {
            return; // another box already owns this interaction
        }

        try {
            Map<String, Object> result = router.fire(row);
            save(j, row.withResponse(ResponseStatus.SENT, Responder.AGENT, result));
        } catch (Exception e) {
            j.del(firedKey); // release the claim so a retry can re-win
            Map<String, Object> err = Map.of("error", String.valueOf(e.getMessage()));
            save(j, row.withResponse(ResponseStatus.FAILED, Responder.AGENT, err));
        }
    }

    private static String consumerTag() {
        return "responder-" + Thread.currentThread().getName();
    }
}
