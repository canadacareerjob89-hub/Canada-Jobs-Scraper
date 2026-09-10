import sys
import os
import re
import json
import time
import random
import base64
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, urljoin, parse_qs, quote_plus
from curl_cffi import requests
from dotenv import load_dotenv
import db
import email_verifier
import google_maps_enricher
import ai_entity_resolver

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Thread-local session for web searches to leverage HTTP/2 connection pooling per worker
_thread_local = threading.local()

def get_search_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = requests.Session(impersonate="chrome124")
    return _thread_local.session

# Comprehensive Canadian Area Codes + North American Toll-Free codes
CANADIAN_AREA_CODES = {
    # Alberta
    "403", "587", "780", "825", "368",
    # British Columbia
    "236", "250", "604", "672", "778",
    # Manitoba
    "204", "431", "584",
    # New Brunswick
    "506", "343",
    # Newfoundland and Labrador
    "709", "879",
    # Nova Scotia & Prince Edward Island
    "902", "782",
    # Ontario
    "226", "249", "289", "343", "365", "416", "437", "519", "548", "613", "647", "705", "753", "807", "905", "382",
    # Quebec
    "418", "438", "450", "514", "579", "581", "819", "873", "354", "367",
    # Saskatchewan
    "306", "639", "475",
    # Territories (Yukon, NWT, Nunavut)
    "867",
    # Toll-Free
    "800", "888", "877", "866", "855", "844", "833"
}

# Exhaustive Blacklist of Job Aggregators, Business Directories, Municipal/Gov Portals & Platforms
EXCLUDED_DOMAINS = {
    # Job aggregators & Career Boards
    "jobbank.gc.ca", "indeed.com", "indeed.ca", "linkedin.com", "glassdoor.ca", 
    "glassdoor.com", "ziprecruiter.com", "workopolis.com", "monster.ca", "monster.com",
    "talent.com", "neuvoo.ca", "neuvoo.com", "eluta.ca", "simplyhired.ca", "jobillico.com", 
    "canadajobs.com", "careerjet.ca", "jobsearch.ca", "jobboom.com", "learn4good.com",
    "canadacareersite.com", "jobrapido.com", "lmiagrader.com", "jobboard247.ca", "bebee.com",
    "jooble.org", "careerbeacon.com", "workinbc.ca", "jobify.ca", "drjobs.ae", "wowjobs.ca",
    "jobads.ca", "joblist.ca", "optioncarriere.com", "option-carriere.ca", "emplois.ca", 
    "emploisquebec.net", "quebecemploi.gouv.qc.ca", "guichetemplois.gc.ca",
    
    # Business Directories, Aggregators & Profilers
    "dnb.com", "infobel.ca", "infobel.com", "infobelpro.com", "411.ca", "yellowpages.ca", 
    "yellowpages.com", "pagesjaunes.ca", "pagesjaunes.com", "yelp.ca", "yelp.com", 
    "cylex-canada.ca", "cylex.ca", "cybo.com", "allbiz.ca", "profilecanada.com", 
    "canpages.ca", "canadapages.com", "zoominfo.com", "bbb.org", "bloomberg.com", 
    "owler.com", "opencorporates.com", "manta.com", "chamberofcommerce.com", "hotfrog.ca", 
    "myallday.com", "locanto.ca", "kijiji.ca", "craigslist.org", "indiansintoronto.ca", 
    "wiza.co", "apollo.io", "rocketreach.co", "lusha.com", "mapquest.com", "tripadvisor.com", 
    "tripadvisor.ca", "trustpilot.com", "kompass.com", "crunchbase.com", "datanyze.com", 
    "pitchbook.com", "tuugo.ca", "n49.com", "canadianbusinessdirectory.ca", "brownbook.net", 
    "find-open.ca", "yalwa.ca", "ezilon.com", "canadianplanet.net", "fyple.ca", 
    "canadiancompanies.info", "bizwiki.ca", "showmelocal.com", "opengovca.com", 
    "firmania.ca", "cdncompanies.com", "brokersnapshot.com", "birdeye.com",
    "indigenouscanada.org", "letsgolfgta.com", "zillow.com", "rentberry.com", "booking.com", 
    "expedia.ca", "doordash.com", "skipthedishes.com", "ubereats.com", "uber.com", "instacart.ca",
    "menu-world.com", "realtor.ca", "point2homes.com", "chambermaster.com", "cylex.net.ca",
    "findglocal.com", "cortico.health", "albertacorporations.com", "lmiafinder.ca", "canadacompanyregistry.com",
    "cantonsdelest.com", "tourismequebec.com", "openbiz.ca", "nexdu.com", "archives.business",
    "federalcorporation.ca", "canadabusinesses.org", "canadacorporations.org", "businessdirectory.ca", "canada-business-directory.com",
    "workabroadlink.com", "lmiajobs.ca", "jobbankjobs.com", "canadajobpostings.com", "cicnews.com",
    "agencycanada.com", "callupcontact.com", "canadian-companies.ca", "canada-biz.com",
    
    # Financial / Insurance / Generic Services Aggregators
    "manulife.com", "sunlife.ca", "greatwestlife.com", "desjardins.com",
    
    # Social Media, Video, CDN & Generic Tech
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "wikipedia.org", 
    "npmcdn.com", "unpkg.com", "jsdelivr.net", "cdnjs.cloudflare.com", "github.com",
    "apple.com", "google.com", "bing.com", "yahoo.com", "reddit.com", "tiktok.com",
    "pinterest.com", "threads.net", "vimeo.com", "wordpress.com", "wixsite.com", "squarespace.com",
    
    # Government Portals & Chambers of Commerce
    "toronto.ca", "canada.ca", "ontario.ca", "gov.bc.ca", "alberta.ca", "quebec.ca",
    "gc.ca", "smithvilletx.org", "mun-ndm.ca", "hampstead.qc.ca", "colwood.ca", 
    "cityofgp.com", "calgary.ca", "edmonton.ca", "vancouver.ca", "ottawa.ca", 
    "montreal.ca", "winnipeg.ca", "mississauga.ca", "brampton.ca", "surrey.ca", "mea.gov.in"
}

