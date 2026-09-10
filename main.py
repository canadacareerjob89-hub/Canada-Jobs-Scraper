import argparse
import csv
import sys
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(override=True)

import db
import core_scraper
import hermes_enrichment

CSV_FIELDNAMES = [
    "job_id",
    "job_title",
    "noc_code",
    "employer_name",
    "employer_email",
    "company_phone",
    "company_website",
    "city",
    "province",
    "full_address",
    "salary",
    "job_type",
    "work_hours_shifts",
    "vacancies",
    "start_date",
    "lmia_status",
    "date_posted",
    "advertised_until",
    "enrichment_status",
    "how_to_apply_instructions",
    "job_url"
]

def export_jobs_to_csv(jobs_list, filename: str):
    """Export list of jobs to CSV with clean UTF-8 BOM encoding and file-lock recovery."""
    if not jobs_list:
        print("No jobs to export.")
        return
        
    target_file = filename
    try:
        with open(target_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(jobs_list)
    except PermissionError:
        # File is open in Excel
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name, ext = os.path.splitext(filename)
        target_file = f"{name}_{timestamp}{ext}"
        print(f"\n[!] Notice: '{filename}' is currently open in Excel or another program.")
        with open(target_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(jobs_list)
            
    print(f"\n[+] Successfully exported {len(jobs_list)} records to: {target_file}")

def main():
    parser = argparse.ArgumentParser(description="Canada Job Bank LMIA Scraper with Hermes Agent Enrichment")
    parser.add_argument("--test", type=int, nargs="?", const=10, help="Run a test scrape on N fresh postings (default: 10)")
    parser.add_argument("--daily", action="store_true", help="Run full daily scrape for new postings with deduplication")
    parser.add_argument("--full", action="store_true", help="Run a complete scrape of all active LMIA postings across all pages")
    parser.add_argument("--enrich-only", action="store_true", help="Run Hermes enrichment on pending jobs in DB")
    parser.add_argument("--db-status", action="store_true", help="Test database health, schema, read/write permissions, and show current statistics")
    parser.add_argument("--clean-expired", action="store_true", help="Purge past expired job postings from the database")
    parser.add_argument("--verify-emails", action="store_true", help="Verify all stored emails against live DNS MX mail server records")
    parser.add_argument("--export", type=str, nargs="?", const="lmia_jobs_master.csv", help="Export active database jobs to CSV")
    
    args = parser.parse_args()
    db.init_db()
    
    if args.db_status:
        import test_db
        test_db.test_database()
        return
        
    elif args.clean_expired:
        purged = db.clean_expired_jobs()
        print(f"\n[+] Purged {purged} expired job postings from the database.")
        import test_db
        test_db.test_database()
        return
        
    elif args.verify_emails:
        import email_verifier
        email_verifier.verify_and_clean_database_emails()
        return
    
    if args.test:
        limit = args.test
        print(f"\n>>> Running TEST SCRAPE on {limit} fresh LMIA jobs...\n")
        scraped_jobs = core_scraper.scrape_lmia_jobs(limit=limit, stop_on_seen=False)
        
        print(f"\n>>> Running Hermes Agent Enrichment on test batch...\n")
        hermes_enrichment.run_hermes_enrichment(scraped_jobs)
        
        seen_test_ids = {j["job_id"] for j in scraped_jobs}
        all_jobs = db.get_all_jobs(active_only=True)
        test_results = [j for j in all_jobs if j["job_id"] in seen_test_ids]
        
        output_file = "lmia_jobs_test.csv"
        export_jobs_to_csv(test_results, output_file)
        
    elif args.full:
        print("\n>>> Running FULL SCRAPE of ALL Active LMIA Postings across Canada...\n")
        new_jobs = core_scraper.scrape_lmia_jobs(limit=None, stop_on_seen=False)
        print(f"\nScraped {len(new_jobs)} new postings from all active search pages.")
        
        all_jobs = db.get_all_jobs(active_only=True)
        master_file = "lmia_jobs_master.csv"
        export_jobs_to_csv(all_jobs, master_file)
        
        print("\n>>> Running Hermes Agent Enrichment on all pending database records...\n")
        hermes_enrichment.run_hermes_enrichment()
        
        export_jobs_to_csv(db.get_all_jobs(active_only=True), master_file)
        
    elif args.daily:
        print("\n>>> Running DAILY LMIA SCRAPER with Automatic Deduplication...\n")
        new_jobs = core_scraper.scrape_lmia_jobs(limit=None, stop_on_seen=True, max_consecutive_seen=10)
        print(f"\nScraped {len(new_jobs)} fresh LMIA jobs today.")
        
        if new_jobs:
            print("\n>>> Running Hermes Agent Enrichment on fresh postings...\n")
            hermes_enrichment.run_hermes_enrichment(new_jobs)
            
        today_str = datetime.now().strftime("%Y-%m-%d")
        daily_file = f"lmia_jobs_{today_str}.csv"
        export_jobs_to_csv(db.get_all_jobs(active_only=True), daily_file)
        
    elif args.enrich_only:
        print("\n>>> Running Hermes Enrichment on pending database records...\n")
        hermes_enrichment.run_hermes_enrichment()
        export_jobs_to_csv(db.get_all_jobs(active_only=True), "lmia_jobs_enriched.csv")
        
    elif args.export:
        print(f"\n>>> Exporting active database records to {args.export}...\n")
        export_jobs_to_csv(db.get_all_jobs(active_only=True), args.export)
        
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
