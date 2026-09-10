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

def search_google_maps_place(
    employer_name: str,
    city: str = "",
    province: str = "",
    full_address: str = ""
) -> Optional[Dict[str, str]]:
    """
    Search Google Maps and Local Place intelligence for a Canadian business.
    
    Extracts:
        - website: Official business website URL (verified against domain matching)
        - phone: Local Canadian business phone number
        - address: Physical business location
        - source: 'Google Maps / Local Places'
    """
    if not employer_name:
        return None
        
    clean_emp = clean_company_name(employer_name)
    if not clean_emp:
        clean_emp = employer_name.strip()
        
    location_parts = [p for p in [city.strip(), province.strip()] if p]
    location_str = " ".join(location_parts)
    
    queries = [
        f'"{clean_emp}" {location_str} Canada Google Maps phone address',
        f'"{clean_emp}" "{city}" location phone website'
    ]
    
    session = get_maps_session()
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
        "Referer": "https://www.bing.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    discovered_website = ""
    discovered_phone = ""
    discovered_address = ""
    
    for q in queries:
        search_url = f"https://www.bing.com/search?q={urllib.parse.quote_plus(q)}"
        try:
            res = session.get(search_url, headers=headers, timeout=(3.0, 5.0))
            if res.status_code != 200 or not res.text:
                continue
                
            sel = Selector(res.text)
            
            # 1. Inspect organic result URLs for official website
            for li in sel.css("li.b_algo"):
                raw_href = li.css("h2 a::attr(href)").get() or ""
                href = decode_bing_url(raw_href)
                snippet = " ".join(li.css(".b_caption p::text, p::text").getall()).strip()
                
                # Check website
                if href and not discovered_website:
                    from hermes_enrichment import is_excluded_domain, is_domain_matching_employer
                    if not is_excluded_domain(href):
                        parsed = urllib.parse.urlparse(href)
                        domain = (parsed.netloc or "").lower().replace("www.", "").split(":")[0]
                        if is_domain_matching_employer(domain, employer_name):
                            discovered_website = f"{parsed.scheme}://{parsed.netloc}"
                            
                # Check for phone in snippet or local card
                if not discovered_phone and snippet:
                    from hermes_enrichment import validate_and_format_phone
                    phone_matches = re.findall(r"(?:Phone|Call|Tel|Contact)[:\s]*([+]?1?[\s.-]?\(?[2-9][0-8][0-9]\)?[\s.-]?[2-9][0-9]{2}[\s.-]?[0-9]{4})", snippet, re.I)
                    if not phone_matches:
                        phone_matches = re.findall(r"(?:\+?1[-.\s]?)?\(?([2-9][0-8][0-9])\)?[-.\s]?([2-9][0-9]{2})[-.\s]?([0-9]{4})", snippet)
                        phone_matches = [f"({m[0]}) {m[1]}-{m[2]}" for m in phone_matches]
                        
                    for p_raw in phone_matches:
                        fmt = validate_and_format_phone(p_raw)
                        if fmt:
                            discovered_phone = fmt
                            break
                            
            # 2. Check Local / Map sidebar card if present
            sidebar_text = " ".join(sel.css(".b_entityTitle, .b_hList, .b_address, .b_factrow").getall())
            if sidebar_text:
                if not discovered_phone:
                    from hermes_enrichment import validate_and_format_phone
                    for p_match in re.finditer(r"(?:\+?1[-.\s]?)?\(?([2-9][0-8][0-9])\)?[-.\s]?([2-9][0-9]{2})[-.\s]?([0-9]{4})", sidebar_text):
                        fmt = validate_and_format_phone(f"({p_match.group(1)}) {p_match.group(2)}-{p_match.group(3)}")
                        if fmt:
                            discovered_phone = fmt
                            break
                            
            if discovered_website and discovered_phone:
                break
                
        except Exception:
            continue
            
    if discovered_website or discovered_phone:
        return {
            "website": discovered_website,
            "phone": discovered_phone,
            "address": discovered_address,
            "source": "Google Maps / Local Places"
        }
        
    return None
