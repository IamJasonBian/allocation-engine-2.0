package com.helsinki.marketdata.config

case class AppConfig(
  alpacaApiKey: String,
  alpacaSecretKey: String,
  redisHost: String,
  redisPort: Int,
  redisPassword: String,
  pollIntervalMs: Long,
  symbols: Seq[String],
  cryptoSymbols: Seq[String],
  historyMaxSize: Int,
  optionsSymbols: Seq[String],
  optionsPollIntervalMs: Long
)

object AppConfig:
  def fromEnv(): AppConfig =
    val redisHostRaw = sys.env.getOrElse(
      "REDIS_HOST",
      "redis-17054.c99.us-east-1-4.ec2.cloud.redislabs.com:17054"
    )
    val (host, port) = redisHostRaw.split(":") match
      case Array(h, p) => (h, p.toIntOption.getOrElse(17054))
      case Array(h)    => (h, 17054)
      case _           => (redisHostRaw, 17054)

    AppConfig(
      alpacaApiKey = sys.env.getOrElse("ALPACA_API_KEY", ""),
      alpacaSecretKey = sys.env.getOrElse("ALPACA_SECRET_KEY", ""),
      redisHost = host,
      redisPort = port,
      redisPassword = sys.env.getOrElse("REDIS_PASSWORD", ""),
      pollIntervalMs = sys.env.getOrElse("POLL_INTERVAL_MS", "3000").toLong,
      symbols = sys.env.getOrElse("SYMBOLS", "BTC").split(",").map(_.trim).toSeq,
      cryptoSymbols = sys.env.getOrElse("CRYPTO_SYMBOLS", "BTC/USD").split(",").map(_.trim).toSeq,
      historyMaxSize = sys.env.getOrElse("HISTORY_MAX_SIZE", "10000").toInt,
      optionsSymbols = sys.env.getOrElse("OPTIONS_SYMBOLS", "IWN,CRWD").split(",").map(_.trim).toSeq,
      optionsPollIntervalMs = sys.env.getOrElse("OPTIONS_POLL_INTERVAL_MS", "30000").toLong
    )
