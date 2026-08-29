# email-connector (Java)

A Java connector for `EmailRowObject` — an email + a classifier layer (intent,
message history, next step) + an action layer keyed by a deterministic
`interaction_id`. It reads/writes the **same Redis keys** the Python allocation
engine uses, so either language can produce or consume rows.

## Layout

| File | Role |
|------|------|
| `EmailRowObject`, `ThreadContext` | domain model (records, immutable) |
| `EmailIntent`, `NextStep`, `ResponseStatus`, `Responder` | classifier/lifecycle enums |
| `EmailClassifier` + `RuleBasedClassifier` | the classifier layer (pluggable; swap in Claude later) |
| `RedisConnection` | **the connection** — pooled Jedis from `REDIS_HOST`/`REDIS_PASSWORD` or `REDIS_URL` |
| `EmailRowConnector` | **the connector** — store + stream adapter (ingest / load / consume) |
| `ResponderRouter` | the firing path (interface or agent) |

## Redis contract (shared with Python)

```
emails          hash    rowId        → EmailRowObject JSON (snake_case)
email.inbound   stream  XADD         → {row_id, interaction_id, intent, next_step}
fired:<ixn>     string  SET NX EX    → distributed dedup on interaction_id
```

JSON is snake_case with ISO-8601 instants, matching the Python `to_redis()`
output, so `EmailRowObject.fromJson(...)` round-trips rows written by either side.

## Flow

```
ingest()  →  email.inbound stream  →  consume()  →  ResponderRouter.fire()
   ▲                                      │
classify (intent, history, next_step)     └─ SET NX on interaction_id → idempotent
```

Delivery is **at-least-once** (rows stay pending until `XACK`); the deterministic
`interaction_id` makes a duplicate a no-op — the second consumer loses the
`SET NX` race. See the parent design notes for the single-box (in-memory
`BlockingQueue` + `ConcurrentHashMap`) vs multi-box (this) tradeoff.

## Run

```bash
cd email-connector
mvn -q compile exec:java            # offline demo if Redis unset; full path if set
mvn -q test                         # round-trip + idempotency tests
REDIS_HOST=localhost mvn -q exec:java
```