GENERIC_WORDS = {
    "inc", "ltd", "corp", "corporation", "limited", "llc", "canada", "canadian",
    "services", "solutions", "enterprises", "holdings", "management", "consulting",
    "group", "company", "the", "and", "co", "ca", "trading", "international", "north",
    "pro", "best", "total", "top", "auto", "cafe", "restaurant", "store", "shop", "express", 
    "general", "direct", "national", "global", "centre", "center", "care", "homes", "food", "foods",
    "construction", "logistics", "transport", "freight", "design", "build", "cleaning",
    "la", "le", "les", "de", "du", "des", "et", "en", "d", "l", "un", "une"
}

DISCARD_EMAIL_PATTERNS = {
    "user@domain.com", "example@domain.com", "email@domain.com", "name@email.com",
    "info@example.com", "test@test.com", "admin@domain.com", "yourname@domain.com",
    "user@email.com", "ana@company.com", "subscribers@yourbrand.com", "press@yourbrand.com"
}

EMAIL_REGEX = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")

import unicodedata

def normalize_text_ascii(text: str) -> str:
    """Normalize French accents and unicode characters into clean ASCII equivalents."""
    if not text:
        return ""
    return unicodedata.normalize('NFKD', str(text)).encode('ASCII', 'ignore').decode('utf-8').strip()

def extract_employer_tokens(employer: str) -> List[str]:
    """Extract distinctive search tokens from employer name, normalizing French accents & unicode."""
    norm = normalize_text_ascii(employer.lower())
    clean = re.sub(r"['’]", "", norm)
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", clean)
    tokens = [t for t in clean.split() if len(t) >= 2 and t not in GENERIC_WORDS]
    return tokens

def get_root_domain_body(domain: str) -> str:
    """Extract root second-level domain (SLD) without TLD extensions or generic subdomains."""
    if not domain:
        return ""
    clean = domain.lower().replace("www.", "").split(":")[0].strip()
    parts = clean.split(".")
    if len(parts) >= 3 and parts[-2] in {"qc", "on", "bc", "ab", "mb", "sk", "ns", "nb", "pe", "nl", "nt", "nu", "yt", "gc", "gov", "org", "co"}:
        return parts[-3].replace("-", "")
    elif len(parts) >= 2:
        return parts[-2].replace("-", "")
    return clean.replace("-", "")

