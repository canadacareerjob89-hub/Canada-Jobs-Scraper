import sqlite3
import os
import sys
from datetime import datetime

DB_FILE = "jobs_tracker.db"

def test_database():
    print("=" * 60)
    print("           DATABASE HEALTH & STATUS CHECK")
    print("=" * 60)
    
    # 1. File existence & size
    if not os.path.exists(DB_FILE):
        print(f"\n[FAIL] Database file '{DB_FILE}' not found on disk!")
        return
        
    file_size_kb = os.path.getsize(DB_FILE) / 1024
    print(f"\n[+] Database File:  {os.path.abspath(DB_FILE)}")
    print(f"[+] File Size:      {file_size_kb:.2f} KB")
    print(f"[+] Status:         ACTIVE on disk")

    # 2. Connection & Schema check
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Check table
        tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").fetchall()
        if not tables:
            print("\n[FAIL] Table 'jobs' does not exist in the database.")
            return
            
        columns = [col[1] for col in c.execute("PRAGMA table_info(jobs)").fetchall()]
        print(f"[+] Schema:         'jobs' table verified ({len(columns)} columns)")
        
    except Exception as e:
        print(f"\n[FAIL] Could not connect to SQLite database: {e}")
        return

    # 3. Read/Write test (test insertion and immediate rollback/delete)
    try:
        test_id = "__TEST_HEALTH_CHECK__"
        c.execute("INSERT OR REPLACE INTO jobs (job_id, job_title, employer_name, enrichment_status) VALUES (?, ?, ?, ?)",
                  (test_id, "Health Check Title", "Health Check Employer", "Test"))
        conn.commit()
        # Verify read
        read_back = c.execute("SELECT job_id FROM jobs WHERE job_id=?", (test_id,)).fetchone()
        # Clean up
        c.execute("DELETE FROM jobs WHERE job_id=?", (test_id,))
        conn.commit()
        
        if read_back and read_back[0] == test_id:
            print("[+] Write Test:     PASSED (Read/Write/Commit permissions OK)")
        else:
            print("[WARN] Write Test:  Record was written but could not be verified.")
    except Exception as e:
        print(f"[FAIL] Read/Write test failed: {e}")

    # 4. Total record count & statistics
    try:
        total_jobs = c.execute("SELECT count(*) FROM jobs").fetchone()[0]
        today_iso = datetime.now().strftime("%Y-%m-%d")
        all_until = c.execute("SELECT advertised_until FROM jobs").fetchall()
        expired_count = sum(1 for (u,) in all_until if u and u < today_iso)
        active_count = total_jobs - expired_count
        
        with_emails = c.execute("SELECT count(*) FROM jobs WHERE employer_email != '' AND employer_email IS NOT NULL").fetchone()[0]
        with_phones = c.execute("SELECT count(*) FROM jobs WHERE company_phone != '' AND company_phone IS NOT NULL").fetchone()[0]
        with_websites = c.execute("SELECT count(*) FROM jobs WHERE company_website != '' AND company_website IS NOT NULL").fetchone()[0]
        
        print("\n" + "-" * 60)
        print(f" TOTAL JOBS IN DB:       {total_jobs}")
        print(f"  - Active Postings:      {active_count} ({(active_count/total_jobs*100 if total_jobs else 0):.1f}%)")
        print(f"  - Expired Postings:     {expired_count}")
        print(f"  - Jobs with Emails:     {with_emails} ({(with_emails/total_jobs*100 if total_jobs else 0):.1f}%)")
        print(f"  - Jobs with Phones:     {with_phones}")
        print(f"  - Jobs with Websites:   {with_websites}")
        print("-" * 60)
        
        # Breakdown by status
        status_counts = c.execute("SELECT enrichment_status, count(*) FROM jobs GROUP BY enrichment_status").fetchall()
        print("\nBreakdown by Enrichment Status:")
        for status, count in status_counts:
            print(f"  * {status or 'Unspecified'}: {count}")
            
    except Exception as e:
        print(f"[FAIL] Failed to retrieve statistics: {e}")

    # 5. Show 5 most recently scraped jobs
    try:
        recent_jobs = c.execute("""
            SELECT job_id, job_title, employer_name, city, province, employer_email, enrichment_status, scraped_at 
            FROM jobs 
            ORDER BY scraped_at DESC, date_posted DESC 
            LIMIT 5
        """).fetchall()
        
        if recent_jobs:
            print("\n" + "=" * 60)
            print(" 5 MOST RECENTLY STORED POSTINGS:")
            print("=" * 60)
            for j in recent_jobs:
                email_display = j['employer_email'] if j['employer_email'] else "[No Direct Email]"
                print(f"• ID: {j['job_id']} | {j['job_title']} at {j['employer_name']} ({j['city']}, {j['province']})")
                print(f"  Contact: {email_display} | Status: {j['enrichment_status']} | Time: {j['scraped_at']}\n")
        else:
            print("\nDatabase is currently empty (0 jobs recorded).")
            
    except Exception as e:
        print(f"[FAIL] Could not fetch recent jobs: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    test_database()
