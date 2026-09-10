import os
import re
import json
import time
import random
import sqlite3
import threading
import unicodedata
from typing import Optional, Dict, List, Any
from curl_cffi import requests
from dotenv import load_dotenv

load_dotenv(override=True)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_PRIMARY_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
GROQ_FALLBACK_POOL = [
    GROQ_PRIMARY_MODEL,
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant"
]

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_PRIMARY_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()
OPENROUTER_FALLBACK_POOL = [
    OPENROUTER_PRIMARY_MODEL,
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free"
]

CACHE_DB_FILE = "ai_cache.db"

# -----------------------------------------------------------------------------
# Canadian Province & Area Code Mapping
# -----------------------------------------------------------------------------
PROVINCE_CODES = {
    "alberta": "AB", "ab": "AB",
    "british columbia": "BC", "bc": "BC",
    "manitoba": "MB", "mb": "MB",
    "new brunswick": "NB", "nb": "NB",
    "newfoundland and labrador": "NL", "newfoundland": "NL", "labrador": "NL", "nl": "NL",
    "nova scotia": "NS", "ns": "NS",
    "northwest territories": "NT", "nt": "NT",
    "nunavut": "NU", "nu": "NU",
    "ontario": "ON", "on": "ON",
    "prince edward island": "PE", "pei": "PE", "pe": "PE",
    "quebec": "QC", "québec": "QC", "qc": "QC",
    "saskatchewan": "SK", "sk": "SK",
    "yukon": "YT", "yt": "YT"
}

PROVINCE_AREA_CODES = {
    "AB": {"403", "587", "780", "825", "368"},
    "BC": {"236", "250", "604", "672", "778"},
    "MB": {"204", "431", "584"},
    "NB": {"506", "343"},
    "NL": {"709", "879"},
    "NS": {"902", "782"},
    "NT": {"867"},
    "NU": {"867"},
    "ON": {"226", "249", "289", "343", "365", "416", "437", "519", "548", "613", "647", "705", "753", "807", "905", "382"},
    "PE": {"902", "782"},
    "QC": {"418", "438", "450", "514", "579", "581", "819", "873", "354", "367"},
    "SK": {"306", "639", "475"},
    "YT": {"867"},
    "TOLL_FREE": {"800", "888", "877", "866", "855", "844", "833"}
}

def is_phone_matching_province(raw_phone: str, province_str: str = "") -> bool:
    """Validate that phone number belongs to the designated Canadian province or Toll-Free."""
    if not raw_phone:
        return False
    digits = re.sub(r"[^\d]", "", str(raw_phone))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return False
    area_code = digits[:3]
    if area_code in PROVINCE_AREA_CODES["TOLL_FREE"]:
        return True
    
    if not province_str:
        all_ca = set().union(*PROVINCE_AREA_CODES.values())
        return area_code in all_ca
        
    norm_p = normalize_text_ascii(province_str.lower().strip())
    p_code = PROVINCE_CODES.get(norm_p, norm_p.upper())
    valid_codes = PROVINCE_AREA_CODES.get(p_code)
    if valid_codes:
        return area_code in valid_codes
        
    all_ca = set().union(*PROVINCE_AREA_CODES.values())
    return area_code in all_ca

# -----------------------------------------------------------------------------
# 1. Thread-Safe Token Bucket Rate Limiters
# -----------------------------------------------------------------------------
class TokenBucketRateLimiter:
    """Thread-safe rate limiter to strictly prevent HTTP 429 rate limit breaches."""
    def __init__(self, max_rate: float = 15.0 / 60.0, capacity: float = 2.0):
        self.max_rate = max_rate  # Tokens added per second
        self.capacity = capacity  # Max burst tokens
        self.tokens = capacity
        self.last_time = time.time()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_time
            self.last_time = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.max_rate)
            
            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.max_rate
                time.sleep(wait_time)
                self.tokens = 0.0
            else:
                self.tokens -= 1.0

