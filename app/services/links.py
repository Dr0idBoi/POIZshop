# app/services/links.py
import re
import logging
from typing import Optional, Dict, Callable
from urllib.parse import quote_plus, urlencode

log = logging.getLogger("links")

# Type alias for carrier link generators
CarrierLinkGenerator = Callable[[str], str]

def _clean_tracking(code: str) -> str:
    """Clean tracking code by removing whitespace and common prefixes"""
    code = code.strip().upper()
    prefixes = ['TRACK:', 'TRACKING:', '#']
    for prefix in prefixes:
        if code.startswith(prefix):
            code = code[len(prefix):]
    return code.strip()

def _boxberry_link(code: str) -> str:
    """Generate Boxberry tracking link"""
    code = _clean_tracking(code)
    return f"https://boxberry.ru/tracking-page?id={quote_plus(code)}"

def _cdek_link(code: str) -> str:
    """Generate CDEK tracking link"""
    code = _clean_tracking(code)
    return f"https://www.cdek.ru/track?order_id={quote_plus(code)}"

def _pochta_link(code: str) -> str:
    """Generate Russian Post tracking link"""
    code = _clean_tracking(code)
    return f"https://www.pochta.ru/tracking#{quote_plus(code)}"

def _poizon_link(code: str) -> str:
    """Generate POIZON item link"""
    code = code.strip()
    if code.startswith('http'):
        return code  # Already a full URL
    return f"https://poizon.com/item/{quote_plus(code)}"

# Registry of carrier link generators
CARRIER_LINKS: Dict[str, CarrierLinkGenerator] = {
    'boxberry': _boxberry_link,
    'cdek': _cdek_link,
    'pochta': _pochta_link,
    'poizon': _poizon_link
}

def carrier_link(carrier: str, code: str) -> Optional[str]:
    """
    Generate tracking link for specified carrier
    
    Args:
        carrier: Carrier code (boxberry, cdek, pochta)
        code: Tracking number or item code
    
    Returns:
        Tracking URL or None if carrier not supported
    """
    try:
        carrier = carrier.lower().strip()
        if carrier in CARRIER_LINKS:
            return CARRIER_LINKS[carrier](code)
        log.warning(f"Unsupported carrier: {carrier}")
    except Exception as e:
        log.error(f"Error generating {carrier} link for {code}: {e}")
    return None

def is_valid_poizon_link(link: str) -> bool:
    """
    Validate POIZON item link format
    
    Args:
        link: URL or item code to validate
    
    Returns:
        True if link appears valid
    """
    try:
        link = link.strip()
        # Direct item codes
        if re.match(r'^[A-Z0-9]{8,}$', link):
            return True
            
        # Full URLs
        if link.startswith(('http://', 'https://')):
            valid_domains = ['poizon.com', 'dewu.com']
            for domain in valid_domains:
                if domain in link.lower():
                    return True
    except Exception as e:
        log.error(f"Error validating POIZON link: {e}")
    return False

def extract_poizon_id(link: str) -> Optional[str]:
    """
    Extract item ID from POIZON link
    
    Args:
        link: POIZON URL or item code
    
    Returns:
        Item ID or None if not found
    """
    try:
        link = link.strip()
        
        # Direct item code
        if re.match(r'^[A-Z0-9]{8,}$', link):
            return link
            
        # Extract from URL
        if link.startswith(('http://', 'https://')):
            # Try different URL patterns
            patterns = [
                r'/item/([A-Z0-9]+)',
                r'id=([A-Z0-9]+)',
                r'/([A-Z0-9]{8,})(?:/|$)'
            ]
            for pattern in patterns:
                match = re.search(pattern, link)
                if match:
                    return match.group(1)
    except Exception as e:
        log.error(f"Error extracting POIZON ID from {link}: {e}")
    return None

def normalize_tracking(carrier: str, code: str) -> Optional[str]:
    """
    Normalize tracking number format for specific carrier
    
    Args:
        carrier: Carrier code
        code: Raw tracking number
    
    Returns:
        Normalized tracking number or None if invalid
    """
    try:
        code = _clean_tracking(code)
        carrier = carrier.lower().strip()
        
        # Carrier-specific validation patterns
        patterns = {
            'boxberry': r'^(?:BBX)?[0-9]{10}$',
            'cdek': r'^[0-9]{10}$',
            'pochta': r'^[A-Z0-9]{13,14}$'
        }
        
        if carrier in patterns:
            if re.match(patterns[carrier], code):
                return code
            # Try adding prefix if needed
            if carrier == 'boxberry' and re.match(r'^[0-9]{10}$', code):
                return f"BBX{code}"
                
        log.warning(f"Invalid tracking format for {carrier}: {code}")
    except Exception as e:
        log.error(f"Error normalizing tracking number: {e}")
    return None
