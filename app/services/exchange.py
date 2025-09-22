# app/services/exchange.py
import logging
import httpx
from typing import Optional
from xml.etree import ElementTree as ET

log = logging.getLogger("exchange")

CBR_DAILY_XML = "https://www.cbr.ru/scripts/XML_daily.asp"

async def fetch_cbr_rate(code: str = "CNY") -> Optional[float]:
    """
    Возвращает курс валюты к рублю (за 1 единицу валюты).
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(CBR_DAILY_XML)
        resp.raise_for_status()
        xml = ET.fromstring(resp.text)
        for valute in xml.findall("Valute"):
            if valute.findtext("CharCode") == code:
                nominal = float(valute.findtext("Nominal").replace(",", "."))
                value = float(valute.findtext("Value").replace(",", "."))
                rate = value / nominal
                return rate
    return None
