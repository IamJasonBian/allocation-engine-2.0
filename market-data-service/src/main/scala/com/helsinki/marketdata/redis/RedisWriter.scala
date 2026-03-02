package com.helsinki.marketdata.redis

import com.helsinki.marketdata.alpaca.Quote
import com.helsinki.marketdata.alpaca.Quote.given
import com.helsinki.marketdata.config.AppConfig
import io.circe.Json
import io.circe.syntax.*
import redis.clients.jedis.{JedisPool, JedisPoolConfig}

class RedisWriter(config: AppConfig):
  private val HashKey = "market-quotes"
  private val HistoryKey = "market-quotes:history"

  private val poolConfig = new JedisPoolConfig()
  poolConfig.setMaxTotal(4)
  poolConfig.setMaxIdle(2)

  private val pool = new JedisPool(
    poolConfig,
    config.redisHost,
    config.redisPort,
    5000,
    if config.redisPassword.nonEmpty then config.redisPassword else null
  )

  def writeQuotes(quotes: Seq[Quote]): Unit =
    val jedis = pool.getResource
    try
      val pipe = jedis.pipelined()

      // Write latest quote per symbol
      for q <- quotes do
        pipe.hset(HashKey, q.symbol, q.asJson.noSpaces)

      // Write metadata
      val meta = Json.obj(
        "updated_at"       -> Json.fromString(java.time.Instant.now.toString),
        "poll_interval_ms" -> Json.fromLong(config.pollIntervalMs),
        "symbols"          -> Json.fromValues(quotes.map(q => Json.fromString(q.symbol)))
      )
      pipe.hset(HashKey, "_meta", meta.noSpaces)

      // Push snapshot into history list
      val snapshot = Json.obj(
        "timestamp" -> Json.fromString(java.time.Instant.now.toString),
        "quotes"    -> Json.fromValues(quotes.map(_.asJson))
      )
      pipe.lpush(HistoryKey, snapshot.noSpaces)
      pipe.ltrim(HistoryKey, 0, config.historyMaxSize - 1)

      pipe.sync()
    finally
      jedis.close()

  def ping(): Boolean =
    val jedis = pool.getResource
    try
      jedis.ping() == "PONG"
    catch
      case _: Exception => false
    finally
      jedis.close()

  def close(): Unit = pool.close()