_GROQ_RATE_LIMITER = TokenBucketRateLimiter(max_rate=28.0 / 60.0, capacity=2.0)
_OPENROUTER_RATE_LIMITER = TokenBucketRateLimiter(max_rate=16.0 / 60.0, capacity=2.0)

# -----------------------------------------------------------------------------
# 2. Persistent SQLite Entity Resolution Cache
# -----------------------------------------------------------------------------
def get_cache_db() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_cache_db():
    with get_cache_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_entity_cache (
                cache_key TEXT PRIMARY KEY,
                stage TEXT,
                data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

init_cache_db()

def normalize_text_ascii(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize('NFKD', str(text)).encode('ASCII', 'ignore').decode('utf-8').strip()

def get_cached_result(cache_key: str, stage: str) -> Optional[Dict]:
    try:
        with get_cache_db() as conn:
            cursor = conn.execute("SELECT data_json FROM ai_entity_cache WHERE cache_key = ? AND stage = ?", (cache_key, stage))
            row = cursor.fetchone()
            if row and row["data_json"]:
                return json.loads(row["data_json"])
    except Exception:
        pass
    return None

def set_cached_result(cache_key: str, stage: str, data: Dict):
    try:
        with get_cache_db() as conn:
            conn.execute("""
                INSERT INTO ai_entity_cache (cache_key, stage, data_json)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET data_json = excluded.data_json
            """, (cache_key, stage, json.dumps(data)))
            conn.commit()
    except Exception:
        pass

# -----------------------------------------------------------------------------
# 3. Robust LLM Invocation & Multi-Engine Dispatcher
# -----------------------------------------------------------------------------
def extract_json_response(raw_content: str) -> Optional[Dict[str, Any]]:
    """Extract and parse structured JSON from LLM markdown codeblocks or raw output."""
    if not raw_content:
        return None
        
    # 1. Codeblock markdown ```json { ... } ```
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except Exception:
            pass
            
    # 2. First { to last }
    s = raw_content.find("{")
    e = raw_content.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(raw_content[s:e+1])
        except Exception:
            pass
            
    return None

def call_groq_llm(
    prompt: str,
    system_prompt: str,
    model_override: Optional[str] = None,
    max_tokens: int = 500
) -> Optional[Dict[str, Any]]:
    """
    Execute LLM call against Groq API (sub-0.6s latency, 14.4k req/day free tier).
    """
    api_key = GROQ_API_KEY
    if not api_key:
        return None

    target_models = [model_override] if model_override else []
    for m in GROQ_FALLBACK_POOL:
        if m not in target_models:
            target_models.append(m)

    session = requests.Session(impersonate="chrome124")

    for model in target_models:
        for attempt in range(1, 4):
            _GROQ_RATE_LIMITER.acquire()
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens
            }
            try:
                res = session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=15.0
                )
                if res.status_code == 200:
                    resp_json = res.json()
                    choices = resp_json.get("choices", [])
                    if choices:
                        raw_content = choices[0].get("message", {}).get("content", "")
                        parsed = extract_json_response(raw_content)
                        if parsed:
                            return parsed
                elif res.status_code in (429, 503):
                    retry_after = res.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        backoff = float(retry_after) + random.uniform(0.5, 1.5)
                    else:
                        backoff = min((2 ** attempt) + random.uniform(0.5, 1.5), 15.0)
                    print(f"    [Groq AI Throttling] Model {model} returned HTTP {res.status_code}. Backing off {backoff:.1f}s...")
                    time.sleep(backoff)
                    continue
                else:
                    break
            except Exception:
                time.sleep(1.0)

    return None

