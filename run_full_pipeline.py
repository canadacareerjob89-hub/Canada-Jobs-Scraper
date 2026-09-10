import time
import sys
from datetime import datetime

import db
import update_noc_codes
import hermes_enrichment
import email_verifier
import main

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

def run_pipeline():
    start_time = time.time()
    print("================================================================================")
    print("      CANADA JOB BANK LMIA SCRAPER - FULL DATASET PROCESSING PIPELINE          ")
    print(f"      Started At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}               ")
    print("================================================================================\n")

    # 1. Initialize Database
    print("[1/5] Initializing Database...")
    db.init_db()

    # 2. NOC Code Multi-Tier Resolution
    print("\n[2/5] Running NOC Code Multi-Tier Resolver...")
    update_noc_codes.update_all_noc_codes(concurrency=15)

    # 3. Dual-Source Hermes + Google Maps Places Enrichment
    print("\n[3/5] Running Dual-Source Enrichment (Corporate Web + Google Maps Places)...")
    all_active = db.get_all_jobs(active_only=True)
    jobs_needing_enrichment = [
        j for j in all_active 
        if not j.get("employer_email") or not j.get("company_phone") or not j.get("company_website")
    ]
    print(f"  -> Found {len(jobs_needing_enrichment)} active records eligible for enrichment.")
    if jobs_needing_enrichment:
        hermes_enrichment.run_hermes_enrichment(jobs_needing_enrichment, concurrency=10)

    # 4. Confidence Scoring & Live DNS MX Resolution
    print("\n[4/5] Running Live DNS MX Verification & Confidence Scoring (>=80% Threshold)...")
    email_verifier.verify_and_clean_database_emails(min_confidence=80)

    # 5. Master CSV Export
    print("\n[5/5] Exporting Final Verified Master Dataset...")
    final_jobs = db.get_all_jobs(active_only=True)
    main.export_jobs_to_csv(final_jobs, "lmia_jobs_master.csv")

    elapsed = time.time() - start_time
    total = len(final_jobs)
    emails = sum(1 for j in final_jobs if j.get("employer_email"))
    nocs = sum(1 for j in final_jobs if j.get("noc_code"))
    websites = sum(1 for j in final_jobs if j.get("company_website"))
    phones = sum(1 for j in final_jobs if j.get("company_phone"))

    print("\n================================================================================")
    print("                     PIPELINE EXECUTION COMPLETE                                ")
    print("================================================================================")
    print(f"  - Total Active Jobs:                {total}")
    print(f"  - 100% Official NOC Codes:          {nocs} ({(nocs/total*100 if total else 0):.1f}%)")
    print(f"  - Deliverable Verified Emails:      {emails} ({(emails/total*100 if total else 0):.1f}%)")
    print(f"  - Verified Company Websites:        {websites}")
    print(f"  - Verified Local Phone Numbers:     {phones}")
    print(f"  - Total Execution Time:             {elapsed:.1f} seconds")
    print(f"  - Master File:                      lmia_jobs_master.csv")
    print("================================================================================\n")

if __name__ == "__main__":
    run_pipeline()
