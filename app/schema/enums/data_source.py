from enum import Enum


class DataSource(str, Enum):
    KIS = "KIS"
    PYKRX = "PYKRX"
    ALPACA = "ALPACA"
    YFINANCE = "YFINANCE"