def is_domain_matching_employer(domain: str, employer: str) -> bool:
    """Strictly verify if candidate domain matches distinctive tokens of employer name."""
    if not domain or not employer:
        return False
        
    domain_clean = domain.lower().replace("www.", "").split(":")[0]
    domain_body = get_root_domain_body(domain_clean)
    
    tokens = extract_employer_tokens(employer)
    if not tokens:
        norm = normalize_text_ascii(employer.lower())
        clean = re.sub(r"[^a-zA-Z0-9\s]", " ", norm)
        tokens = [t for t in clean.split() if len(t) >= 2 and t not in GENERIC_WORDS]
        
    if not tokens:
        return False
        
    # 1. Exact concatenated string match (e.g. "fromagerielachaudiere" in "fromagerielachaudiere.com")
    concat_all = "".join(tokens)
    if concat_all in domain_body or domain_body in concat_all:
        return True
        
    # 2. Multi-token evaluation: require at least 60% overlap or minimum 2 distinctive tokens
    matched_tokens = [t for t in tokens if t in domain_body]
    if len(tokens) == 1:
        token = tokens[0]
        if len(token) >= 3 and (token in domain_body or domain_body in token):
            return True
        return False
    elif len(tokens) >= 2:
        match_ratio = len(matched_tokens) / len(tokens)
        if len(matched_tokens) >= 2 or match_ratio >= 0.6:
            return True
            
    return False

def is_excluded_domain(domain_or_url: str) -> bool:
    """Check if a domain or URL belongs to blacklisted directories, aggregators, or platforms."""
    try:
        if "://" in domain_or_url:
            domain = urlparse(domain_or_url).netloc.lower()
        else:
            domain = domain_or_url.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        domain = domain.split(":")[0].strip()
        if not domain:
            return True
        for exc in EXCLUDED_DOMAINS:
            if domain == exc or domain.endswith("." + exc) or exc in domain:
                return True
    except Exception:
        pass
    return False

def is_excluded_email(email: str) -> bool:
    """Check if an email is from an excluded domain or is a generic/dummy artifact."""
    email = email.lower().strip()
    if "@" not in email:
        return True
    prefix, domain = email.split("@", 1)
    if is_excluded_domain(domain):
        return True
    if prefix in {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", 
                  "abuse", "postmaster", "spam", "privacy", "dataprotection", "legal"}:
        return True
    if any(p in email for p in DISCARD_EMAIL_PATTERNS):
        return True
    if "u003" in email or "\\" in email:
        return True
    return False

