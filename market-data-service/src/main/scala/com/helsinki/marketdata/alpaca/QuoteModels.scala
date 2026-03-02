package com.helsinki.marketdata.alpaca

import io.circe.*
import io.circe.syntax.*

case class Quote(
  bid: Double,
  ask: Double,
  mid: Double,
  spread: Double,
  spreadBps: Double,
  bidSize: Option[Int],
  askSize: Option[Int],
  bidExchange: Option[String],
  askExchange: Option[String],
  timestamp: String,
  source: String,
  symbol: String,
  assetClass: String
)

object Quote:
  given Encoder[Quote] = Encoder.instance { q =>
    Json.obj(
      "bid"          -> Json.fromDoubleOrNull(q.bid),
      "ask"          -> Json.fromDoubleOrNull(q.ask),
      "mid"          -> Json.fromDoubleOrNull(q.mid),
      "spread"       -> Json.fromDoubleOrNull(q.spread),
      "spread_bps"   -> Json.fromDoubleOrNull(q.spreadBps),
      "bid_size"     -> q.bidSize.fold(Json.Null)(Json.fromInt),
      "ask_size"     -> q.askSize.fold(Json.Null)(Json.fromInt),
      "bid_exchange" -> q.bidExchange.fold(Json.Null)(Json.fromString),
      "ask_exchange" -> q.askExchange.fold(Json.Null)(Json.fromString),
      "timestamp"    -> Json.fromString(q.timestamp),
      "source"       -> Json.fromString(q.source),
      "symbol"       -> Json.fromString(q.symbol),
      "asset_class"  -> Json.fromString(q.assetClass)
    )
  }
