import re
import dns.resolver
from functools import lru_cache
from typing import Tuple, List, Dict, Optional
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

# Disposable / Dummy email patterns
DISCARD_EMAIL_PATTERNS = {
    "user@domain.com", "example@domain.com", "email@domain.com", "name@email.com",
    "info@example.com", "test@test.com", "admin@domain.com", "yourname@domain.com",
    "sample@sample.com", "contact@domain.com", "user@email.com", "ana@company.com",
    "subscribers@yourbrand.com", "press@yourbrand.com"
}

DISCARD_DOMAIN_SUBSTRINGS = [
    "google.com", "googlegroups.com", "sentry.io", "wixpress.com", "cloudflare.com",
    "schema.org", "w3.org", "example.com", "domain.com", "test.com", "wordpress.com",
    "wixsite.com", "squarespace.com", "github.com", "facebook.com", "twitter.com",
    "instagram.com", "linkedin.com", "indeed.com", "glassdoor.com", "jobbank.gc.ca"
]

GENERIC_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "yahoo.ca", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "aol.com", "bell.net", "rogers.com",
    "shaw.ca", "sympatico.ca", "telus.net", "videotron.ca", "protonmail.com", "zoho.com"
}

HIGH_INTENT_PREFIXES = {
    "careers", "career", "hiring", "jobs", "job", "hr", "recruitment", "recruiting",
    "resume", "resumes", "apply", "employment", "info", "contact", "admin", "office",
    "management", "general", "work", "operations", "help", "support", "inquiry", "inquiries"
}

GENERIC_NAME_WORDS = {
    "inc", "ltd", "corp", "corporation", "llc", "limited", "canada", "canadian",
    "services", "solutions", "enterprises", "holdings", "management", "consulting",
    "group", "company", "the", "and", "co", "ca", "trading", "international", "north"
}

EMAIL_SYNTAX_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

def is_valid_syntax(email: str) -> bool:
    """Check whether string matches valid RFC email regex syntax."""
    if not email or not isinstance(email, str):
        return False
    email_clean = email.strip().lower().strip(".")
    if not EMAIL_SYNTAX_REGEX.match(email_clean):
        return False
    if any(email_clean.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".js", ".css", ".webp", ".ico"]):
        return False
    return True

@lru_cache(maxsize=8192)
def is_domain_mx_deliverable(domain: str) -> bool:
    """Check if domain has active MX (Mail Exchange) or A records with DNS caching."""
    if not domain or "." not in domain:
        return False
        
    domain = domain.lower().strip(".")
    
    # Check known domain blocklist
    if any(blocked in domain for blocked in DISCARD_DOMAIN_SUBSTRINGS):
        return False
        
    # High-trust popular email providers bypass DNS lookup
    if domain in GENERIC_EMAIL_PROVIDERS:
        return True

    # Live DNS Query
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=3.0)
        if len(answers) > 0:
            return True
    except Exception:
        pass
        
    # Fallback to A record (RFC 5321 standard fallback if MX is missing)
    try:
        answers = dns.resolver.resolve(domain, "A", lifetime=2.0)
        if len(answers) > 0:
            return True
    except Exception:
        pass
        
    return False

def verify_email_deliverability(email: str) -> Tuple[bool, str]:
    """
    Validate email syntax, check domain blocklists, and verify live DNS MX records.
    Returns (is_valid, reason).
    """
    if not is_valid_syntax(email):
        return False, "Invalid email syntax or file extension"
        
    email_clean = email.strip().lower().strip(".")
    
    # Dummy / Template Email Check
    if email_clean in DISCARD_EMAIL_PATTERNS:
        return False, "Template / placeholder email"
        
    parts = email_clean.split("@")
    if len(parts) != 2:
        return False, "Malformed email parts"
        
    domain = parts[1]
    
    # Live DNS MX Deliverability Check
    if not is_domain_mx_deliverable(domain):
        return False, f"Domain '{domain}' has no active MX mail exchange records"
        
    return True, "Deliverable (Valid MX)"

