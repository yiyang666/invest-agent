"""Concrete data provider adapters."""

from .akshare_eastmoney import AkshareEastmoneyNavProvider
from .akshare_ths import AkshareThsDailyNavProvider, AkshareThsFundMetadataProvider
from .csv_nav import LocalCsvNavProvider
from .penghua_official import PenghuaOfficialNavProvider

__all__ = [
    "AkshareEastmoneyNavProvider",
    "AkshareThsDailyNavProvider",
    "AkshareThsFundMetadataProvider",
    "LocalCsvNavProvider",
    "PenghuaOfficialNavProvider",
]
