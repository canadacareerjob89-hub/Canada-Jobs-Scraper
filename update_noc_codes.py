import time
import random
import re
import sys
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests
from scrapling import Selector
import db
import main

sys.stdout.reconfigure(line_buffering=True)

def fetch_noc_for_job(job_id: str, session: requests.Session) -> tuple[str, str]:
    url = f"https://www.jobbank.gc.ca/jobsearch/jobposting/{job_id}"
    for attempt in range(4):
        try:
            res = session.get(url, timeout=12)
            if res.status_code == 200:
                dsel = Selector(res.text)
                noc_no = dsel.css('span.noc-no::text').get() or ''
                raw_code = dsel.css('span.aa_jobbank_job_noccode::text').get() or ''
                
                # Tier 1: span.noc-no
                if noc_no and re.search(r'\d{4,5}', noc_no):
                    return job_id, noc_no.strip()
                    
                # Tier 2: span.aa_jobbank_job_noccode
                if raw_code and re.search(r'\d{4,5}', raw_code):
                    return job_id, f"NOC {raw_code.strip()}"
                
                # Tier 3: fn21 query parameter
                fn21_match = re.search(r'fn21=(\d{4,5})', res.text)
                if fn21_match:
                    return job_id, f"NOC {fn21_match.group(1)}"
                
                # Tier 4: raw text regex
                noc_match = re.search(r'NOC\s*(\d{4,5})', res.text, re.I)
                if noc_match:
                    return job_id, f"NOC {noc_match.group(1)}"
                
                # Inactive / expired notice
                if any(p in res.text.lower() for p in ['posting has expired', 'is no longer available', 'poste a expiré', "poste n'est plus disponible"]):
                    return job_id, "EXPIRED"

                return job_id, ""
            elif res.status_code == 429:
                time.sleep(2 + random.uniform(1.5, 3.0))
                continue
        except Exception:
            time.sleep(1.0)
    return job_id, ""

def update_all_noc_codes(concurrency: int = 15):
    db.init_db()
    
    with db.get_db() as conn:
        cursor = conn.execute("""
            SELECT job_id, employer_name, job_title 
            FROM jobs 
            WHERE (noc_code IS NULL OR noc_code = '') 
              AND (advertised_until >= date('now') OR advertised_until IS NULL OR advertised_until = '')
        """)
        jobs_to_update = cursor.fetchall()
        
    total = len(jobs_to_update)
    print(f"\n=======================================================")
    print(f" NOC Code Multi-Tier Resolver: Processing {total} active jobs missing NOC")
    print(f" Concurrency: {concurrency} workers (TLS Chrome 124)")
    print(f"=======================================================\n")
    
    if total == 0:
        print("All active jobs already have NOC codes.")
        main.export_jobs_to_csv(db.get_all_jobs(active_only=True), "lmia_jobs_master.csv")
        return

    sessions = [requests.Session(impersonate="chrome124") for _ in range(concurrency)]
    start_time = time.time()
    updated_count = 0
    batch_updates = []
    
    def worker_task(index, job_id):
        sess = sessions[index % concurrency]
        return fetch_noc_for_job(job_id, sess)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(worker_task, idx, row[0]): row for idx, row in enumerate(jobs_to_update)}
        
        for future in as_completed(futures):
            job_id, noc_code = future.result()
            if noc_code:
                batch_updates.append((noc_code, job_id))
                updated_count += 1
                print(f"[{updated_count}/{total}] Resolved NOC for Job ID {job_id} -> {noc_code}")
                
            if len(batch_updates) >= 10:
                with db.get_db() as conn:
                    conn.executemany("UPDATE jobs SET noc_code = ? WHERE job_id = ?", batch_updates)
                    conn.commit()
                batch_updates.clear()

    # Flush remaining batch
    if batch_updates:
        with db.get_db() as conn:
            conn.executemany("UPDATE jobs SET noc_code = ? WHERE job_id = ?", batch_updates)
            conn.commit()
            
    total_time = time.time() - start_time
    print(f"\n[+] NOC Multi-Tier Resolver Finished! {updated_count}/{total} updated in {total_time:.1f}s.")
    
    print("\n>>> Exporting updated master dataset to CSV...")
    main.export_jobs_to_csv(db.get_all_jobs(active_only=True), "lmia_jobs_master.csv")

if __name__ == "__main__":
    update_all_noc_codes(concurrency=15)

