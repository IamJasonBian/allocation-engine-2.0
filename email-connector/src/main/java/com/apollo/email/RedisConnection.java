package com.apollo.email;

import java.net.URI;
import java.time.Duration;

import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

/**
 * The connection. Owns a pooled Jedis client built from the same environment
 * the Python engine uses (see {@code app/redis_store.py::_get_client}):
 *
 * <ol>
 *   <li>{@code REDIS_HOST} (+ optional {@code :port}) and {@code REDIS_PASSWORD}</li>
 *   <li>else {@code REDIS_URL}</li>
 *   <li>else not configured &rarr; {@link #isConfigured()} is false</li>
 * </ol>
 *
 * <p>Pooled because the connector fans out across worker threads/virtual
 * threads — each borrows a resource with {@link #jedis()} in try-with-resources.
 */
public final class RedisConnection implements AutoCloseable {

    private static final int DEFAULT_PORT = 6379;

    private final JedisPool pool;

    private RedisConnection(JedisPool pool) {
        this.pool = pool;
    }

    /** Build from env, or return a not-configured instance (pool == null). */
    public static RedisConnection fromEnv() {
        return new RedisConnection(buildPool(System.getenv()));
    }

    /** Build from an explicit map (handy for tests). */
    public static RedisConnection from(java.util.Map<String, String> env) {
        return new RedisConnection(buildPool(env));
    }

    private static JedisPool buildPool(java.util.Map<String, String> env) {
        JedisPoolConfig cfg = new JedisPoolConfig();
        cfg.setMaxTotal(16);
        cfg.setMaxIdle(8);
        cfg.setMinIdle(1);
        cfg.setTestOnBorrow(true);

        String host = trimToNull(env.get("REDIS_HOST"));
        if (host != null) {
            int port = DEFAULT_PORT;
            int colon = host.lastIndexOf(':');
            if (colon > -1) {
                try {
                    port = Integer.parseInt(host.substring(colon + 1));
                    host = host.substring(0, colon);
                } catch (NumberFormatException ignored) {
                    // leave host/port as-is, matching the Python fallback
                }
            }
            String password = trimToNull(env.get("REDIS_PASSWORD"));
            return new JedisPool(cfg, host, port, (int) Duration.ofSeconds(5).toMillis(), password);
        }

        String url = trimToNull(env.get("REDIS_URL"));
        if (url != null) {
            return new JedisPool(cfg, URI.create(url), (int) Duration.ofSeconds(5).toMillis());
        }

        return null; // not configured
    }

    public boolean isConfigured() {
        return pool != null;
    }

    /** Borrow a connection from the pool. Close it (try-with-resources) to return it. */
    public Jedis jedis() {
        if (pool == null) {
            throw new IllegalStateException(
                    "Redis not configured (set REDIS_HOST/REDIS_PASSWORD or REDIS_URL)");
        }
        return pool.getResource();
    }

    @Override
    public void close() {
        if (pool != null) {
            pool.close();
        }
    }

    private static String trimToNull(String s) {
        if (s == null) {
            return null;
        }
        String t = s.trim();
        return t.isEmpty() ? null : t;
    }
}
