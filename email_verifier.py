import re
import dns.resolver
from functools import lru_cache
from typing import Tuple, List

# Disposable / Dummy email patterns
DISCARD_EMAIL_PATTERNS = {
    "user@domain.com", "example@domain.com", "email@domain.com", "name@email.com",
    "info@example.com", "test@test.com", "admin@domain.com", "yourname@domain.com",
    "sample@sample.com", "contact@domain.com"
}

DISCARD_DOMAIN_SUBSTRINGS = [
    "google.com", "googlegroups.com", "sentry.io", "wixpress.com", "cloudflare.com",
    "schema.org", "w3.org", "example.com", "domain.com", "test.com"
]

EMAIL_SYNTAX_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

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
    common_valid_domains = {
        "gmail.com", "yahoo.com", "yahoo.ca", "hotmail.com", "outlook.com", 
        "live.com", "icloud.com", "aol.com", "bell.net", "rogers.com", 
        "shaw.ca", "sympatico.ca", "telus.net", "videotron.ca"
    }
    if domain in common_valid_domains:
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
    if not email or not isinstance(email, str):
        return False, "Empty or non-string email"
        
    email_clean = email.strip().lower().strip(".")
    
    # 1. Regex & Syntax Check
    if not EMAIL_SYNTAX_REGEX.match(email_clean):
        return False, "Invalid syntax"
        
    # 2. File extension false-positives
    if any(email_clean.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".js", ".css", ".webp", ".ico"]):
        return False, "Static asset false positive"
        
    # 3. Dummy / Template Email Check
    if email_clean in DISCARD_EMAIL_PATTERNS:
        return False, "Template / placeholder email"
        
    parts = email_clean.split("@")
    if len(parts) != 2:
        return False, "Malformed email parts"
        
    domain = parts[1]
    
    # 4. Live DNS MX Deliverability Check
    if not is_domain_mx_deliverable(domain):
        return False, f"Domain '{domain}' has no active MX mail exchange records"
        
    return True, "Deliverable (Valid MX)"

def filter_deliverable_emails(email_string: str) -> List[str]:
    """Takes a comma-separated email string or raw text and returns only valid, deliverable emails."""
    if not email_string:
        return []
        
    # Extract potential emails
    raw_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", email_string)
    valid_emails = []
    
    for em in set(raw_emails):
        is_deliverable, reason = verify_email_deliverability(em)
        if is_deliverable:
            valid_emails.append(em.lower().strip())
            
    return sorted(valid_emails)

from concurrent.futures import ThreadPoolExecutor

def prefetch_domain_mx(domains: List[str], max_workers: int = 40):
    """Pre-resolve a batch of unique domains concurrently in parallel."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(is_domain_mx_deliverable, domains))

def verify_and_clean_database_emails() -> Tuple[int, int, int]:
    """
    Scan all emails stored in the SQLite database and verify them against live DNS MX records.
    Returns (total_checked, valid_count, removed_count).
    """
    import db
    db.init_db()
    
    print("=== Scanning Database for Live Email Deliverability (DNS MX Check) ===")
    
    with db.get_db() as conn:
        cursor = conn.execute("SELECT job_id, employer_email, enrichment_status FROM jobs WHERE employer_email != '' AND employer_email IS NOT NULL")
        rows = cursor.fetchall()
        
    total_checked = len(rows)
    print(f"Loaded {total_checked} stored email records from database.")
    
    # Collect all unique domains for parallel prefetching
    all_domains = set()
    for r in rows:
        for em in re.findall(r"@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", r["employer_email"]):
            all_domains.add(em.lower().strip("."))
            
    print(f"Pre-resolving {len(all_domains)} unique domains across {total_checked} emails in parallel...")
    prefetch_domain_mx(list(all_domains))
    print("Domain MX resolution complete. Validating and committing updates...")
    
    valid_count = 0
    removed_count = 0
    
    with db.get_db() as conn:
        for r in rows:
            job_id = r["job_id"]
            raw_email = r["employer_email"]
            status = r["enrichment_status"]
            
            clean_list = filter_deliverable_emails(raw_email)
            if clean_list:
                valid_count += 1
                new_email = ", ".join(clean_list)
                if new_email != raw_email:
                    conn.execute("UPDATE jobs SET employer_email = ? WHERE job_id = ?", (new_email, job_id))
            else:
                removed_count += 1
                new_status = "Undeliverable (Invalid MX)" if "Enriched" in str(status) else "Pending Hermes Enrichment"
                conn.execute("UPDATE jobs SET employer_email = '', enrichment_status = ? WHERE job_id = ?", (new_status, job_id))
                
        conn.commit()
        
    print(f"\n[+] Deliverability Verification Complete:")
    print(f"  - Total Checked:      {total_checked}")
    print(f"  - Verified Valid MX:  {valid_count} ({(valid_count/total_checked*100 if total_checked else 0):.1f}%)")
    print(f"  - Removed / Cleaned:  {removed_count}")
    return total_checked, valid_count, removed_count
