import sqlite3
import os
from typing import List, Dict, Optional, Set

DB_FILE = 'jobs_tracker.db'

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                job_title TEXT,
                employer_name TEXT,
                employer_email TEXT,
                noc_code TEXT,
                city TEXT,
                province TEXT,
                full_address TEXT,
                salary TEXT,
                job_type TEXT,
                work_hours_shifts TEXT,
                vacancies TEXT,
                start_date TEXT,
                lmia_status TEXT DEFAULT 'LMIA requested',
                date_posted TEXT,
                advertised_until TEXT,
                how_to_apply_instructions TEXT,
                job_url TEXT,
                company_website TEXT,
                company_phone TEXT,
                enrichment_status TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Ensure noc_code column exists for existing databases
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'noc_code' not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN noc_code TEXT")
        conn.commit()

def get_seen_job_ids() -> Set[str]:
    init_db()
    with get_db() as conn:
        cursor = conn.execute('SELECT job_id FROM jobs')
        return {row['job_id'] for row in cursor.fetchall()}

def save_job(job_data: Dict):
    init_db()
    with get_db() as conn:
        columns = ', '.join(job_data.keys())
        placeholders = ', '.join(['?'] * len(job_data))
        sql = f'''
            INSERT INTO jobs ({columns}) 
            VALUES ({placeholders}) 
            ON CONFLICT(job_id) DO UPDATE SET 
                noc_code = CASE WHEN excluded.noc_code != '' AND excluded.noc_code IS NOT NULL THEN excluded.noc_code ELSE jobs.noc_code END,
                employer_email = CASE WHEN excluded.employer_email != '' AND excluded.employer_email IS NOT NULL THEN excluded.employer_email ELSE jobs.employer_email END, 
                company_website = CASE WHEN excluded.company_website != '' AND excluded.company_website IS NOT NULL THEN excluded.company_website ELSE jobs.company_website END, 
                company_phone = CASE WHEN excluded.company_phone != '' AND excluded.company_phone IS NOT NULL THEN excluded.company_phone ELSE jobs.company_phone END, 
                enrichment_status = CASE WHEN excluded.enrichment_status != '' AND excluded.enrichment_status IS NOT NULL THEN excluded.enrichment_status ELSE jobs.enrichment_status END
        '''
        conn.execute(sql, list(job_data.values()))
        conn.commit()

def update_job_noc(job_id: str, noc_code: str):
    with get_db() as conn:
        conn.execute('UPDATE jobs SET noc_code = ? WHERE job_id = ?', (noc_code, job_id))
        conn.commit()


def update_enrichment(job_id: str, email: str, website: str, phone:
                        str, status: str):
    with get_db() as conn:
        conn.execute('''
            UPDATE jobs 
            SET employer_email = CASE WHEN employer_email = '' OR employer_email IS NULL THEN ? ELSE employer_email END,
                company_website = ?,
                company_phone = ?,
                enrichment_status = ?
            WHERE job_id = ?
''', (email, website, phone, status, job_id))
        conn.commit()

def get_all_jobs(active_only: bool = True) -> List[Dict]:
    init_db()
    with get_db() as conn:
        if active_only:
            cursor = conn.execute("""
                SELECT * FROM jobs 
                WHERE (advertised_until >= date('now') OR advertised_until IS NULL OR advertised_until = '')
                ORDER BY date_posted DESC, scraped_at DESC
            """)
        else:
            cursor = conn.execute('SELECT * FROM jobs ORDER BY date_posted DESC, scraped_at DESC')
        return [dict(row) for row in cursor.fetchall()]

def clean_expired_jobs() -> int:
    """Delete postings from the database whose advertised_until date is in the past."""
    init_db()
    with get_db() as conn:
        cursor = conn.execute("SELECT count(*) FROM jobs WHERE advertised_until < date('now') AND advertised_until != ''")
        expired_count = cursor.fetchone()[0]
        if expired_count > 0:
            conn.execute("DELETE FROM jobs WHERE advertised_until < date('now') AND advertised_until != ''")
            conn.commit()
        return expired_count