def call_openrouter_llm(
    prompt: str,
    system_prompt: str,
    model_override: Optional[str] = None,
    max_tokens: int = 500
) -> Optional[Dict[str, Any]]:
    """
    Call OpenRouter LLM as secondary fallback with rate limiting, backoff, and model fallback.
    """
    api_key = OPENROUTER_API_KEY
    if not api_key:
        return None

    target_models = [model_override] if model_override else []
    for m in OPENROUTER_FALLBACK_POOL:
        if m not in target_models:
            target_models.append(m)

    session = requests.Session(impersonate="chrome124")

    for model in target_models:
        for attempt in range(1, 4):
            _OPENROUTER_RATE_LIMITER.acquire()
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/job-scraper",
                "X-Title": "Canada LMIA AI Scraper"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens
            }
            try:
                res = session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=20.0
                )
                if res.status_code == 200:
                    resp_json = res.json()
                    choices = resp_json.get("choices", [])
                    if choices:
                        raw_content = choices[0].get("message", {}).get("content") or choices[0].get("message", {}).get("reasoning") or ""
                        parsed = extract_json_response(raw_content)
                        if parsed:
                            return parsed
                elif res.status_code in (429, 503):
                    if "free-models-per-day" in res.text or res.headers.get("x-ratelimit-remaining") == "0":
                        print("    [OpenRouter Notice] Daily free account limit reached.")
                        return None
                    retry_after = res.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        backoff = float(retry_after) + random.uniform(0.5, 1.5)
                    else:
                        backoff = min((2 ** attempt) + random.uniform(1.0, 3.0), 30.0)
                    time.sleep(backoff)
                    continue
                else:
                    break
            except Exception:
                time.sleep(1.5)
                
    return None

def call_ai_engine(
    prompt: str,
    system_prompt: str,
    max_tokens: int = 500
) -> Optional[Dict[str, Any]]:
    """
    Tiered AI Engine Dispatcher:
    1. Primary: Groq API (qwen/qwen3.8-27b, 0.5s latency, 14.4k req/day)
    2. Secondary Fallback: OpenRouter API (openrouter/free)
    """
    # 1. Try Groq Primary
    if GROQ_API_KEY:
        groq_result = call_groq_llm(prompt=prompt, system_prompt=system_prompt, max_tokens=max_tokens)
        if groq_result:
            return groq_result

    # 2. Try OpenRouter Fallback
    if OPENROUTER_API_KEY:
        openrouter_result = call_openrouter_llm(prompt=prompt, system_prompt=system_prompt, max_tokens=max_tokens)
        if openrouter_result:
            return openrouter_result

    return None

# -----------------------------------------------------------------------------
# 4. Stage 1: Entity & SERP Disambiguation Agent
# -----------------------------------------------------------------------------
SERP_SYSTEM_PROMPT = """You are an expert Canadian commercial entity resolution intelligence engine.
TASK: Analyze the target Canadian employer's job posting metadata alongside the provided search engine results to identify the authentic official company website and verified business telephone number.

CRITICAL RULES:
1. REJECT DIRECTORIES & AGGREGATORS: Never return third-party business directories (YellowPages, PagesJaunes, Yelp, Infobel, Dun & Bradstreet, Cylex, FindGlocal, Cybo, MapQuest, ZoomInfo, BBB, Kompass, Kijiji, Job Bank aggregators). If a result is a directory listing, skip it!
2. FRANCHISES & NETWORKS: If the employer is an independent pharmacy, clinic, dealership, or franchise operating under a parent network (e.g. RemedysRx, Guardian, Re/Max, Home Hardware, Tim Hortons), the official location page (e.g. guardian-ida-remedysrx.ca/.../mell-pharmacy) is valid.
3. DOMAIN ACCURACY: The website MUST genuinely belong to this specific employer in this Canadian city/province (e.g. Lekker Food Distributors in Richmond, BC -> lekkerfoods.com).
4. VERIFIED PHONE: Extract the direct telephone number for this business location matching the Canadian province.
5. If no authentic official website exists, set official_website_url to null and is_company_identified to false.

OUTPUT JSON FORMAT ONLY:
{
  "is_company_identified": true,
  "official_website_url": "https://company.com",
  "verified_phone": "(XXX) XXX-XXXX",
  "match_confidence": 95,
  "reasoning": "Result 1 is the direct official website of Company in City, Province."
}"""

