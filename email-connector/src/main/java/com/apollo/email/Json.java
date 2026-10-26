package com.apollo.email;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.fasterxml.jackson.module.paramnames.ParameterNamesModule;

/**
 * Shared {@link ObjectMapper}, configured to match the Python side's
 * {@code to_redis()} output:
 * <ul>
 *   <li>snake_case property names (Java {@code rowId} &harr; JSON {@code row_id})</li>
 *   <li>{@link java.time.Instant} as ISO-8601 strings, not epoch numbers</li>
 *   <li>nulls retained, so Python {@code cls(**d)} sees every field</li>
 * </ul>
 */
public final class Json {

    public static final ObjectMapper MAPPER = JsonMapper.builder()
            .addModule(new JavaTimeModule())
            .addModule(new ParameterNamesModule())
            .propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .build();

    private Json() {}

    public static String write(Object value) {
        try {
            return MAPPER.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("JSON serialize failed", e);
        }
    }

    public static <T> T read(String raw, Class<T> type) {
        try {
            return MAPPER.readValue(raw, type);
        } catch (Exception e) {
            throw new IllegalStateException("JSON deserialize failed: " + raw, e);
        }
    }
}