def calculate_email_confidence(
    email: str,
    employer_name: str = "",
    company_website: str = "",
    source: str = "web",
    is_ai_verified: bool = False
) -> Tuple[int, str, str]:
    """
    Calculate an objective Confidence Score (0 - 100%) and Verification Grade for an email.
    
    Returns:
        (score: int, grade: str, explanation: str)
        - score: 0 to 100
        - grade: 'DIRECT_JOBBANK', 'VERIFIED_HIGH', 'VERIFIED_GOOD', 'LOW_CONFIDENCE', 'INVALID_UNDELIVERABLE'
        - explanation: Short human-readable reasoning string
    """
    if not email or not is_valid_syntax(email):
        return 0, "INVALID_UNDELIVERABLE", "Invalid email syntax"
        
    email_clean = email.strip().lower().strip(".")
    if email_clean in DISCARD_EMAIL_PATTERNS:
        return 0, "INVALID_UNDELIVERABLE", "Placeholder / sample email"

    parts = email_clean.split("@")
    if len(parts) != 2:
        return 0, "INVALID_UNDELIVERABLE", "Malformed email structure"

    prefix, domain = parts[0], parts[1]
    
    # 1. DNS MX Deliverability Check (Hard Requirement)
    if not is_domain_mx_deliverable(domain):
        return 0, "INVALID_UNDELIVERABLE", f"Domain '{domain}' has no active MX records (Undeliverable)"

    # 2. Direct Extraction from ESDC Job Bank Posting
    if source == "direct_jobbank":
        return 100, "DIRECT_JOBBANK", "100% (Direct JobBank Posting with Active MX)"

    # 3. Hermes / Web Sourced Confidence Rubric (Score builds up)
    score = 0
    reasons = []

    # A. Active DNS MX Check Passed
    score += 30
    reasons.append("Active MX")

    # B. Domain Match with Company Website / Employer Name
    website_domain = ""
    if company_website:
        parsed = urlparse(company_website if "://" in company_website else f"https://{company_website}")
        website_domain = (parsed.netloc or "").lower()
        if website_domain.startswith("www."):
            website_domain = website_domain[4:]
        website_domain = website_domain.split(":")[0].strip()

    is_domain_match = False
    if website_domain and (domain == website_domain or domain.endswith("." + website_domain) or website_domain.endswith("." + domain)):
        score += 40
        is_domain_match = True
        reasons.append(f"Domain Match ({domain})")
    elif employer_name:
        # Check tokens of employer name
        emp_clean = re.sub(r"[^a-zA-Z0-9\s]", " ", employer_name.lower())
        tokens = [t for t in emp_clean.split() if len(t) >= 3 and t not in GENERIC_NAME_WORDS]
        domain_clean = domain.replace("-", "").replace(".", "")
        if tokens and any(t in domain_clean for t in tokens):
            score += 35
            is_domain_match = True
            reasons.append(f"Employer Name Match in Domain")

    # C. Hiring / Corporate Department Prefix
    is_high_intent = any(p in prefix for p in HIGH_INTENT_PREFIXES)
    if is_high_intent:
        score += 20
        reasons.append(f"Hiring Prefix '{prefix}@'")
    else:
        # Standard corporate name pattern (e.g. first.last@company.com)
        if is_domain_match and ("." in prefix or "_" in prefix or len(prefix) >= 4):
            score += 15
            reasons.append(f"Corporate Identity '{prefix}@'")

    # D. OpenRouter AI Verification
    if is_ai_verified:
        score += 15
        reasons.append("AI Verified Contact")

    # E. Free Webmail Penalty (gmail, yahoo, etc.) when found via web scraping
    if domain in GENERIC_EMAIL_PROVIDERS:
        if not is_ai_verified and not is_high_intent:
            score = min(score, 60)
            reasons.append("Generic Webmail (Unverified)")
        elif is_ai_verified and is_high_intent:
            score = 85
            reasons.append("AI Confirmed Official Webmail")
        else:
            score = min(score, 75)

    # Cap score at 100
    final_score = min(100, score)

    # Determine verification grade
    if final_score >= 90:
        grade = "VERIFIED_HIGH"
    elif final_score >= 80:
        grade = "VERIFIED_GOOD"
    else:
        grade = "LOW_CONFIDENCE"

    explanation = f"{final_score}% ({' + '.join(reasons)})"
    return final_score, grade, explanation

