from app.schema import Market

MARKET_TO_PYKRX = {
    Market.KR_KOSPI: "KOSPI",
    Market.KR_KOSDAQ: "KOSDAQ",
}

KR_MARKETS = frozenset({Market.KR_KOSPI, Market.KR_KOSDAQ})
US_MARKETS = frozenset({Market.US_NYSE, Market.US_NASDAQ})