def validate_and_format_phone(raw_phone: str) -> Optional[str]:
    """Validate that a phone number has a legitimate Canadian/NANP area code and clean format."""
    digits = re.sub(r"[^\d]", "", raw_phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        area_code = digits[:3]
        prefix = digits[3:6]
        line = digits[6:]
        if len(set(digits)) <= 2:
            return None
        if area_code in CANADIAN_AREA_CODES and prefix[0] in "23456789":
            return f"({area_code}) {prefix}-{line}"
    return None

def is_valid_email(email: str) -> bool:
    """Validate email syntax and check active DNS MX deliverability."""
    if is_excluded_email(email):
        return False
    is_deliverable, _ = email_verifier.verify_email_deliverability(email)
    return is_deliverable

def clean_html_to_text(html: str) -> str:
    """Strip scripts, styles, svgs, stylesheets, header/nav boilerplate and extract pure visible text."""
    cleaned = re.sub(r"<(script|style|svg|noscript|iframe|header|footer|nav)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.I)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"&[a-zA-Z0-9#]+;", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def call_openrouter_ai(company_name: str, address: str, website_domain: str, website_text: str) -> Dict[str, Optional[str]]:
    """Call OpenRouter LLM with strict verification prompt to prevent directory false positives."""
    if not OPENROUTER_API_KEY:
        return {}

    clean_snippet = website_text[:2200]
    
    system_prompt = (
        "You are an expert contact intelligence verification engine for Canadian businesses.\n"
        "TASK: Determine if the provided webpage text belongs to the specific company and extract their direct corporate contact info.\n\n"
        "STRICT RULES:\n"
        "1. COMPANY VERIFICATION: Does this webpage genuinely belong to '{company_name}' in Canada?\n"
        "2. REJECT DIRECTORIES: If this is a business directory (Dun & Bradstreet, Infobel, YellowPages, BBB, etc.), municipal chamber, or aggregator, return is_company_match: false.\n"
        "3. REJECT 3RD PARTY EMAILS: Never return emails from web hosting support, chambers of commerce, or directories.\n"
        "4. OFFICIAL CONTACT ONLY: Extract only the official corporate/recruitment email and phone for '{company_name}'.\n"
        "5. If no direct email or phone for this specific company is present, set them to null.\n\n"
        "OUTPUT SCHEMA (JSON ONLY):\n"
        "{\n"
        "  \"is_company_match\": true,\n"
        "  \"email\": \"contact@company.com\",\n"
        "  \"phone\": \"(XXX) XXX-XXXX\"\n"
        "}"
    )
    user_prompt = (
        f"Target Company: {company_name}\n"
        f"Location/Address: {address}\n"
        f"Website Domain: {website_domain}\n\n"
        f"Webpage Content Snippet:\n{clean_snippet}\n\n"
        f"JSON Response:"
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/job-scraper",
        "X-Title": "CA Job Bank Scraper"
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 120
    }

    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=(3.0, 8.0))
        if res.status_code == 200:
            data = res.json()
            raw_msg = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
            content = raw_msg.strip()
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                if parsed.get("is_company_match") is False:
                    return {}
                return {
                    "email": parsed.get("email"),
                    "phone": parsed.get("phone")
                }
    except Exception as e:
        print(f"    [AI API notice] OpenRouter fallback: {e}")

    return {}

def decode_bing_url(href: str) -> str:
    """Decode base64 encoded destination URL from Bing search result redirect links."""
    if "bing.com/ck/a" in href and "u=" in href:
        try:
            parsed = parse_qs(urlparse(href).query)
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
                return parse_qs(urlparse(href).query).get("RU", [parse_qs(href).get("RU", [""])[0] or re.sub(r"/RK=.*", "", ru_match.group(1))])[0]
        except Exception:
            pass
    return href

def search_yahoo_canada(query: str, session: requests.Session) -> List[Dict]:
    """Query Yahoo Canada for localized Canadian business search and Knowledge cards."""
    url = f"https://ca.search.yahoo.com/search?p={quote_plus(query)}"
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
            from scrapling import Selector
            sel = Selector(res.text)
            for li in sel.css(".algo, .algo-sr, .dd.algo"):
                title = li.css("h3 a::text, .title a::text").get() or ""
                raw_href = li.css("h3 a::attr(href), .title a::attr(href)").get() or ""
                href = google_maps_enricher.decode_yahoo_url(raw_href)
                snippet = " ".join(li.css(".compText::text, p::text").getall()).strip()
                if href and href.startswith("http") and not is_excluded_domain(href):
                    results.append({"title": title, "href": href, "snippet": snippet})
    except Exception:
        pass
    return results

def search_bing(query: str, session: requests.Session) -> List[Dict]:
    """Search Bing using Chrome 124 TLS impersonation with high limits and zero blocks."""
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.bing.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        res = session.get(url, headers=headers, timeout=(3.0, 5.0))
        if res.status_code == 200:
            from scrapling import Selector
            sel = Selector(res.text)
            results = []
            for li in sel.css("li.b_algo"):
                title = li.css("h2 a::text, h2::text").get() or ""
                href = li.css("h2 a::attr(href)").get()
                if not href:
                    continue
                actual_url = decode_bing_url(href)
                snippet = " ".join(li.css(".b_caption p::text, p::text").getall()).strip()
                if actual_url and actual_url.startswith("http") and not is_excluded_domain(actual_url):
                    results.append({"title": title, "href": actual_url, "snippet": snippet})
            return results
    except Exception:
        pass
    return []

def search_duckduckgo_chrome(query: str, session: requests.Session) -> List[Dict]:
    """Search DuckDuckGo HTML using Chrome 124 TLS impersonation as secondary fallback."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://html.duckduckgo.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        res = session.get(url, headers=headers, timeout=(3.0, 5.0))
        if res.status_code == 200:
            from scrapling import Selector
            sel = Selector(res.text)
            results = []
            for art in sel.css(".result"):
                title = art.css(".result__title a::text, h2 a::text").get() or ""
                link = art.css(".result__url::attr(href), .result__title a::attr(href)").get()
                if not link:
                    continue
                if "uddg=" in link:
                    parsed = parse_qs(urlparse(link).query)
                    actual_url = parsed.get("uddg", [""])[0]
                else:
                    actual_url = link
                snippet = " ".join(art.css(".result__snippet::text").getall()).strip()
                if actual_url and actual_url.startswith("http") and not is_excluded_domain(actual_url):
                    results.append({"title": title, "href": actual_url, "snippet": snippet})
            return results
    except Exception:
        pass
    return []

def search_company_info(employer: str, address: str, city: str, province: str = "") -> List[Dict]:
    """Search company website and info using Yahoo Canada + Bing + DuckDuckGo, strictly filtering out directory domains."""
    clean_emp = re.sub(r"\b(inc|ltd|corp|corporation|llc|limited)\.?\b", "", employer, flags=re.I).strip()
    clean_emp = normalize_text_ascii(clean_emp)
    
    queries = []
    if clean_emp and city:
        queries.append(f'"{clean_emp}" "{city}" official website')
    if clean_emp and province:
        queries.append(f'"{clean_emp}" {province} canada website')
    if clean_emp:
        queries.append(f'"{clean_emp}" canada official website')

    found_results = []
    seen_urls = set()
    session = get_search_session()

    for q in queries:
        # 1. Yahoo Canada
        for r in search_yahoo_canada(q, session):
            href = r.get("href", "")
            if href and href not in seen_urls and not is_excluded_domain(href):
                seen_urls.add(href)
                found_results.append(r)
                
        # 2. Bing
        for r in search_bing(q, session):
            href = r.get("href", "")
            if href and href not in seen_urls and not is_excluded_domain(href):
                seen_urls.add(href)
                found_results.append(r)

        if len(found_results) >= 4:
            break
            
        # 3. DuckDuckGo if needed
        for r in search_duckduckgo_chrome(q, session):
            href = r.get("href", "")
            if href and href not in seen_urls and not is_excluded_domain(href):
                seen_urls.add(href)
                found_results.append(r)

        if len(found_results) >= 5:
            break
        time.sleep(random.uniform(0.2, 0.4))
        
    return found_results[:8]

def extract_contacts_from_url(url: str, session: requests.Session) -> Tuple[List[str], List[str], str]:
    """Fetch web page, return valid company emails, verified Canadian phones, and clean text."""
    emails = []
    phones = []
    text_content = ""
    try:
        res = session.get(url, timeout=(3.0, 5.0))
        if res.status_code == 200:
            html = res.text
            text_content = clean_html_to_text(html)

            # 1. Tel links
            for tel in re.findall(r'href=["\']tel:([^"\'>]+)', html, re.I):
                formatted = validate_and_format_phone(tel)
                if formatted:
                    phones.append(formatted)

            # 2. Mailto links
            for mailto in re.findall(r'href=["\']mailto:([^"?\'>]+)', html, re.I):
                clean_m = mailto.lower().strip()
                if is_valid_email(clean_m) and not is_excluded_email(clean_m):
                    emails.append(clean_m)

            # 3. Regex emails from clean visible text
            for em in EMAIL_REGEX.findall(text_content):
                clean_em = em.lower().strip(".")
                if is_valid_email(clean_em) and not is_excluded_email(clean_em):
                    emails.append(clean_em)

            # 4. Strict Regex phones from clean visible text
            for match in re.finditer(r"(?:\+?1[-.\s]?)?\(?([2-9][0-8][0-9])\)?[-.\s]?([2-9][0-9]{2})[-.\s]?([0-9]{4})", text_content):
                phone_str = f"({match.group(1)}) {match.group(2)}-{match.group(3)}"
                formatted = validate_and_format_phone(phone_str)
                if formatted:
                    phones.append(formatted)

    except Exception:
        pass
        
    return list(set(emails)), list(set(phones)), text_content

def enrich_single_job(job: Dict) -> Dict:
    """Enrich a single job using 2-Stage Agentic AI (SERP Disambiguation + Deep Contact Extraction)."""
    job_id = job.get("job_id")
    employer = job.get("employer_name", "")
    job_title = job.get("job_title", "")
    address = job.get("full_address", "")
    city = job.get("city", "")
    province = job.get("province", "")
    
    print(f"\n[AI Contact Agent] Researching: {employer} | {city}, {province} (Job ID: {job_id})", flush=True)
    
    search_results = search_company_info(employer, address, city, province)
    
    session = requests.Session(impersonate="chrome124")
    best_website = ""
    discovered_emails = []
    discovered_phones = []
    combined_page_text = ""
    ai_used = False

    # Stage 1: AI SERP & Entity Disambiguation Agent
    ai_serp_decision = ai_entity_resolver.resolve_company_serp(
        employer_name=employer,
        job_title=job_title,
        city=city,
        province=province,
        full_address=address,
        search_results=search_results
    )
    
    if ai_serp_decision and ai_serp_decision.get("is_company_identified"):
        ai_used = True
        best_website = ai_serp_decision.get("official_website_url") or ""
        ai_phone = ai_serp_decision.get("verified_phone")
        if ai_phone:
            if ai_entity_resolver.is_phone_matching_province(ai_phone, province):
                fmt = validate_and_format_phone(ai_phone)
                if fmt:
                    discovered_phones.append(fmt)
        print(f"  -> [Stage 1 AI] Identified Domain: {best_website} (Confidence: {ai_serp_decision.get('match_confidence', 0)}%)", flush=True)
        if ai_phone:
            print(f"  -> [Stage 1 AI] Extracted Phone: {ai_phone}", flush=True)
    else:
        # Fallback to Local Maps / Heuristic Check
        print("  -> Stage 1 AI found no direct site in search results. Checking Source 2 (Google Maps / Local Places)...", flush=True)
        maps_data = google_maps_enricher.search_google_maps_place(
            employer_name=employer,
            city=city,
            province=province,
            full_address=address
        )
        if maps_data:
            if maps_data.get("website"):
                best_website = maps_data["website"]
                print(f"  -> Discovered Website via Google Maps: {best_website}", flush=True)
            if maps_data.get("phone"):
                m_phone = maps_data["phone"]
                if ai_entity_resolver.is_phone_matching_province(m_phone, province):
                    fmt = validate_and_format_phone(m_phone)
                    if fmt:
                        discovered_phones.append(fmt)
                        print(f"  -> Discovered Phone via Google Maps: {fmt}", flush=True)

    # If website found, crawl & execute Stage 2 Deep Contact Extraction
    if best_website:
        parsed_url = urlparse(best_website if "://" in best_website else f"https://{best_website}")
        base_origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        # Crawl homepage
        ems, phs, txt = extract_contacts_from_url(best_website, session)
        for em in ems:
            if not is_excluded_email(em):
                discovered_emails.append(em)
        for p in phs:
            if ai_entity_resolver.is_phone_matching_province(p, province):
                discovered_phones.append(p)
        combined_page_text += " " + txt[:2200]

        # Crawl contact/about/career pages if needed
        for sub in ["/contact", "/contact-us", "/about", "/about-us", "/careers", "/jobs", "/nous-joindre"]:
            c_url = urljoin(base_origin, sub)
            c_ems, c_phs, c_txt = extract_contacts_from_url(c_url, session)
            for em in c_ems:
                if not is_excluded_email(em):
                    discovered_emails.append(em)
            for p in c_phs:
                if ai_entity_resolver.is_phone_matching_province(p, province):
                    discovered_phones.append(p)
            combined_page_text += " " + c_txt[:1200]
            if len(discovered_emails) >= 2:
                break

        # Stage 2: Deep Contact Extraction AI Agent
        if combined_page_text.strip():
            ai_contact = ai_entity_resolver.extract_contacts_from_page_ai(
                employer_name=employer,
                website_url=best_website,
                page_text=combined_page_text
            )
            if ai_contact:
                ai_used = True
                c_email = (ai_contact.get("employer_email") or "").strip()
                c_phone = (ai_contact.get("verified_phone") or "").strip()
                if c_email and not is_excluded_email(c_email):
                    discovered_emails.append(c_email)
                if c_phone and ai_entity_resolver.is_phone_matching_province(c_phone, province):
                    fmt = validate_and_format_phone(c_phone)
                    if fmt:
                        discovered_phones.append(fmt)

    # 1. Strict Confidence Scoring & DNS Deliverability Filter
    verified_email_candidates = []
    top_score = 0
    top_grade = ""
    top_confidence_str = ""

    parsed_web_domain = ""
    if best_website:
        parsed_web_domain = urlparse(best_website).netloc.lower().replace("www.", "")

    for em in set(discovered_emails):
        if is_excluded_email(em):
            continue
        em_parts = em.lower().strip().split("@")
        if len(em_parts) != 2:
            continue
        em_domain = em_parts[1].strip(".")

        # Discovered email domain MUST match or be sub-domain of verified company domain if website exists
        if parsed_web_domain:
            if em_domain != parsed_web_domain and not parsed_web_domain.endswith("." + em_domain) and not em_domain.endswith("." + parsed_web_domain):
                continue

        score, grade, explanation = email_verifier.calculate_email_confidence(
            email=em,
            employer_name=employer,
            company_website=best_website,
            source="web",
            is_ai_verified=ai_used
        )

        if score >= 80:  # Minimum 80% confidence threshold
            verified_email_candidates.append((em, score, grade, explanation))

    verified_email_candidates.sort(key=lambda x: x[1], reverse=True)

    # Dedup & format phones (strictly matching province)
    valid_phones = []
    for p in list(set(discovered_phones)):
        fmt = validate_and_format_phone(p)
        if fmt and ai_entity_resolver.is_phone_matching_province(fmt, province):
            if fmt not in valid_phones:
                valid_phones.append(fmt)
    final_phone = ", ".join(valid_phones[:2]) if valid_phones else ""

    if verified_email_candidates:
        final_email = verified_email_candidates[0][0]
        top_score = verified_email_candidates[0][1]
        top_grade = verified_email_candidates[0][2]
        top_confidence_str = f"{top_score}%"
        status = f"Enriched ({top_confidence_str} Verified)"
    elif best_website:
        final_email = ""
        top_confidence_str = "0%"
        top_grade = "NO_EMAIL_DISCOVERED"
        status = "Enriched (Website Discovered)"
    else:
        final_email = ""
        top_confidence_str = "0%"
        top_grade = "NO_STANDALONE_WEBSITE"
        status = "No Standalone Website Found"

    print(f"  -> Website: {best_website or 'N/A'}", flush=True)
    print(f"  -> Verified Email (>=80%): {final_email or 'N/A'} {f'[{top_confidence_str}]' if final_email else ''}", flush=True)
    print(f"  -> Verified Phone (Province Matched): {final_phone or 'N/A'}", flush=True)
    print(f"  -> Status: {status}", flush=True)

    db.update_enrichment(
        job_id=job_id,
        email=final_email,
        website=best_website,
        phone=final_phone,
        status=status,
        email_confidence=top_confidence_str,
        email_verification_status=top_grade
    )

    job["employer_email"] = final_email
    job["company_website"] = best_website
    job["company_phone"] = final_phone
    job["enrichment_status"] = status
    job["email_confidence"] = top_confidence_str
    job["email_verification_status"] = top_grade

    return job

def run_hermes_enrichment(jobs_list: Optional[List[Dict]] = None, concurrency: int = 8) -> List[Dict]:
    """Run Hermes Agent enrichment on active pending or missing-email jobs concurrently."""
    today_str = time.strftime("%Y-%m-%d")
    if jobs_list is None:
        all_active = db.get_all_jobs(active_only=True)
        jobs_to_enrich = [j for j in all_active if j.get("enrichment_status") == "Pending Hermes Enrichment" or not j.get("employer_email")]
    else:
        jobs_to_enrich = [
            j for j in jobs_list 
            if not j.get("employer_email") and (not j.get("advertised_until") or j.get("advertised_until") >= today_str)
        ]

    if GROQ_API_KEY:
        ai_tag = f"Groq AI: {GROQ_MODEL} (OpenRouter fallback)"
    elif OPENROUTER_API_KEY:
        ai_tag = f"OpenRouter AI: {OPENROUTER_MODEL}"
    else:
        ai_tag = "Fast Heuristic Mode (No API Key set)"
    total = len(jobs_to_enrich)
    print(f"\n=======================================================")
    print(f" Hermes Enrichment Agent: Processing {total} active jobs missing email")
    print(f" Mode: {ai_tag} | Concurrency: {concurrency} workers")
    print(f"=======================================================\n")

    if total == 0:
        return []

    enriched_results = []
    completed = 0

    if concurrency <= 1 or total <= 2:
        for idx, job in enumerate(jobs_to_enrich, start=1):
            print(f"\n[{idx}/{total}] Enriching Job ID {job.get('job_id')}...")
            enriched = enrich_single_job(job)
            enriched_results.append(enriched)
            time.sleep(random.uniform(0.3, 0.6))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(enrich_single_job, job): job for job in jobs_to_enrich}
            for future in as_completed(futures):
                try:
                    enriched = future.result()
                    enriched_results.append(enriched)
                    completed += 1
                    if completed % 10 == 0 or completed == total:
                        print(f"[*] Hermes Progress: {completed}/{total} enriched ({(completed/total)*100:.1f}%)")
                except Exception as e:
                    print(f"[!] Error enriching job: {e}")

    print(f"\n[Hermes Agent] Enrichment complete for {len(enriched_results)} jobs.")
    return enriched_results


