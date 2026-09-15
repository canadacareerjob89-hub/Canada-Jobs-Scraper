import sqlite3
import os
from typing import List, Dict, Optional, Set
import data_cleaner

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

DB_FILE = 'jobs_tracker.db'

def get_database_url() -> Optional[str]:
    url = os.getenv('DATABASE_URL') or os.getenv('NEON_DATABASE_URL') or os.getenv('VITE_NEON_DATABASE_URL')
    if url and (url.startswith('postgres://') or url.startswith('postgresql://')):
        # Fix postgres:// prefix for SQLAlchemy/psycopg2 compatibility if needed
        if url.startswith('postgres://'):
            url = 'postgresql://' + url[len('postgres://'):]
        return url
    return None

def is_postgres() -> bool:
    return bool(get_database_url() and PSYCOPG2_AVAILABLE)

def get_db():
    pg_url = get_database_url()
    if pg_url and PSYCOPG2_AVAILABLE:
        # Optimized for NeonDB Serverless: Fast connection timeout and TCP keepalives
        conn = psycopg2.connect(
            pg_url,
            cursor_factory=RealDictCursor,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
        )
        return conn
    else:
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

def get_table_name() -> str:
    return "raw_scraped_jobs" if is_postgres() else "jobs"

def init_db():
    pg_url = get_database_url()
    if pg_url and PSYCOPG2_AVAILABLE:
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS raw_scraped_jobs (
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
                    email_confidence TEXT DEFAULT '',
                    email_verification_status TEXT DEFAULT '',
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                DO $$ 
                BEGIN 
                    BEGIN
                        ALTER TABLE raw_scraped_jobs ADD COLUMN noc_code TEXT;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE raw_scraped_jobs ADD COLUMN email_confidence TEXT DEFAULT '';
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE raw_scraped_jobs ADD COLUMN email_verification_status TEXT DEFAULT '';
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                END $$;
            """)
            # NeonDB Indexes for high-speed filtering and 0 full-table-scan reads
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_raw_jobs_date_posted ON raw_scraped_jobs(date_posted DESC);
                CREATE INDEX IF NOT EXISTS idx_raw_jobs_advertised_until ON raw_scraped_jobs(advertised_until);
                CREATE INDEX IF NOT EXISTS idx_raw_jobs_noc_code ON raw_scraped_jobs(noc_code);
                CREATE INDEX IF NOT EXISTS idx_raw_jobs_employer_name ON raw_scraped_jobs(employer_name);
                CREATE INDEX IF NOT EXISTS idx_raw_jobs_city_province ON raw_scraped_jobs(city, province);
            """)
        conn.commit()
        conn.close()
    else:
        with sqlite3.connect(DB_FILE) as conn:
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
                    email_confidence TEXT DEFAULT '',
                    email_verification_status TEXT DEFAULT '',
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor = conn.execute("PRAGMA table_info(jobs)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'noc_code' not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN noc_code TEXT")
            if 'email_confidence' not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN email_confidence TEXT DEFAULT ''")
            if 'email_verification_status' not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN email_verification_status TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_date_posted ON jobs(date_posted DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_advertised_until ON jobs(advertised_until)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_noc_code ON jobs(noc_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_employer_name ON jobs(employer_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_city_province ON jobs(city, province)")
            conn.commit()

def get_seen_job_ids() -> Set[str]:
    init_db()
    if is_postgres():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute('SELECT job_id FROM raw_scraped_jobs')
            res = {row['job_id'] for row in cur.fetchall()}
        conn.close()
        return res
    else:
        with get_db() as conn:
            cursor = conn.execute('SELECT job_id FROM jobs')
            return {row['job_id'] for row in cursor.fetchall()}

def save_job(job_data: Dict):
    save_jobs_batch([job_data])

def save_jobs_batch(jobs_list: List[Dict], batch_size: int = 250):
    """High-performance batch upsert optimized for NeonDB & SQLite (batches round trips)."""
    if not jobs_list:
        return
    cleaned_jobs = [data_cleaner.clean_job_dict(j) for j in jobs_list]
    init_db()
    if is_postgres():
        conn = get_db()
        for i in range(0, len(cleaned_jobs), batch_size):
            batch = cleaned_jobs[i:i + batch_size]
            if not batch:
                continue
            columns = list(batch[0].keys())
            cols_str = ', '.join(columns)
            placeholders = ', '.join(['%s'] * len(columns))
            sql = f'''
                INSERT INTO raw_scraped_jobs ({cols_str}) 
                VALUES ({placeholders}) 
                ON CONFLICT(job_id) DO UPDATE SET 
                    noc_code = CASE WHEN EXCLUDED.noc_code != '' AND EXCLUDED.noc_code IS NOT NULL THEN EXCLUDED.noc_code ELSE raw_scraped_jobs.noc_code END,
                    employer_email = CASE WHEN EXCLUDED.employer_email != '' AND EXCLUDED.employer_email IS NOT NULL THEN EXCLUDED.employer_email ELSE raw_scraped_jobs.employer_email END, 
                    company_website = CASE WHEN EXCLUDED.company_website != '' AND EXCLUDED.company_website IS NOT NULL THEN EXCLUDED.company_website ELSE raw_scraped_jobs.company_website END, 
                    company_phone = CASE WHEN EXCLUDED.company_phone != '' AND EXCLUDED.company_phone IS NOT NULL THEN EXCLUDED.company_phone ELSE raw_scraped_jobs.company_phone END, 
                    enrichment_status = CASE WHEN EXCLUDED.enrichment_status != '' AND EXCLUDED.enrichment_status IS NOT NULL THEN EXCLUDED.enrichment_status ELSE raw_scraped_jobs.enrichment_status END,
                    email_confidence = CASE WHEN EXCLUDED.email_confidence != '' AND EXCLUDED.email_confidence IS NOT NULL THEN EXCLUDED.email_confidence ELSE raw_scraped_jobs.email_confidence END,
                    email_verification_status = CASE WHEN EXCLUDED.email_verification_status != '' AND EXCLUDED.email_verification_status IS NOT NULL THEN EXCLUDED.email_verification_status ELSE raw_scraped_jobs.email_verification_status END
            '''
            values = [list(j.values()) for j in batch]
            with conn.cursor() as cur:
                cur.executemany(sql, values)
        conn.commit()
        conn.close()
    else:
        with get_db() as conn:
            for i in range(0, len(cleaned_jobs), batch_size):
                batch = cleaned_jobs[i:i + batch_size]
                if not batch:
                    continue
                columns = list(batch[0].keys())
                cols_str = ', '.join(columns)
                placeholders = ', '.join(['?'] * len(columns))
                sql = f'''
                    INSERT INTO jobs ({cols_str}) 
                    VALUES ({placeholders}) 
                    ON CONFLICT(job_id) DO UPDATE SET 
                        noc_code = CASE WHEN excluded.noc_code != '' AND excluded.noc_code IS NOT NULL THEN excluded.noc_code ELSE jobs.noc_code END,
                        employer_email = CASE WHEN excluded.employer_email != '' AND excluded.employer_email IS NOT NULL THEN excluded.employer_email ELSE jobs.employer_email END, 
                        company_website = CASE WHEN excluded.company_website != '' AND excluded.company_website IS NOT NULL THEN excluded.company_website ELSE jobs.company_website END, 
                        company_phone = CASE WHEN excluded.company_phone != '' AND excluded.company_phone IS NOT NULL THEN excluded.company_phone ELSE jobs.company_phone END, 
                        enrichment_status = CASE WHEN excluded.enrichment_status != '' AND excluded.enrichment_status IS NOT NULL THEN excluded.enrichment_status ELSE jobs.enrichment_status END,
                        email_confidence = CASE WHEN excluded.email_confidence != '' AND excluded.email_confidence IS NOT NULL THEN excluded.email_confidence ELSE jobs.email_confidence END,
                        email_verification_status = CASE WHEN excluded.email_verification_status != '' AND excluded.email_verification_status IS NOT NULL THEN excluded.email_verification_status ELSE jobs.email_verification_status END
                '''
                values = [list(j.values()) for j in batch]
                conn.executemany(sql, values)
            conn.commit()

def update_job_noc(job_id: str, noc_code: str):
    if is_postgres():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute('UPDATE raw_scraped_jobs SET noc_code = %s WHERE job_id = %s', (noc_code, job_id))
        conn.commit()
        conn.close()
    else:
        with get_db() as conn:
            conn.execute('UPDATE jobs SET noc_code = ? WHERE job_id = ?', (noc_code, job_id))
            conn.commit()

def update_enrichment(
    job_id: str,
    email: str,
    website: str,
    phone: str,
    status: str,
    email_confidence: str = "",
    email_verification_status: str = ""
):
    if is_postgres():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute('''
                UPDATE raw_scraped_jobs 
                SET employer_email = %s,
                    company_website = %s,
                    company_phone = %s,
                    enrichment_status = %s,
                    email_confidence = CASE WHEN %s != '' THEN %s ELSE email_confidence END,
                    email_verification_status = CASE WHEN %s != '' THEN %s ELSE email_verification_status END
                WHERE job_id = %s
            ''', (email, website, phone, status, email_confidence, email_confidence, email_verification_status, email_verification_status, job_id))
        conn.commit()
        conn.close()
    else:
        with get_db() as conn:
            conn.execute('''
                UPDATE jobs 
                SET employer_email = ?,
                    company_website = ?,
                    company_phone = ?,
                    enrichment_status = ?,
                    email_confidence = CASE WHEN ? != '' THEN ? ELSE email_confidence END,
                    email_verification_status = CASE WHEN ? != '' THEN ? ELSE email_verification_status END
                WHERE job_id = ?
            ''', (email, website, phone, status, email_confidence, email_confidence, email_verification_status, email_verification_status, job_id))
            conn.commit()

def get_all_jobs(active_only: bool = True) -> List[Dict]:
    init_db()
    if is_postgres():
        conn = get_db()
        with conn.cursor() as cur:
            if active_only:
                cur.execute("""
                    SELECT * FROM raw_scraped_jobs 
                    WHERE (advertised_until >= CURRENT_DATE::text OR advertised_until IS NULL OR advertised_until = '')
                    ORDER BY date_posted DESC, scraped_at DESC
                """)
            else:
                cur.execute('SELECT * FROM raw_scraped_jobs ORDER BY date_posted DESC, scraped_at DESC')
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    else:
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
    if is_postgres():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) as count FROM raw_scraped_jobs WHERE advertised_until < CURRENT_DATE::text AND advertised_until != ''")
            res = cur.fetchone()
            expired_count = res['count'] if isinstance(res, dict) else res[0]
            if expired_count > 0:
                cur.execute("DELETE FROM raw_scraped_jobs WHERE advertised_until < CURRENT_DATE::text AND advertised_until != ''")
        conn.commit()
        conn.close()
        return expired_count
    else:
        with get_db() as conn:
            cursor = conn.execute("SELECT count(*) FROM jobs WHERE advertised_until < date('now') AND advertised_until != ''")
            expired_count = cursor.fetchone()[0]
            if expired_count > 0:
                conn.execute("DELETE FROM jobs WHERE advertised_until < date('now') AND advertised_until != ''")
                conn.commit()
            return expired_count
