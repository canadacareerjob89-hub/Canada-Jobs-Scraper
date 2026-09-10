import re
import urllib.parse
import threading
import base64
from typing import Optional, Dict, Tuple, List
from curl_cffi import requests
from scrapling import Selector

import email_verifier

_thread_local = threading.local()

def get_maps_session() -> requests.Session:
    """Thread-local session for Google Maps and Local Place queries with TLS Chrome 124 fingerprinting."""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = requests.Session(impersonate="chrome124")
    return _thread_local.session

def clean_company_name(name: str) -> str:
    """Strip generic corporate suffixes for clean Maps searching."""
    clean = re.sub(r"\b(inc|ltd|corp|corporation|llc|limited|co)\.?\b", "", name, flags=re.I)
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()

def decode_bing_url(href: str) -> str:
    """Decode base64 encoded destination URL from Bing search result redirect links."""
    if "bing.com/ck/a" in href and "u=" in href:
        try:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            u_val = parsed.get("u", [""])[0]
            if u_val.startswith("a1"):
                raw_b64 = u_val[2:]
                raw_b64 += "=" * ((4 - len(raw_b64) % 4) % 4)
                return base64.urlsafe_b64decode(raw_b64).decode("utf-8", errors="ignore")
        except Exception:
            pass
    return href

def decode_yahoo_url(href: str) -> str:
    """Decode destination URL from Yahoo Canada search result redirect links."""
    if "r.search.yahoo.com" in href and "/RU=" in href:
        try:
            ru_match = re.search(r"/RU=([^/]+)", href)
            if ru_match:
                return urllib.parse.unquote(ru_match.group(1))
        except Exception:
            pass
    return href

KNOWN_NETWORK_DOMAINS = {
    "guardian-ida-remedysrx.ca", "remedysrx.ca", "pharmasave.com", "shoppersdrugmart.ca",
    "rexall.ca", "jeancoutu.com", "familiprix.com", "uniprix.com", "proxim.ca",
    "remax.ca", "royallepage.ca", "century21.ca", "sutton.com", "c21.ca",
    "homehardware.ca", "rona.ca", "canadiantire.ca", "napaonline.com", "autopro.ca",
    "oktire.com", "fountain-tire.com", "kal-tire.com", "speedyglass.ca", "mrsub.ca",
    "timhortons.com", "timhortons.ca", "subway.com", "bostonpizza.com", "dairyqueen.com",
    "mcdonalds.ca", "aandw.ca", "iga.net", "metro.ca", "sobeys.com", "foodland.ca"
}

def is_url_matching_employer(url: str, employer: str) -> bool:
    """
    Strictly check if URL domain matches employer name, or if it is an authentic location sub-page
    on a recognized Canadian parent network/franchise.
    """
    from hermes_enrichment import extract_employer_tokens, is_domain_matching_employer, is_excluded_domain
    
    if not url or not employer or is_excluded_domain(url):
        return False
        
    try:
        parsed = urllib.parse.urlparse(url)
        domain = (parsed.netloc or "").lower().replace("www.", "").split(":")[0]
        
        # 1. Primary Rule: Distinctive employer name tokens match in the Domain Name itself
        if is_domain_matching_employer(domain, employer):
            return True
            
        # 2. Secondary Rule: Authenticated Location sub-page ONLY on recognized parent franchise networks
        is_known_network = any(net in domain for net in KNOWN_NETWORK_DOMAINS)
        if is_known_network:
            tokens = extract_employer_tokens(employer)
            if tokens:
                clean_path = parsed.path.lower().replace("-", "").replace("_", "")
                matched = [t for t in tokens if t in clean_path]
                if len(tokens) == 1 and len(tokens[0]) >= 3 and tokens[0] in clean_path:
                    return True
                elif len(tokens) >= 2 and len(matched) == len(tokens):
                    return True
    except Exception:
        pass
    return False

def search_yahoo_canada(query: str, session: requests.Session) -> List[Dict]:
    """Query Yahoo Canada for localized Canadian business search and Knowledge cards."""
    url = f"https://ca.search.yahoo.com/search?p={urllib.parse.quote_plus(query)}"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
        "Referer": "https://ca.search.yahoo.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    results = []
    try:
        res = session.get(url, headers=headers, timeout=(3.0, 5.0))
        if res.status_code == 200 and res.text:
            sel = Selector(res.text)
            for li in sel.css(".algo, .algo-sr, .dd.algo"):
                title = li.css("h3 a::text, .title a::text").get() or ""
                raw_href = li.css("h3 a::attr(href), .title a::attr(href)").get() or ""
                href = decode_yahoo_url(raw_href)
                snippet = " ".join(li.css(".compText::text, p::text").getall()).strip()
                if href and href.startswith("http"):
                    results.append({"title": title, "href": href, "snippet": snippet})
    except Exception:
        pass
    return results

