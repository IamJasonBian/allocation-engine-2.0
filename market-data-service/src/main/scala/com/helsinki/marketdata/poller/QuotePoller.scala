package com.helsinki.marketdata.poller

import com.helsinki.marketdata.alpaca.AlpacaClient
import com.helsinki.marketdata.config.AppConfig
import com.helsinki.marketdata.redis.RedisWriter

class QuotePoller(config: AppConfig):
  private val alpaca = AlpacaClient(config)
  private val redis = RedisWriter(config)
  @volatile private var running = true

  def start(): Unit =
    println(s"[market-data] Starting poller")
    println(s"  stocks:   ${config.symbols.mkString(", ")}")
    println(s"  crypto:   ${config.cryptoSymbols.mkString(", ")}")
    println(s"  interval: ${config.pollIntervalMs}ms")
    println(s"  redis:    ${config.redisHost}:${config.redisPort}")

    // Verify Redis connectivity
    try
      if redis.ping() then
        println("[market-data] Redis connection OK")
      else
        println("[market-data] WARNING: Redis ping failed — will retry on writes")
    catch
      case e: Exception =>
        println(s"[market-data] WARNING: Redis ping error: ${e.getMessage} — will retry on writes")

    while running do
      try
        val stockQuotes = config.symbols.flatMap(alpaca.fetchStockQuote)
        val cryptoQuotes = config.cryptoSymbols.flatMap(alpaca.fetchCryptoQuote)
        val allQuotes = stockQuotes ++ cryptoQuotes

        if allQuotes.nonEmpty then
          val summary = allQuotes.map(q => f"${q.symbol}: $$${q.mid}%.2f (${q.spreadBps}%.1f bps)").mkString(", ")
          println(s"[market-data] Fetched ${allQuotes.size} quotes  [$summary]")
          try
            redis.writeQuotes(allQuotes)
            println(s"[market-data] Written to Redis OK")
          catch
            case e: Exception =>
              println(s"[market-data] Redis write failed: ${e.getMessage}")
        else
          println("[market-data] WARNING: no quotes received")

        Thread.sleep(config.pollIntervalMs)
      catch
        case _: InterruptedException =>
          running = false
        case e: Exception =>
          println(s"[market-data] ERROR: ${e.getMessage}")
          Thread.sleep(config.pollIntervalMs)

  def stop(): Unit =
    running = false
    alpaca.close()
    redis.close()
    println("[market-data] Stopped")
