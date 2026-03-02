package com.helsinki.marketdata

import com.helsinki.marketdata.config.AppConfig
import com.helsinki.marketdata.poller.QuotePoller

@main def run(): Unit =
  println("[market-data] Initializing Alpaca -> Redis market data service")
  val config = AppConfig.fromEnv()

  if config.alpacaApiKey.isEmpty then
    System.err.println("[market-data] FATAL: ALPACA_API_KEY must be set")
    sys.exit(1)

  val poller = QuotePoller(config)

  Runtime.getRuntime.addShutdownHook(new Thread(() => {
    println("\n[market-data] Shutting down...")
    poller.stop()
  }))

  poller.start()
