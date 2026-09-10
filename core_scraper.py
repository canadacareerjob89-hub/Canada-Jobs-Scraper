import csv
import re
import time
import random
from typing import List, Dict, Optional, Set
from curl_cffi import requests
from scrapling import Selector
import db
import email_verifier
import data_cleaner

def fetch_with_retry(session: requests.Session, url: str, method: str = "GET", headers: dict = None, data: dict = None, max_retries: int = 3):
    """Fetch URL with rate-limit detection (429) and exponential backoff retry."""
    for attempt in range(1, max_retries + 1):
        try:
            if method == "POST":
                res = session.post(url, headers=headers, data=data, timeout=15)
            else:
                res = session.get(url, headers=headers, timeout=15)
                
            if res.status_code == 429:
                wait_time = (2 ** attempt) + random.uniform(1.5, 3.5)
                print(f"    [Rate-limit 429 detected] Pausing for {wait_time:.1f}s before retry {attempt}/{max_retries}...")
                time.sleep(wait_time)
                continue
            elif res.status_code == 503:
                time.sleep(3 + random.uniform(1, 2))
                continue
                
            return res
        except Exception as e:
            if attempt == max_retries:
                raise e
            time.sleep(2 * attempt)
    return None

def scrape_lmia_jobs(limit: Optional[int] = None, 
                     stop_on_seen: bool = True, 
                     max_consecutive_seen: int = 5) -> List[Dict]:
    session = requests.Session(impersonate="chrome124")
    db_ids = db.get_seen_job_ids()
    
    new_jobs = []
    page_num = 1
    consecutive_seen = 0
    total_processed = 0
    
    print("=== Starting LMIA Job Bank Scraper (sort=D, fskl=101010) ===")
    if stop_on_seen:
        print(f"Deduplication active: {len(db_ids)} already seen jobs in DB.")
    
    while True:
        search_url = f"https://www.jobbank.gc.ca/jobsearch/jobsearch?page={page_num}&sort=D&fskl=101010"
        print(f"\n[---] Fetching search page {page_num}...")
        try:
            res = fetch_with_retry(session, search_url)
            if not res or res.status_code != 200:
                print(f"Failed to fetch page {page_num} (Status: {res.status_code if res else 'No response'})")
                break
        except Exception as e:
            print(f"Error fetching page {page_num}: {e}")
            break
            
        sel = Selector(res.text)
        articles = sel.css("article")
        if not articles:
            print("No more jobs found.")
            break
            
        for art in articles:
            if limit and len(new_jobs) >= limit:
                print(f"Reached requested limit of {limit} jobs.")
                return new_jobs
                
            link = art.css("a.resultJobItem::attr(href)").get() or art.css("a::attr(href)").get()
            if not link:
                continue
                
            job_id_match = re.search(r"/jobposting/(\d+)", link)
            job_id = job_id_match.group(1) if job_id_match else ""
            if not job_id:
                continue
                
            # Deduplication Check
            if stop_on_seen and job_id in db_ids:
                consecutive_seen += 1
                if consecutive_seen >= max_consecutive_seen:
                    print(f"Encountered {consecutive_seen} previously seen jobs in a row. Halting scraper.")
                    return new_jobs
                continue
            elif not stop_on_seen and job_id in db_ids:
                continue
            else:
                consecutive_seen = 0
                
            card_employer = art.css("li.business::text").get() or ""
            card_date = art.css("li.date::text").get() or ""
            job_url = f"https://www.jobbank.gc.ca/jobsearch/jobposting/{job_id}"
            total_processed += 1
            print(f" [{total_processed}] Scraping Job ID {job_id}...")
            
            try:
                detail_res = fetch_with_retry(session, job_url)
                if not detail_res or detail_res.status_code != 200:
                    continue
                dsel = Selector(detail_res.text)
                
                # Title
                title = dsel.css('h1 span[property="title"]::text').get() or dsel.css("h1::text").get()
                title = title.strip() if title else ""
                
                # NOC Code
                noc_no = dsel.css('span.noc-no::text').get() or ''
                raw_code = dsel.css('span.aa_jobbank_job_noccode::text').get() or ''
                noc_code = noc_no.strip() if noc_no else (f'NOC {raw_code.strip()}' if raw_code else '')

                # Employer
                employer = card_employer.strip()
                if not employer:
                    emp_match = re.search(r"for\s*Employer\d*?\s*details\s*([^\n\r]+)", detail_res.text)
                    if emp_match:
                        employer = emp_match.group(1).strip()
                
                # Location / Address
                city = dsel.css('span[property="addressLocality"]::text').get() or ""
                province = dsel.css('span[property="addressRegion"]::text').get() or ""
                postal = dsel.css('span[property="postalCode"]::text').get() or ""
                street = dsel.css('span[property="streetAddress"]::text').get() or ""
                full_address = ", ".join(filter(None, [street.strip(), city.strip(), province.strip(), postal.strip()]))
                
                # Salary & Terms
                terms = ""
                vacancies = ""
                start_date = ""
                shifts = ""
                salary = ""
                for li in dsel.css("ul.job-posting-brief li"):
                    txt = " ".join(li.css("::text").getall()).strip()
                    txt = re.sub(r"\s+", " ", txt)
                    if "salary" in txt.lower() or "$" in txt:
                        salary = re.sub(r"Salary\r\n|Salary", "", txt).strip()
                    elif "terms of employment" in txt.lower():
                        terms = re.sub(r"Terms of employment", "", txt).strip()
                    elif "vacanc" in txt.lower():
                        vac_m = re.search(r"\d+", txt)
                        vacancies = vac_m.group(0) if vac_m else "1"
                    elif "start" in txt.lower():
                        start_date = txt
                    elif any(s in txt.lower() for s in ["morning", "day", "evening", "night", "shift", "weekend"]) and "weeks" not in txt.lower():
                        shifts = txt
                
                # Check for expired or inactive posting notice
                page_lower = detail_res.text.lower()
                if any(p in page_lower for p in ["posting has expired", "is no longer available", "poste n'est plus disponible", "poste a expiré", "posting is inactive"]):
                    print(f"    [Expired/Inactive Notice] Job ID {job_id} is no longer active. Skipping.")
                    continue
                
                # Advertised Until
                adv_until = dsel.css('p[property="validThrough"]::text').get() or ""
                adv_until = adv_until.strip()
                today_str = time.strftime("%Y-%m-%d")
                if adv_until and re.match(r"^\d{4}-\d{2}-\d{2}$", adv_until) and adv_until < today_str:
                    print(f"    [Expired Date] Job ID {job_id} expired on {adv_until} (Today: {today_str}). Skipping.")
                    continue
                
                # Native Email AJAX POST
                headers = {
                    "Faces-Request": "partial/ajax",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": "https://www.jobbank.gc.ca",
                    "Referer": job_url,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                }
                data = {
                    "seekeractivity:jobid": str(job_id),
                    "seekeractivity_SUBMIT": "1",
                    "jakarta.faces.ViewState": "stateless",
                    "jakarta.faces.behavior.event": "action",
                    "action": "applynowbutton",
                    "jakarta.faces.partial.event": "click",
                    "jakarta.faces.source": "seekeractivity",
                    "jakarta.faces.partial.ajax": "true",
                    "jakarta.faces.partial.execute": "jobid",
                    "jakarta.faces.partial.render": "applynow markappliedgroup",
                    "seekeractivity": "seekeractivity"
                }
                
                employer_email = ""
                apply_text = ""
                enrichment_status = "No Direct Email"
                
                try:
                    ajax_res = fetch_with_retry(session, job_url, method="POST", headers=headers, data=data)
                    if ajax_res and ajax_res.status_code == 200:
                        emails = re.findall(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b", ajax_res.text)
                        valid_emails = [em.lower() for em in emails if "jobbank.gc.ca" not in em.lower() and email_verifier.is_valid_syntax(em)]
                        
                        if valid_emails:
                            employer_email = valid_emails[0]
                            enrichment_status = "Direct JobBank"
                        
                        as_clean = re.sub(r"<[^>]+>", " ", ajax_res.text)
                        apply_text = re.sub(r"\s+", " ", as_clean).strip()
                except Exception:
                    pass
                
                job_record = {
                    "job_id": str(job_id),
                    "job_title": title,
                    "employer_name": employer,
                    "employer_email": employer_email,
                    "noc_code": noc_code,
                    "city": city.strip(),
                    "province": province.strip(),
                    "full_address": full_address,
                    "salary": salary,
                    "job_type": terms,
                    "work_hours_shifts": shifts,
                    "vacancies": vacancies,
                    "start_date": start_date,
                    "lmia_status": "LMIA requested",
                    "date_posted": card_date.strip(),
                    "advertised_until": adv_until,
                    "how_to_apply_instructions": apply_text[:300],
                    "job_url": job_url,
                    "company_website": "",
                    "company_phone": "",
                    "enrichment_status": enrichment_status
                }
                
                job_record = data_cleaner.clean_job_dict(job_record)
                db.save_job(job_record)
                db_ids.add(job_id)
                new_jobs.append(job_record)
                
                status_tag = f"Email: {employer_email}" if employer_email else "[No Direct Email]"
                print(f"    + {title} | {employer} | {status_tag}")
                
                # Polite randomized delay (1.0s to 2.2s)
                time.sleep(random.uniform(1.0, 2.2))
            except Exception as e:
                print(f"    ! Error processing {job_id}: {e}")
                
        print(f"--- Page {page_num} finished. Total new jobs scraped this session: {len(new_jobs)} | Total in DB: {len(db_ids)} ---")
        page_num += 1
        # Inter-page pause
        time.sleep(random.uniform(1.5, 3.0))
        
    return new_jobs