def filter_deliverable_emails(
    email_string: str,
    min_confidence: int = 80,
    employer_name: str = "",
    company_website: str = "",
    source: str = "web"
) -> List[Tuple[str, int, str]]:
    """
    Takes a comma-separated email string or raw text and returns only verified emails
    that meet or exceed the minimum confidence threshold (default: 80%).
    
    Returns List of tuples: [(email, confidence_score, explanation), ...]
    """
    if not email_string:
        return []
        
    raw_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", str(email_string))
    qualified = []
    
    for em in set(raw_emails):
        em_clean = em.lower().strip()
        score, grade, explanation = calculate_email_confidence(
            email=em_clean,
            employer_name=employer_name,
            company_website=company_website,
            source=source
        )
        if score >= min_confidence:
            qualified.append((em_clean, score, explanation))
            
    # Sort by highest confidence score first
    qualified.sort(key=lambda x: x[1], reverse=True)
    return qualified

def prefetch_domain_mx(domains: List[str], max_workers: int = 40):
    """Pre-resolve a batch of unique domains concurrently in parallel."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(is_domain_mx_deliverable, domains))

def verify_and_clean_database_emails(min_confidence: int = 80) -> Tuple[int, int, int]:
    """
    Scan all emails stored in the database, calculate confidence scores,
    and remove or flag low-confidence (< min_confidence) records.
    Returns (total_checked, valid_count, removed_count).
    """
    import db
    db.init_db()
    
    print(f"=== Scanning Database with Confidence Scoring Engine (Threshold: >={min_confidence}%) ===")
    
    with db.get_db() as conn:
        if db.is_postgres():
            with conn.cursor() as cur:
                cur.execute("SELECT job_id, employer_name, employer_email, company_phone, company_website, enrichment_status FROM jobs WHERE employer_email != '' AND employer_email IS NOT NULL")
                rows = [dict(r) for r in cur.fetchall()]
        else:
            cursor = conn.execute("SELECT job_id, employer_name, employer_email, company_phone, company_website, enrichment_status FROM jobs WHERE employer_email != '' AND employer_email IS NOT NULL")
            rows = [dict(r) for r in cursor.fetchall()]
        
    total_checked = len(rows)
    print(f"Loaded {total_checked} stored email records from database.")
    
    # Collect all unique domains for parallel prefetching
    all_domains = set()
    for r in rows:
        for em in re.findall(r"@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", r.get("employer_email", "")):
            all_domains.add(em.lower().strip("."))
            
    print(f"Pre-resolving {len(all_domains)} unique domains across {total_checked} emails in parallel...")
    prefetch_domain_mx(list(all_domains))
    print("Domain MX resolution complete. Evaluating confidence scores and committing updates...")
    
    valid_count = 0
    removed_count = 0
    
    for r in rows:
        job_id = r["job_id"]
        raw_email = r.get("employer_email", "")
        employer = r.get("employer_name", "")
        website = r.get("company_website", "")
        status = r.get("enrichment_status", "")
        
        # Source detection
        src = "direct_jobbank" if "JobBank" in str(status) or "Direct" in str(status) else "web"
        
        qualified = filter_deliverable_emails(
            raw_email,
            min_confidence=min_confidence,
            employer_name=employer,
            company_website=website,
            source=src
        )
        
        if qualified:
            valid_count += 1
            top_emails = [q[0] for q in qualified[:2]]
            new_email = ", ".join(top_emails)
            top_score = qualified[0][1]
            top_exp = qualified[0][2]
            grade = "VERIFIED_HIGH" if top_score >= 90 else "VERIFIED_GOOD"
            confidence_tag = f"{top_score}%"
            
            db.update_enrichment(
                job_id=job_id,
                email=new_email,
                website=website,
                phone=r.get("company_phone", ""),
                status=f"Verified ({confidence_tag})" if "Direct" not in str(status) else status,
                email_confidence=confidence_tag,
                email_verification_status=grade
            )
        else:
            removed_count += 1
            db.update_enrichment(
                job_id=job_id,
                email="",
                website=website,
                phone=r.get("company_phone", ""),
                status="Low Confidence (Filtered <80%)",
                email_confidence="0%",
                email_verification_status="REJECTED_LOW_CONFIDENCE"
            )
            
    print(f"\n[+] Confidence Verification & Database Cleaning Complete:")
    print(f"  - Total Checked:            {total_checked}")
    print(f"  - Verified High Confidence: {valid_count} ({(valid_count/total_checked*100 if total_checked else 0):.1f}%)")
    print(f"  - Low Confidence Filtered:  {removed_count}")
    return total_checked, valid_count, removed_count

