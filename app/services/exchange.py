# app/services/exchange.py
import aiohttp
import logging
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
import asyncio
from collections import OrderedDict

from ..db import get_db

log = logging.getLogger("exchange")

# Improved cache with size limit and TTL
class LRUCache:
    """LRU Cache с ограничением размера и TTL"""
    
    def __init__(self, maxsize: int = 100, ttl: timedelta = timedelta(hours=1)):
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: OrderedDict[str, Tuple[float, datetime]] = OrderedDict()
    
    def get(self, key: str) -> Optional[float]:
        """Получить значение из кеша"""
        if key not in self._cache:
            return None
        
        value, timestamp = self._cache[key]
        
        # Проверяем TTL
        if datetime.now() - timestamp > self.ttl:
            del self._cache[key]
            return None
        
        # Перемещаем в конец (most recently used)
        self._cache.move_to_end(key)
        return value
    
    def set(self, key: str, value: float):
        """Установить значение в кеш"""
        # Удаляем старое значение если есть
        if key in self._cache:
            del self._cache[key]
        
        # Добавляем новое значение
        self._cache[key] = (value, datetime.now())
        
        # Проверяем размер и удаляем самые старые записи
        while len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)
    
    def clear(self):
        """Очистить кеш"""
        self._cache.clear()
    
    def size(self) -> int:
        """Текущий размер кеша"""
        return len(self._cache)

# Глобальный кеш
RATE_CACHE = LRUCache(maxsize=100, ttl=timedelta(hours=1))
CACHE_DURATION = timedelta(hours=1)

# CBR API endpoint
CBR_API_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

async def _fetch_cbr_data() -> Optional[Dict]:
    """Fetch exchange rates from CBR API with retries"""
    retries = 3
    retry_delay = 1.0
    
    async with aiohttp.ClientSession() as session:
        while retries > 0:
            try:
                async with session.get(CBR_API_URL, timeout=10) as response:
                    if response.status == 200:
                        data = await response.text()
                        return json.loads(data)
                    log.warning(f"CBR API returned status {response.status}")
            except asyncio.TimeoutError:
                log.warning("CBR API request timed out")
            except Exception as e:
                log.error(f"Error fetching CBR data: {e}")
            
            retries -= 1
            if retries > 0:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
    
    return None

def _parse_rate(data: Dict, currency: str) -> Optional[float]:
    """Parse exchange rate from CBR API response"""
    try:
        if "Valute" in data and currency in data["Valute"]:
            currency_data = data["Valute"][currency]
            nominal = float(currency_data["Nominal"])
            value = float(currency_data["Value"])
            return value / nominal
    except (KeyError, ValueError, TypeError) as e:
        log.error(f"Error parsing rate for {currency}: {e}")
    return None

async def fetch_cbr_rate(currency: str) -> Optional[float]:
    """
    Fetch exchange rate from CBR with caching
    
    Args:
        currency: Currency code (e.g., "USD", "EUR", "CNY")
    
    Returns:
        Exchange rate or None if unavailable
    """
    # Check cache first
    cached_rate = RATE_CACHE.get(currency)
    if cached_rate is not None:
        log.debug(f"Cache hit for {currency}: {cached_rate}")
        return cached_rate
    
    try:
        # Try to get recent rate from database first
        async with get_db() as db:
            cur = await db.execute(
                "SELECT rate FROM exchange_rates "
                "WHERE base=? AND quote='RUB' "
                "AND datetime(as_of) > datetime('now', '-1 hour') "
                "ORDER BY as_of DESC LIMIT 1",
                (currency,)
            )
            row = await cur.fetchone()
            if row:
                rate = float(row['rate'])
                # Для CNY курс уже должен содержать +1 (если был сохранен правильно)
                # Но на всякий случай проверяем и добавляем +1 если нужно
                if currency == "CNY" and rate < 20:  # Если курс меньше 20, то скорее всего не содержит +1
                    rate += 1.0
                    log.info(f"DB hit for {currency}: adjusted to {rate} (+1.0)")
                else:
                    log.debug(f"DB hit for {currency}: {rate}")
                RATE_CACHE.set(currency, rate)
                return rate
        
        # If no recent rate in DB, fetch from API
        data = await _fetch_cbr_data()
        if not data:
            return None
        
        rate = _parse_rate(data, currency)
        if rate:
            # Для CNY добавляем +1 рубль
            if currency == "CNY":
                adjusted_rate = rate + 1.0
                log.info(f"API fetch for {currency}: {rate} -> adjusted to {adjusted_rate} (+1.0), cache size: {RATE_CACHE.size()}")
            else:
                adjusted_rate = rate
                log.info(f"API fetch for {currency}: {rate}, cache size: {RATE_CACHE.size()}")
            
            # Store in cache (adjusted rate)
            RATE_CACHE.set(currency, adjusted_rate)
            
            # Store in database (adjusted rate)
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO exchange_rates(base, quote, rate, as_of) "
                    "VALUES(?, 'RUB', ?, datetime('now'))",
                    (currency, adjusted_rate)
                )
                await db.commit()
            
            return adjusted_rate
            
    except Exception as e:
        log.error(f"Error in fetch_cbr_rate for {currency}: {e}")
    
    return None

async def get_rate_history(currency: str, days: int = 7) -> Dict[str, float]:
    """
    Get exchange rate history from database
    
    Args:
        currency: Currency code
        days: Number of days of history
        
    Returns:
        Dictionary of date -> rate
    """
    try:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT rate, date(as_of) as date FROM exchange_rates "
                "WHERE base=? AND quote='RUB' "
                "AND datetime(as_of) > datetime('now', ? || ' days') "
                "ORDER BY as_of",
                (currency, -days)
            )
            return {row['date']: float(row['rate']) for row in await cur.fetchall()}
    except Exception as e:
        log.error(f"Error getting rate history for {currency}: {e}")
        return {}

def clear_rate_cache() -> None:
    """Clear exchange rate cache"""
    RATE_CACHE.clear()
    log.info("Exchange rate cache cleared")

async def force_refresh_cny_rate() -> Optional[float]:
    """Принудительно обновить курс CNY с +1 рублем"""
    # Очищаем кэш для CNY
    if "CNY" in RATE_CACHE._cache:
        del RATE_CACHE._cache["CNY"]
    
    # Получаем свежий курс
    return await fetch_cbr_rate("CNY")

def get_cache_stats() -> Dict[str, int]:
    """Получить статистику кеша"""
    return {
        "size": RATE_CACHE.size(),
        "maxsize": RATE_CACHE.maxsize
    }