def resolve_company_serp(
    employer_name: str,
    job_title: str,
    city: str,
    province: str,
    full_address: str,
    search_results: List[Dict[str, str]]
) -> Dict[str, Any]:
    """
    Stage 1 AI Agent: Disambiguate search results to find the official company website and verified phone.
    """
    cache_key = f"{normalize_text_ascii(employer_name.lower())}|{normalize_text_ascii(city.lower())}"
    cached = get_cached_result(cache_key, "stage1_serp")
    if cached:
        return cached

    if not search_results:
        return {"is_company_identified": False, "official_website_url": None, "verified_phone": None, "match_confidence": 0, "reasoning": "No search results provided"}

    results_text = ""
    for idx, r in enumerate(search_results[:5], start=1):
        title = r.get("title", "")
        url = r.get("href", "")
        snippet = r.get("snippet", "")
        results_text += f"\n[Result {idx}]\nTitle: {title}\nURL: {url}\nSnippet: {snippet}\n"

    prompt = (
        f"TARGET CANADIAN EMPLOYER METADATA:\n"
        f"Company Name: {employer_name}\n"
        f"Job Title: {job_title}\n"
        f"City: {city}\n"
        f"Province: {province}\n"
        f"Physical Address: {full_address}\n\n"
        f"SEARCH ENGINE RESULTS (Top 5):\n{results_text}\n\n"
        f"Determine if any result contains the authentic official website or verified local telephone number for this Canadian business.\n"
        f"JSON Output:"
    )

    ai_data = call_ai_engine(prompt=prompt, system_prompt=SERP_SYSTEM_PROMPT, max_tokens=500)
    
    if ai_data and isinstance(ai_data, dict):
        set_cached_result(cache_key, "stage1_serp", ai_data)
        return ai_data

    return {"is_company_identified": False, "official_website_url": None, "verified_phone": None, "match_confidence": 0, "reasoning": "AI evaluation bypassed or unavailable"}

# -----------------------------------------------------------------------------
# 5. Stage 2: Deep Contact & Email Extraction Agent
# -----------------------------------------------------------------------------
CONTACT_SYSTEM_PROMPT = """You are a contact intelligence extraction engine for Canadian corporate websites.
TASK: Extract direct corporate, recruitment, management, or primary contact email addresses and telephone numbers from the provided webpage text.

RULES:
1. Extract authentic company email addresses for HR, careers, hiring, general inquiries, or managers.
2. DISCARD 3rd-party web developer/platform emails (e.g. support@wix.com, info@wordpress.org, privacy@shopify.com).
3. If no authentic corporate email is found on the page, set employer_email to null.

OUTPUT JSON FORMAT ONLY:
{
  "employer_email": "careers@company.com",
  "email_type": "recruitment_direct",
  "email_confidence": 95,
  "contact_name_or_dept": "Human Resources",
  "verified_phone": "(XXX) XXX-XXXX"
}"""

def extract_contacts_from_page_ai(
    employer_name: str,
    website_url: str,
    page_text: str
) -> Dict[str, Any]:
    """
    Stage 2 AI Agent: Extract verified HR/recruiter/corporate emails from crawled webpage text.
    """
    if not page_text or len(page_text.strip()) < 30:
        return {}

    cache_key = f"{normalize_text_ascii(employer_name.lower())}|{website_url.lower()}"
    cached = get_cached_result(cache_key, "stage2_contact")
    if cached:
        return cached

    clean_text = page_text[:2200]
    prompt = (
        f"Company Name: {employer_name}\n"
        f"Website URL: {website_url}\n\n"
        f"Webpage Content Snippet:\n{clean_text}\n\n"
        f"Extract direct company recruitment/HR/business contact info.\n"
        f"JSON Output:"
    )

    ai_data = call_ai_engine(prompt=prompt, system_prompt=CONTACT_SYSTEM_PROMPT, max_tokens=400)
    
    if ai_data and isinstance(ai_data, dict):
        set_cached_result(cache_key, "stage2_contact", ai_data)
        return ai_data

    return {}