def search_google_maps_place(
    employer_name: str,
    city: str = "",
    province: str = "",
    full_address: str = ""
) -> Optional[Dict[str, str]]:
    """
    Search Local Place Intelligence & Map profiles for a Canadian business.
    
    Extracts:
        - website: Official business website URL (standalone or franchise location page)
        - phone: Local Canadian business phone number (validated)
        - address: Physical business location
        - source: 'Google Maps / Local Places'
    """
    if not employer_name:
        return None
        
    clean_emp = clean_company_name(employer_name)
    if not clean_emp:
        clean_emp = employer_name.strip()
        
    # Extract street name if available
    street_part = ""
    if full_address:
        parts = full_address.split(",")
        if parts:
            street_part = parts[0].strip()
            
    location_parts = [p for p in [city.strip(), province.strip()] if p]
    location_str = " ".join(location_parts)
    
    queries = [
        f'{clean_emp} {location_str} {street_part}'.strip(),
        f'"{clean_emp}" "{city}" location phone',
        f'{clean_emp} {city} {province} Canada phone website'
    ]
    
    session = get_maps_session()
    
    discovered_website = ""
    discovered_phone = ""
    discovered_address = ""
    
    from hermes_enrichment import validate_and_format_phone
    
    for q in queries:
        # Query 1: Yahoo Canada (Google-grade Canadian Local index)
        yahoo_res = search_yahoo_canada(q, session)
        for item in yahoo_res:
            href = item.get("href", "")
            snippet = item.get("snippet", "")
            
            # 1. Check if URL matches employer domain or sub-page
            if href and not discovered_website:
                if is_url_matching_employer(href, employer_name):
                    discovered_website = href
                    
            # 2. Extract Phone number from rich snippet
            if not discovered_phone and snippet:
                # Direct labeled phone
                phone_matches = re.findall(r"(?:Phone|Call|Tel|T[ée]l[ée]phone|Contact)[:\s]*([+]?1?[\s.-]?\(?[2-9][0-8][0-9]\)?[\s.-]?[2-9][0-9]{2}[\s.-]?[0-9]{4})", snippet, re.I)
                if not phone_matches:
                    phone_matches = re.findall(r"(?:\+?1[-.\s]?)?\(?([2-9][0-8][0-9])\)?[-.\s]?([2-9][0-9]{2})[-.\s]?([0-9]{4})", snippet)
                    phone_matches = [f"({m[0]}) {m[1]}-{m[2]}" for m in phone_matches]
                    
                for p_raw in phone_matches:
                    fmt = validate_and_format_phone(p_raw)
                    if fmt:
                        discovered_phone = fmt
                        break
                        
        if discovered_website and discovered_phone:
            break
            
        # Query 2: Bing Local & Organic
        search_url = f"https://www.bing.com/search?q={urllib.parse.quote_plus(q)}"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
            "Referer": "https://www.bing.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        try:
            res = session.get(search_url, headers=headers, timeout=(3.0, 5.0))
            if res.status_code == 200 and res.text:
                sel = Selector(res.text)
                for li in sel.css("li.b_algo"):
                    raw_href = li.css("h2 a::attr(href)").get() or ""
                    href = decode_bing_url(raw_href)
                    snippet = " ".join(li.css(".b_caption p::text, p::text").getall()).strip()
                    
                    if href and not discovered_website:
                        if is_url_matching_employer(href, employer_name):
                            discovered_website = href
                            
                    if not discovered_phone and snippet:
                        phone_matches = re.findall(r"(?:Phone|Call|Tel|Contact)[:\s]*([+]?1?[\s.-]?\(?[2-9][0-8][0-9]\)?[\s.-]?[2-9][0-9]{2}[\s.-]?[0-9]{4})", snippet, re.I)
                        if not phone_matches:
                            phone_matches = re.findall(r"(?:\+?1[-.\s]?)?\(?([2-9][0-8][0-9])\)?[-.\s]?([2-9][0-9]{2})[-.\s]?([0-9]{4})", snippet)
                            phone_matches = [f"({m[0]}) {m[1]}-{m[2]}" for m in phone_matches]
                        for p_raw in phone_matches:
                            fmt = validate_and_format_phone(p_raw)
                            if fmt:
                                discovered_phone = fmt
                                break
        except Exception:
            pass
            
        if discovered_website and discovered_phone:
            break
            
    if discovered_website or discovered_phone:
        return {
            "website": discovered_website,
            "phone": discovered_phone,
            "address": discovered_address or full_address,
            "source": "Google Maps / Local Places"
        }
        
    return None
