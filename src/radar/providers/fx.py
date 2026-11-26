"""Exchange rates for display conversion.

Primary: ECB euro reference rates (no key, ~30 currencies, working days).
Secondary: open.er-api.com (no key, daily, covers AMD/GEL/KZT and the rest of
the region; attribution "Rates By Exchange Rate API" is required and the data
may not be redistributed). Rates are for showing an approximate converted
price next to the provider's original price; nothing is charged in a
converted currency.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from radar.config import get_settings
from radar.core.errors import ProviderError
from radar.core.money import ConvertedMoney, FxRate, Money
from radar.providers.http import ProviderHttp

ECB_NS = {"e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
OPEN_ER_ATTRIBUTION = "Rates By Exchange Rate API (https://www.exchangerate-api.com)"


def _parse_ecb(xml_text: str) -> tuple[dict[str, Decimal], datetime]:
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise ProviderError("ecb", "unexpected DTD in feed")
    root = ET.fromstring(xml_text)  # noqa: S314 - DTDs rejected above
    rates: dict[str, Decimal] = {"EUR": Decimal(1)}
    as_of = datetime.now(UTC)
    for cube in root.iterfind(".//e:Cube[@time]", ECB_NS):
        as_of = datetime.fromisoformat(cube.attrib["time"]).replace(tzinfo=UTC)
        for c in cube:
            try:
                rates[c.attrib["currency"].upper()] = Decimal(c.attrib["rate"])
            except (KeyError, InvalidOperation):
                continue
    return rates, as_of


class FxService:
    def __init__(self, http: ProviderHttp | None = None, ttl_s: float = 6 * 3600) -> None:
        self.http = http or ProviderHttp("fx", rate_per_s=0.5, burst=2, max_retries=1)
        self.ttl_s = ttl_s
        # base EUR tables: source -> (fetched_monotonic, rates, as_of)
        self._tables: dict[str, tuple[float, dict[str, Decimal], datetime]] = {}

    async def _ecb(self) -> tuple[dict[str, Decimal], datetime]:
        resp = await self.http.request("GET", get_settings().fx_ecb_url)
        return _parse_ecb(resp.text)

    async def _open_er(self) -> tuple[dict[str, Decimal], datetime]:
        data = await self.http.get_json(f"{get_settings().fx_open_er_url}/EUR")
        if data.get("result") != "success":
            raise ProviderError("open_er", "rate feed returned an error")
        rates = {k.upper(): Decimal(str(v)) for k, v in data.get("rates", {}).items()}
        as_of = datetime.fromtimestamp(int(data.get("time_last_update_unix", time.time())), UTC)
        return rates, as_of

    async def _table(self, source: str) -> tuple[dict[str, Decimal], datetime]:
        hit = self._tables.get(source)
        if hit and time.monotonic() - hit[0] < self.ttl_s:
            return hit[1], hit[2]
        rates, as_of = await (self._ecb() if source == "ecb" else self._open_er())
        self._tables[source] = (time.monotonic(), rates, as_of)
        return rates, as_of

    def seed(self, source: str, rates: dict[str, Decimal], as_of: datetime) -> None:
        """Inject a table (tests, or rates restored from the database)."""
        self._tables[source] = (time.monotonic(), rates, as_of)

    async def rate(self, base: str, quote: str) -> FxRate | None:
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return FxRate(base, quote, Decimal(1), datetime.now(UTC), "identity")
        for source in ("ecb", "open_er"):
            try:
                rates, as_of = await self._table(source)
            except ProviderError:
                continue
            if base in rates and quote in rates and rates[base] != 0:
                label = "ECB reference rate" if source == "ecb" else OPEN_ER_ATTRIBUTION
                return FxRate(base, quote, rates[quote] / rates[base], as_of, label)
        return None

    async def convert(self, money: Money, quote: str) -> ConvertedMoney | None:
        r = await self.rate(money.currency, quote)
        return r.convert(money) if r else None

    async def aclose(self) -> None:
        await self.http.aclose()
