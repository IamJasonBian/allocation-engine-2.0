package com.helsinki.marketdata.alpaca

import com.helsinki.marketdata.config.AppConfig
import io.circe.parser.*
import sttp.client3.*
import java.time.Instant

class AlpacaClient(config: AppConfig):
  private val backend = HttpClientSyncBackend()

  private val stockBaseUrl = "https://data.alpaca.markets/v2/stocks"
  private val cryptoBaseUrl = "https://data.alpaca.markets/v1beta3/crypto/us"

  def fetchStockQuote(symbol: String): Option[Quote] =
    try
      val response = basicRequest
        .get(uri"$stockBaseUrl/$symbol/quotes/latest")
        .header("APCA-API-KEY-ID", config.alpacaApiKey)
        .header("APCA-API-SECRET-KEY", config.alpacaSecretKey)
        .send(backend)

      response.body match
        case Right(body) => parseStockQuote(body, symbol)
        case Left(err) =>
          println(s"[alpaca] Stock quote error for $symbol: $err")
          None
    catch
      case e: Exception =>
        println(s"[alpaca] Stock quote exception for $symbol: ${e.getMessage}")
        None

  def fetchCryptoQuote(symbol: String): Option[Quote] =
    try
      val response = basicRequest
        .get(uri"$cryptoBaseUrl/latest/quotes?symbols=$symbol")
        .header("APCA-API-KEY-ID", config.alpacaApiKey)
        .header("APCA-API-SECRET-KEY", config.alpacaSecretKey)
        .send(backend)

      response.body match
        case Right(body) => parseCryptoQuote(body, symbol)
        case Left(err) =>
          println(s"[alpaca] Crypto quote error for $symbol: $err")
          None
    catch
      case e: Exception =>
        println(s"[alpaca] Crypto quote exception for $symbol: ${e.getMessage}")
        None

  private def parseStockQuote(body: String, symbol: String): Option[Quote] =
    parse(body).toOption.flatMap { json =>
      val c = json.hcursor.downField("quote")
      for
        bp <- c.get[Double]("bp").toOption
        ap <- c.get[Double]("ap").toOption
      yield
        val bs = c.get[Int]("bs").toOption
        val as_ = c.get[Int]("as").toOption
        val bx = c.get[String]("bx").toOption
        val ax = c.get[String]("ax").toOption
        val mid = (bp + ap) / 2.0
        val spread = ap - bp
        val spreadBps = if mid > 0 then (spread / mid) * 10000.0 else 0.0
        Quote(bp, ap, mid, spread, math.round(spreadBps * 100.0) / 100.0,
              bs, as_, bx, ax, Instant.now.toString, "alpaca", symbol, "stock")
    }

  private def parseCryptoQuote(body: String, symbol: String): Option[Quote] =
    parse(body).toOption.flatMap { json =>
      val c = json.hcursor.downField("quotes").downField(symbol)
      for
        bp <- c.get[Double]("bp").toOption
        ap <- c.get[Double]("ap").toOption
      yield
        val bs = c.get[Double]("bs").toOption.map(_.toInt)
        val as_ = c.get[Double]("as").toOption.map(_.toInt)
        val mid = (bp + ap) / 2.0
        val spread = ap - bp
        val spreadBps = if mid > 0 then (spread / mid) * 10000.0 else 0.0
        Quote(bp, ap, mid, spread, math.round(spreadBps * 100.0) / 100.0,
              bs, as_, None, None, Instant.now.toString, "alpaca", symbol, "crypto")
    }

  def close(): Unit = backend.close()
