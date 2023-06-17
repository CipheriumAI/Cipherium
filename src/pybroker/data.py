r"""Contains :class:`.DataSource`\ s used to fetch external data."""

"""Copyright (C) 2023 Edward West

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Lesser General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along
with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import akshare
import alpaca.data.historical.stock as alpaca_stock
import alpaca.data.historical.crypto as alpaca_crypto
import itertools
import numpy as np
import pandas as pd
import yfinance
from pybroker.cache import DataSourceCacheKey
from pybroker.common import (
    DataCol,
    parse_timeframe,
    to_datetime,
    to_seconds,
    verify_data_source_columns,
    verify_date_range,
)
from pybroker.scope import StaticScope
from abc import ABC, abstractmethod
from alpaca.data.enums import Adjustment
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from datetime import datetime
from typing import Final, Iterable, Optional, Union


class DataSourceCacheMixin:
    """Mixin that implements fetching and storing cached :class:`.DataSource`
    data.
    """

    def get_cached(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start_date: Union[str, datetime, pd.Timestamp, np.datetime64],
        end_date: Union[str, datetime, pd.Timestamp, np.datetime64],
        adjust: Optional[str],
    ) -> tuple[pd.DataFrame, Iterable[str]]:
        """Retrieves cached data from disk when caching is enabled with
        :meth:`pybroker.cache.enable_data_source_cache`.

        Args:
            symbols: :class:`Iterable` of symbols for fetching cached data.
            timeframe: Formatted string that specifies the timeframe
                resolution of the cached data. The timeframe string supports
                the following units:

                - ``"s"``/``"sec"``: seconds
                - ``"m"``/``"min"``: minutes
                - ``"h"``/``"hour"``: hours
                - ``"d"``/``"day"``: days
                - ``"w"``/``"week"``: weeks
                - ``"mo"``/``"month"``: months

                An example timeframe string is ``1h 30m``.
            start_date: Starting date of the cached data (inclusive).
            end_date: Ending date of the cached data (inclusive).
            adjust: The type of adjustment to make.

        Returns:
            ``tuple[pandas.DataFrame, Iterable[str]]`` containing a
            :class:`pandas.DataFrame` with the cached data, and an
            ``Iterable[str]`` of symbols for which no cached data was
            found.
        """
        df = pd.DataFrame()
        scope = StaticScope.instance()
        cache = scope.data_source_cache
        if cache is None:
            return df, symbols
        start_date = to_datetime(start_date)
        end_date = to_datetime(end_date)
        tf_seconds = to_seconds(timeframe)
        uncached_syms = []
        cached_syms = []
        for sym in symbols:
            cache_key = DataSourceCacheKey(
                symbol=sym,
                tf_seconds=tf_seconds,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            cached = cache.get(repr(cache_key))
            scope.logger.debug_get_data_source_cache(cache_key)
            if cached is None:
                uncached_syms.append(sym)
            else:
                cached_syms.append(sym)
                df = pd.concat([df, cached])
        if not uncached_syms:
            scope.logger.loaded_bar_data()
        scope.logger.info_loaded_bar_data(
            symbols=cached_syms,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )
        return df, uncached_syms

    def set_cached(
        self,
        timeframe: str,
        start_date: Union[str, datetime, pd.Timestamp, np.datetime64],
        end_date: Union[str, datetime, pd.Timestamp, np.datetime64],
        adjust: Optional[str],
        data: pd.DataFrame,
    ):
        """Stores data to disk cache when caching is enabled with
        :meth:`pybroker.cache.enable_data_source_cache`.

        Args:
            timeframe: Formatted string that specifies the timeframe
                resolution of the data to cache. The timeframe string supports
                the following units:

                - ``"s"``/``"sec"``: seconds
                - ``"m"``/``"min"``: minutes
                - ``"h"``/``"hour"``: hours
                - ``"d"``/``"day"``: days
                - ``"w"``/``"week"``: weeks
                - ``"mo"``/``"month"``: months

                An example timeframe string would be ``1h 30m``.
            start_date: Starting date of the data to cache (inclusive).
            end_date: Ending date of the data to cache (inclusive).
            adjust: The type of adjustment to make.
            data: :class:`pandas.DataFrame` containing the data to cache.
        """
        if data.empty:
            return
        scope = StaticScope.instance()
        cache = scope.data_source_cache
        if cache is None:
            return
        start_date = to_datetime(start_date)
        end_date = to_datetime(end_date)
        tf_seconds = to_seconds(timeframe)
        for sym in data[DataCol.SYMBOL.value].unique():
            df = data[data[DataCol.SYMBOL.value] == sym]
            cache_key = DataSourceCacheKey(
                symbol=sym,
                tf_seconds=tf_seconds,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            cache.set(repr(cache_key), df)
            scope.logger.debug_set_data_source_cache(cache_key)


class DataSource(ABC, DataSourceCacheMixin):
    """Base class for querying data from an external source. Extend this class
    and override :meth:`._fetch_data` to implement a custom
    :class:`.DataSource` that can be used with
    :class:`pybroker.strategy.Strategy`.
    """

    def __init__(self):
        self._scope = StaticScope.instance()
        self._logger = self._scope.logger

    def query(
        self,
        symbols: Union[str, Iterable[str]],
        start_date: Union[str, datetime],
        end_date: Union[str, datetime],
        timeframe: Optional[str] = "",
        adjust: Optional[str] = None,
    ) -> pd.DataFrame:
        """Queries data. Cached data is returned if caching is enabled by
        calling :meth:`pybroker.cache.enable_data_source_cache`.

        Args:
            symbols: Symbols of the data to query.
            start_date: Start date of the data to query (inclusive).
            end_date: End date of the data to query (inclusive).
            timeframe: Formatted string that specifies the timeframe
                resolution to query. The timeframe string supports the
                following units:

                - ``"s"``/``"sec"``: seconds
                - ``"m"``/``"min"``: minutes
                - ``"h"``/``"hour"``: hours
                - ``"d"``/``"day"``: days
                - ``"w"``/``"week"``: weeks
                - ``"mo"``/``"month"``: months

                An example timeframe string is ``1h 30m``.
            adjust: The type of adjustment to make.

        Returns:
            :class:`pandas.DataFrame` containing the queried data.
        """
        start_date = to_datetime(start_date)
        end_date = to_datetime(end_date)
        verify_date_range(start_date, end_date)
        if type(symbols) == str and not symbols:
            raise ValueError("Symbols cannot be empty.")
        unique_syms = (
            frozenset((symbols,))
            if type(symbols) == str
            else frozenset(symbols)
        )
        if not unique_syms:
            raise ValueError("Symbols cannot be empty.")
        timeframe = self._format_timeframe(timeframe)
        cached_df, uncached_syms = self.get_cached(
            symbols=unique_syms,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if not uncached_syms:
            return cached_df
        self._logger.download_bar_data_start()
        self._logger.info_download_bar_data_start(
            symbols=uncached_syms,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )
        df = self._fetch_data(
            unique_syms, start_date, end_date, timeframe, adjust
        )
        if (
            self._scope.data_source_cache is not None
            and not cached_df.columns.empty
            and set(cached_df.columns) != set(df.columns)
        ):
            self._logger.info_invalidate_data_source_cache()
            self._scope.data_source_cache.clear()
            return self.query(symbols, start_date, end_date, timeframe)
        verify_data_source_columns(df)
        self.set_cached(timeframe, start_date, end_date, adjust, df)
        df = pd.concat((cached_df, df))
        if not df.empty:
            df = df.sort_values(by=[DataCol.DATE.value, DataCol.SYMBOL.value])
        self._logger.download_bar_data_completed()
        return df.reset_index(drop=True)

    @abstractmethod
    def _fetch_data(
        self,
        symbols: frozenset[str],
        start_date: datetime,
        end_date: datetime,
        timeframe: Optional[str],
        adjust: Optional[str],
    ) -> pd.DataFrame:
        """:meta public:
        Override this method to return data from a custom
        source. The returned :class:`pandas.DataFrame` must contain the
        following columns: ``symbol``, ``date``, ``open``, ``high``, ``low``,
        and ``close``.

        Args:
            symbols: Ticker symbols of the data to query.
            start_date: Start date of the data to query (inclusive).
            end_date: End date of the data to query (inclusive).
            timeframe: Formatted string that specifies the timeframe
                resolution to query. The timeframe string supports the
                following units:

                - ``"s"``/``"sec"``: seconds
                - ``"m"``/``"min"``: minutes
                - ``"h"``/``"hour"``: hours
                - ``"d"``/``"day"``: days
                - ``"w"``/``"week"``: weeks
                - ``"mo"``/``"month"``: months

                An example timeframe string is ``1h 30m``.
            adjust: The type of adjustment to make.

        Returns:
            :class:`pandas.DataFrame` containing the queried data.
        """

    def _format_timeframe(self, timeframe: Optional[str]) -> str:
        if not timeframe:
            return ""
        return " ".join(
            f"{part[0]}{part[1]}" for part in parse_timeframe(timeframe)
        )


def _parse_alpaca_timeframe(
    timeframe: Optional[str],
) -> tuple[int, TimeFrameUnit]:
    if timeframe is None:
        raise ValueError("Timeframe needs to be specified for Alpaca.")
    parts = parse_timeframe(timeframe)
    if len(parts) != 1:
        raise ValueError(f"Invalid Alpaca timeframe: {timeframe}")
    tf = parts[0]
    if tf[1] == "min":
        unit = TimeFrameUnit.Minute
    elif tf[1] == "hour":
        unit = TimeFrameUnit.Hour
    elif tf[1] == "day":
        unit = TimeFrameUnit.Day
    elif tf[1] == "week":
        unit = TimeFrameUnit.Week
    elif tf[1] == "month":
        unit = TimeFrame.Month
    else:
        raise ValueError(f"Invalid Alpaca timeframe: {timeframe}")
    return tf[0], unit


class Alpaca(DataSource):
    """Retrieves stock data from `Alpaca <https://alpaca.markets/>`_."""

    __EST: Final = "US/Eastern"

    def __init__(self, api_key: str, api_secret: str):
        super().__init__()
        self._api = alpaca_stock.StockHistoricalDataClient(api_key, api_secret)

    def query(
        self,
        symbols: Union[str, Iterable[str]],
        start_date: Union[str, datetime],
        end_date: Union[str, datetime],
        timeframe: Optional[str] = "1d",
        adjust: Optional[str] = None,
    ) -> pd.DataFrame:
        _parse_alpaca_timeframe(timeframe)
        return super().query(symbols, start_date, end_date, timeframe, adjust)

    def _fetch_data(
        self,
        symbols: frozenset[str],
        start_date: datetime,
        end_date: datetime,
        timeframe: Optional[str],
        adjust: Optional[str],
    ) -> pd.DataFrame:
        """:meta private:"""
        amount, unit = _parse_alpaca_timeframe(timeframe)
        adj_enum = None
        if adjust is not None:
            for member in Adjustment:
                if member.value == adjust:
                    adj_enum = member
                    break
            if adj_enum is None:
                raise ValueError(f"Unknown adjustment: {adjust}.")
        request = StockBarsRequest(
            symbol_or_symbols=list(symbols),
            start=start_date,
            end=end_date,
            timeframe=TimeFrame(amount, unit),
            limit=None,
            adjustment=adj_enum,
            feed=None,
        )
        df = self._api.get_stock_bars(request).df  # type: ignore[union-attr]
        if df.columns.empty:
            return pd.DataFrame(
                columns=[
                    DataCol.SYMBOL.value,
                    DataCol.DATE.value,
                    DataCol.OPEN.value,
                    DataCol.HIGH.value,
                    DataCol.LOW.value,
                    DataCol.CLOSE.value,
                    DataCol.VOLUME.value,
                    DataCol.VWAP.value,
                ]
            )
        if df.empty:
            return df
        df = df.reset_index()
        df.rename(columns={"timestamp": DataCol.DATE.value}, inplace=True)
        df = df[[col.value for col in DataCol]]
        df[DataCol.DATE.value] = pd.to_datetime(df[DataCol.DATE.value])
        df[DataCol.DATE.value] = df[DataCol.DATE.value].dt.tz_convert(
            self.__EST
        )
        return df


class AlpacaCrypto(DataSource):
    """Retrieves crypto data from `Alpaca <https://alpaca.markets/>`_.

    Args:
        api_key: Alpaca API key.
        api_secret: Alpaca API secret.
    """

    TRADE_COUNT: Final = "trade_count"
    COLUMNS: Final = (
        DataCol.SYMBOL.value,
        DataCol.DATE.value,
        DataCol.OPEN.value,
        DataCol.HIGH.value,
        DataCol.LOW.value,
        DataCol.CLOSE.value,
        DataCol.VOLUME.value,
        DataCol.VWAP.value,
        TRADE_COUNT,
    )

    __EST: Final = "US/Eastern"

    def __init__(self, api_key: str, api_secret: str):
        super().__init__()
        self._scope.register_custom_cols(self.TRADE_COUNT)
        self._api = alpaca_crypto.CryptoHistoricalDataClient(
            api_key, api_secret
        )

    def query(
        self,
        symbols: Union[str, Iterable[str]],
        start_date: Union[str, datetime],
        end_date: Union[str, datetime],
        timeframe: Optional[str] = "1d",
        _: Optional[str] = None,
    ) -> pd.DataFrame:
        _parse_alpaca_timeframe(timeframe)
        return super().query(symbols, start_date, end_date, timeframe, _)

    def _fetch_data(
        self,
        symbols: frozenset[str],
        start_date: datetime,
        end_date: datetime,
        timeframe: Optional[str],
        _: Optional[str],
    ) -> pd.DataFrame:
        """:meta private:"""
        amount, unit = _parse_alpaca_timeframe(timeframe)
        request = CryptoBarsRequest(
            symbol_or_symbols=list(symbols),
            start=start_date,
            end=end_date,
            timeframe=TimeFrame(amount, unit),
            limit=None,
        )
        df = self._api.get_crypto_bars(request).df  # type: ignore[union-attr]
        if df.columns.empty:
            return pd.DataFrame(columns=self.COLUMNS)
        if df.empty:
            return df
        df = df.reset_index()
        df.rename(columns={"timestamp": DataCol.DATE.value}, inplace=True)
        df = df[[col for col in self.COLUMNS]]
        df[DataCol.DATE.value] = pd.to_datetime(df[DataCol.DATE.value])
        df[DataCol.DATE.value] = df[DataCol.DATE.value].dt.tz_convert(
            self.__EST
        )
        return df


class YFinance(DataSource):
    r"""Retrieves data from `Yahoo Finance <https://finance.yahoo.com/>`_\ .

    Attributes:
        ADJ_CLOSE: Column name of adjusted close prices.
    """

    ADJ_CLOSE: Final = "adj_close"
    __TIMEFRAME: Final = "1d"

    def __init__(self):
        super().__init__()
        self._scope.register_custom_cols(self.ADJ_CLOSE)

    def query(
        self,
        symbols: Union[str, Iterable[str]],
        start_date: Union[str, datetime],
        end_date: Union[str, datetime],
        _timeframe: Optional[str] = "",
        _adjust: Optional[str] = None,
    ) -> pd.DataFrame:
        r"""Queries data from `Yahoo Finance <https://finance.yahoo.com/>`_\ .
        The timeframe of the data is limited to per day only.

        Args:
            symbols: Ticker symbols of the data to query.
            start_date: Start date of the data to query (inclusive).
            end_date: End date of the data to query (inclusive).

        Returns:
            :class:`pandas.DataFrame` containing the queried data.
        """
        return super().query(
            symbols, start_date, end_date, self.__TIMEFRAME, _adjust
        )

    def _fetch_data(
        self,
        symbols: frozenset[str],
        start_date: datetime,
        end_date: datetime,
        _timeframe: Optional[str],
        _adjust: Optional[str],
    ) -> pd.DataFrame:
        """:meta private:"""
        df = yfinance.download(list(symbols), start=start_date, end=end_date)
        if df.columns.empty:
            return pd.DataFrame(
                columns=[
                    DataCol.SYMBOL.value,
                    DataCol.DATE.value,
                    DataCol.OPEN.value,
                    DataCol.HIGH.value,
                    DataCol.LOW.value,
                    DataCol.CLOSE.value,
                    DataCol.VOLUME.value,
                    self.ADJ_CLOSE,
                ]
            )
        if df.empty:
            return df
        df = df.reset_index()
        result = pd.DataFrame()
        if len(symbols) == 1:
            result[DataCol.DATE.value] = df["Date"].values
            result[DataCol.SYMBOL.value] = tuple(
                itertools.repeat(next(iter(symbols)), len(df["Close"].values))
            )
            result[DataCol.OPEN.value] = df["Open"].values
            result[DataCol.HIGH.value] = df["High"].values
            result[DataCol.LOW.value] = df["Low"].values
            result[DataCol.CLOSE.value] = df["Close"].values
            result[DataCol.VOLUME.value] = df["Volume"].values
            result[self.ADJ_CLOSE] = df["Adj Close"].values
        else:
            df.columns = df.columns.to_flat_index()
            for sym in symbols:
                sym_df = pd.DataFrame()
                sym_df[DataCol.DATE.value] = df[("Date", "")].values
                sym_df[DataCol.SYMBOL.value] = tuple(
                    itertools.repeat(sym, len(df[("Close", sym)].values))
                )
                sym_df[DataCol.OPEN.value] = df[("Open", sym)].values
                sym_df[DataCol.HIGH.value] = df[("High", sym)].values
                sym_df[DataCol.LOW.value] = df[("Low", sym)].values
                sym_df[DataCol.CLOSE.value] = df[("Close", sym)].values
                sym_df[DataCol.VOLUME.value] = df[("Volume", sym)].values
                sym_df[self.ADJ_CLOSE] = df[("Adj Close", sym)].values
                result = pd.concat((result, sym_df))
        return result


class AKShare(DataSource):
    r"""Retrieves data from `AKShare <https://akshare.akfamily.xyz/>`_\ .

    Attributes:
        ADJ_CLOSE: Column name of adjusted close prices.
    """

    __TIMEFRAME: Final = "1d"

    def query(
        self,
        symbols: Union[str, Iterable[str]],
        start_date: Union[str, datetime],
        end_date: Union[str, datetime],
        _: Optional[str] = "",
        adjust: Optional[str] = "hfq",
    ) -> pd.DataFrame:
        r"""Queries data from `AKShare <https://akshare.akfamily.xyz/>`_\ .
        The timeframe of the data is limited to per day only.

        Args:
            symbols: Ticker symbols of the data to query.
            start_date: Start date of the data to query (inclusive).
            end_date: End date of the data to query (inclusive).
            adjust: The type of adjustment to make.

        Returns:
            :class:`pandas.DataFrame` containing the queried data.
        """
        return super().query(
            symbols, start_date, end_date, self.__TIMEFRAME, adjust
        )

    def _fetch_data(
        self,
        symbols: frozenset[str],
        start_date: datetime,
        end_date: datetime,
        _: Optional[str],
        adjust: Optional[str],
    ) -> pd.DataFrame:
        """:meta private:"""
        start_date_str = to_datetime(start_date).strftime("%Y%m%d")
        end_date_str = to_datetime(end_date).strftime("%Y%m%d")
        symbols_list = list(symbols)
        symbols_simple = [item.split(".")[0] for item in symbols_list]
        result = pd.DataFrame()
        for i in range(len(symbols_list)):
            try:
                temp_df = akshare.stock_zh_a_hist(
                    symbols_simple[i],
                    start_date=start_date_str,
                    end_date=end_date_str,
                    period="daily",
                    adjust=adjust if adjust is not None else "",
                )
                if not temp_df.columns.empty:
                    temp_df["symbol"] = symbols_list[i]
            except KeyError:
                temp_df = pd.DataFrame()
            result = pd.concat([result, temp_df], ignore_index=True)
        if result.columns.empty:
            return pd.DataFrame(
                columns=[
                    DataCol.SYMBOL.value,
                    DataCol.DATE.value,
                    DataCol.OPEN.value,
                    DataCol.HIGH.value,
                    DataCol.LOW.value,
                    DataCol.CLOSE.value,
                    DataCol.VOLUME.value,
                ]
            )
        if result.empty:
            return result
        result.rename(
            columns={
                "日期": DataCol.DATE.value,
                "开盘": DataCol.OPEN.value,
                "收盘": DataCol.CLOSE.value,
                "最高": DataCol.HIGH.value,
                "最低": DataCol.LOW.value,
                "成交量": DataCol.VOLUME.value,
            },
            inplace=True,
        )
        result["date"] = pd.to_datetime(result["date"])
        result = result[
            [
                DataCol.DATE.value,
                DataCol.SYMBOL.value,
                DataCol.OPEN.value,
                DataCol.HIGH.value,
                DataCol.LOW.value,
                DataCol.CLOSE.value,
                DataCol.VOLUME.value,
            ]
        ]
        return result
