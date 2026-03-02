package com.helsinki.marketdata.options

import io.circe.*
import io.circe.syntax.*

case class OptionGreeks(
  delta: Option[Double],
  gamma: Option[Double],
  theta: Option[Double],
  vega: Option[Double],
  rho: Option[Double]
)

case class OptionTrade(
  price: Double,
  size: Int,
  timestamp: String
)

case class OptionQuote(
  bid: Double,
  ask: Double,
  bidSize: Int,
  askSize: Int,
  timestamp: String
)

case class OptionSnapshot(
  symbol: String,
  latestTrade: Option[OptionTrade],
  latestQuote: Option[OptionQuote],
  greeks: Option[OptionGreeks],
  impliedVolatility: Option[Double]
)

case class OptionBar(
  timestamp: String,
  open: Double,
  high: Double,
  low: Double,
  close: Double,
  volume: Long,
  tradeCount: Int,
  vwap: Double
)

case class OptionChainEntry(
  contractSymbol: String,
  underlying: String,
  snapshot: OptionSnapshot,
  bars: Seq[OptionBar]
)

object OptionsEncoders:
  given Encoder[OptionGreeks] = Encoder.instance { g =>
    Json.obj(
      "delta" -> g.delta.fold(Json.Null)(Json.fromDoubleOrNull),
      "gamma" -> g.gamma.fold(Json.Null)(Json.fromDoubleOrNull),
      "theta" -> g.theta.fold(Json.Null)(Json.fromDoubleOrNull),
      "vega"  -> g.vega.fold(Json.Null)(Json.fromDoubleOrNull),
      "rho"   -> g.rho.fold(Json.Null)(Json.fromDoubleOrNull)
    )
  }

  given Encoder[OptionTrade] = Encoder.instance { t =>
    Json.obj(
      "price"     -> Json.fromDoubleOrNull(t.price),
      "size"      -> Json.fromInt(t.size),
      "timestamp" -> Json.fromString(t.timestamp)
    )
  }

  given Encoder[OptionQuote] = Encoder.instance { q =>
    Json.obj(
      "bid"       -> Json.fromDoubleOrNull(q.bid),
      "ask"       -> Json.fromDoubleOrNull(q.ask),
      "bid_size"  -> Json.fromInt(q.bidSize),
      "ask_size"  -> Json.fromInt(q.askSize),
      "timestamp" -> Json.fromString(q.timestamp)
    )
  }

  given Encoder[OptionBar] = Encoder.instance { b =>
    Json.obj(
      "timestamp"   -> Json.fromString(b.timestamp),
      "open"        -> Json.fromDoubleOrNull(b.open),
      "high"        -> Json.fromDoubleOrNull(b.high),
      "low"         -> Json.fromDoubleOrNull(b.low),
      "close"       -> Json.fromDoubleOrNull(b.close),
      "volume"      -> Json.fromLong(b.volume),
      "trade_count" -> Json.fromInt(b.tradeCount),
      "vwap"        -> Json.fromDoubleOrNull(b.vwap)
    )
  }

  given Encoder[OptionSnapshot] = Encoder.instance { s =>
    Json.obj(
      "symbol"             -> Json.fromString(s.symbol),
      "latest_trade"       -> s.latestTrade.fold(Json.Null)(_.asJson),
      "latest_quote"       -> s.latestQuote.fold(Json.Null)(_.asJson),
      "greeks"             -> s.greeks.fold(Json.Null)(_.asJson),
      "implied_volatility" -> s.impliedVolatility.fold(Json.Null)(Json.fromDoubleOrNull)
    )
  }

  given Encoder[OptionChainEntry] = Encoder.instance { e =>
    Json.obj(
      "contract_symbol" -> Json.fromString(e.contractSymbol),
      "underlying"      -> Json.fromString(e.underlying),
      "snapshot"        -> e.snapshot.asJson,
      "bars"            -> Json.fromValues(e.bars.map(_.asJson))
    )
  }
