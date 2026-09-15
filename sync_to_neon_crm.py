#!/usr/bin/env python3
"""
Automated Neon PostgreSQL CRM Ingestion Pipeline
Syncs all scraped LMIA jobs, employers, and recruitment contacts directly into Neon Cloud PostgreSQL.
"""

import os
import sys
import json
import re
import urllib.request
import urllib.error
from urllib.parse import quote
from datetime import datetime
from dotenv import load_dotenv

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

load_dotenv(override=True)

import db

CRM_JSON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'twenty CRM', 'src', 'providers', 'data', 'lmia-db.json'))

def execute_neon_sql(conn_string: str, sql: str, params: list = None):
    """Execute SQL query via Neon Serverless HTTP API for fast serverless execution."""
    if not conn_string or not conn_string.startswith(('postgres://', 'postgresql://')):
        raise ValueError("Invalid PostgreSQL connection string.")

    match = re.search(r'@([^:/]+)', conn_string)
    if not match:
        raise ValueError("Could not parse host from connection string")
    
    host = match.group(1)
    endpoint = f"https://{host}/sql"
    
    payload = json.dumps({"query": sql, "params": params or []}).encode('utf-8')
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Neon-Connection-String": conn_string
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f"Neon DB Error ({e.code}): {err_msg}")

def infer_industry(title: str, employer: str, noc: str) -> str:
    text = f"{title} {employer} {noc}".upper()
    if any(k in text for k in ['TRUCK', 'DRIVER', 'TRANSPORT', 'LOGISTIC', 'DISPATCH', 'DELIVERY', 'HAUL']):
        return 'TRANSPORTATION'
    if any(k in text for k in ['COOK', 'CHEF', 'FOOD', 'RESTAURANT', 'HOTEL', 'CLEAN', 'MOTEL', 'BAKER', 'PIZZA', 'BAR', 'KITCHEN', 'DISHWASHER', 'HOUSEKEEP', 'SERVER', 'HOSPITALITY', 'PUB', 'CAFE']):
        return 'HOSPITALITY'
    if any(k in text for k in ['CONSTRUCT', 'CARPENTER', 'WELDER', 'PLUMB', 'ELECTRIC', 'ROOF', 'MASON', 'PAINTER', 'DRYWALL', 'CEMENT', 'CONCRETE', 'HVAC', 'LABOURER', 'BUILD']):
        return 'CONSTRUCTION'
    if any(k in text for k in ['FARM', 'AGRICULTUR', 'GREENHOUSE', 'HARVEST', 'DAIRY', 'LIVESTOCK', 'CROP', 'POULTRY', 'NURSERY', 'FRUIT', 'MUSHROOM']):
        return 'AGRICULTURE'
    if any(k in text for k in ['SOFTWAR', 'DEVELOPER', 'PROGRAMMER', 'ENGINEER', 'DATA', 'IT ', 'TECH', 'WEB', 'NETWORK', 'CLOUD', 'AI ']):
        return 'TECHNOLOGY'
    if any(k in text for k in ['NURSE', 'HEALTH', 'DENTAL', 'CAREGIVER', 'MEDICAL', 'PHARMACY', 'CLINIC', 'CARE', 'PHYSICIAN', 'THERAPIST']):
        return 'HEALTHCARE'
    if any(k in text for k in ['RETAIL', 'SALES', 'STORE', 'CASHIER', 'CLERK', 'SUPERMARKET', 'GROCERY', 'MERCHANDIS', 'MARKET']):
        return 'RETAIL'
    if any(k in text for k in ['ACCOUNT', 'BOOKKEEP', 'FINANC', 'BANK', 'AUDIT', 'TAX', 'INSURAN']):
        return 'FINANCIAL_SERVICES'
    if any(k in text for k in ['MANUFACTUR', 'FABRICAT', 'ASSEMBL', 'PLANT', 'MILL', 'OPERATOR', 'MACHIN', 'PRODUCTION']):
        return 'INDUSTRIAL_MANUFACTURING'
    if any(k in text for k in ['TEACH', 'TUTOR', 'INSTRUCT', 'SCHOOL', 'EDUCAT']):
        return 'EDUCATION'
    return 'OTHER'

def parse_wage(salary_str: str) -> float:
    if not salary_str:
        return 25.0
    match = re.search(r'[\d,.]+', str(salary_str).replace(',', ''))
    if match:
        try:
            val = float(match.group(0))
            return val
        except Exception:
            pass
    return 25.0

def sync_to_neon():
    conn_string = os.getenv("DATABASE_URL") or os.getenv("VITE_NEON_DATABASE_URL_CANADA_CAREER")
    if not conn_string:
        print("[!] Error: No DATABASE_URL environment variable found.")
        return False

    print("\n🍁 ========================================================")
    print("🍁 AUTO-SYNCING SCRAPED DATA DIRECTLY TO NEON CLOUD CRM DB")
    print("🍁 ========================================================\n")

    # 1. Load active jobs from SQLite
    print("[1/4] Loading active scraped jobs from database...")
    raw_jobs = db.get_all_jobs(active_only=False)
    if not raw_jobs:
        with db.get_db() as conn:
            cursor = conn.execute("SELECT * FROM jobs ORDER BY date_posted DESC, scraped_at DESC")
            raw_jobs = [dict(r) for r in cursor.fetchall()]
    
    print(f"  -> Total records to process: {len(raw_jobs):,}")

    if not raw_jobs:
        print("[!] No job records found to sync.")
        return True

    # 2. Build Relational Models (Companies, Contacts, Jobs)
    print("\n[2/4] Normalizing into CRM entities (Companies, Contacts, Jobs)...")
    
    deal_stages = [
        ("1", "NEW"),
        ("2", "FOLLOW-UP"),
        ("3", "UNDER REVIEW"),
        ("4", "DEMO"),
        ("5", "WON"),
        ("6", "LOST")
    ]
    for st_id, st_title in deal_stages:
        execute_neon_sql(
            conn_string,
            "INSERT INTO deal_stages (id, title, updated_at) VALUES ($1, $2, NOW()) ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title;",
            [st_id, st_title]
        )

    company_map = {}
    companies_batch = []
    contacts_batch = []
    jobs_batch = []

    comp_id_counter = 1
    contact_id_counter = 1
    deal_id_counter = 1

    for j in raw_jobs:
        emp_name = (j.get('employer_name') or 'Canadian Employer').strip()
        city = (j.get('city') or '').strip()
        prov = (j.get('province') or '').strip()
        loc_str = f'{city}, {prov}' if city and prov else (prov or city or 'Canada')
        
        comp_key = emp_name.lower()
        
        if comp_key not in company_map:
            cid = str(comp_id_counter)
            comp_id_counter += 1
            
            c_industry = infer_industry(j.get('job_title', ''), emp_name, j.get('noc_code', ''))
            c_avatar = f'https://api.dicebear.com/7.x/identicon/svg?seed={quote(emp_name)}'
            
            ct_id = str(contact_id_counter)
            contact_id_counter += 1
            
            # Contact
            contacts_batch.append({
                'id': ct_id,
                'name': 'Hiring Manager',
                'email': (j.get('employer_email') or '').strip(),
                'phone': (j.get('company_phone') or '').strip(),
                'job_title': 'Recruitment & LMIA Coordinator',
                'status': 'QUALIFIED',
                'stage': 'CUSTOMER',
                'timezone': 'EST',
                'company_id': cid,
                'sales_owner_id': '1'
            })
            
            # Company
            companies_batch.append({
                'id': cid,
                'name': emp_name,
                'avatar_url': c_avatar,
                'total_revenue': 0,
                'industry': c_industry,
                'company_size': 'MEDIUM',
                'business_type': 'B2B',
                'country': loc_str,
                'website': (j.get('company_website') or '').strip(),
                'email': (j.get('employer_email') or '').strip(),
                'phone': (j.get('company_phone') or '').strip(),
                'sales_owner_id': '1'
            })
            
            company_map[comp_key] = (cid, ct_id)
        
        cid, ct_id = company_map[comp_key]
        did = str(deal_id_counter)
        deal_id_counter += 1
        
        wage_val = parse_wage(j.get('salary', ''))
        
        notes_dict = {
            'job_id': str(j.get('job_id') or ''),
            'noc_code': str(j.get('noc_code') or ''),
            'salary_raw': str(j.get('salary') or 'Competitive'),
            'city': city,
            'province': prov,
            'location': loc_str,
            'vacancies': re.search(r'\d+', str(j.get('vacancies') or '1')).group(0) if re.search(r'\d+', str(j.get('vacancies') or '1')) else '1',
            'job_type': str(j.get('job_type') or 'Permanent employment Full time'),
            'lmia_status': str(j.get('lmia_status') or 'LMIA requested'),
            'employer_id': cid,
            'employer_name': emp_name,
            'employer_email': str(j.get('employer_email') or ''),
            'company_phone': str(j.get('company_phone') or ''),
            'company_website': str(j.get('company_website') or ''),
            'job_url': str(j.get('job_url') or ''),
            'work_hours': str(j.get('work_hours_shifts') or ''),
            'start_date': str(j.get('start_date') or 'Starts as soon as possible'),
            'date_posted': str(j.get('date_posted') or ''),
            'advertised_until': str(j.get('advertised_until') or '')
        }
        
        jobs_batch.append({
            'id': did,
            'title': j.get('job_title') or 'Job Title',
            'value': wage_val,
            'stage_id': '1',
            'company_id': cid,
            'deal_owner_id': '1',
            'deal_contact_id': ct_id,
            'notes': json.dumps(notes_dict)
        })

    print(f"  -> Generated {len(companies_batch):,} Companies")
    print(f"  -> Generated {len(contacts_batch):,} Recruitment Contacts")
    print(f"  -> Generated {len(jobs_batch):,} LMIA Job Postings")

    # 3. Batch Ingest into Neon Cloud DB
    print("\n[3/4] Uploading batches to Neon Cloud Database...")
    BATCH_SIZE = 200

    # 3a. Companies
    for i in range(0, len(companies_batch), BATCH_SIZE):
        chunk = companies_batch[i:i + BATCH_SIZE]
        placeholders = []
        params = []
        p_idx = 1
        for c in chunk:
            placeholders.append(f"(${p_idx}, ${p_idx+1}, ${p_idx+2}, ${p_idx+3}, ${p_idx+4}, ${p_idx+5}, ${p_idx+6}, ${p_idx+7}, ${p_idx+8}, ${p_idx+9}, ${p_idx+10}, ${p_idx+11})")
            params.extend([
                c['id'], c['name'], c['avatar_url'], c['total_revenue'], c['industry'],
                c['company_size'], c['business_type'], c['country'], c['website'],
                c['email'], c['phone'], c['sales_owner_id']
            ])
            p_idx += 12
        
        query = f"""
            INSERT INTO companies (id, name, avatar_url, total_revenue, industry, company_size, business_type, country, website, email, phone, sales_owner_id)
            VALUES {', '.join(placeholders)}
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                country = EXCLUDED.country,
                website = EXCLUDED.website,
                email = EXCLUDED.email,
                phone = EXCLUDED.phone,
                updated_at = NOW();
        """
        execute_neon_sql(conn_string, query, params)
        sys.stdout.write(f"   ➜ Synced {min(i + BATCH_SIZE, len(companies_batch)):,} / {len(companies_batch):,} Companies...\r")
    print(f"\n   ✅ All {len(companies_batch):,} Companies synced to Neon!")

    # 3b. Contacts
    for i in range(0, len(contacts_batch), BATCH_SIZE):
        chunk = contacts_batch[i:i + BATCH_SIZE]
        placeholders = []
        params = []
        p_idx = 1
        for ct in chunk:
            placeholders.append(f"(${p_idx}, ${p_idx+1}, ${p_idx+2}, ${p_idx+3}, ${p_idx+4}, ${p_idx+5}, ${p_idx+6}, ${p_idx+7}, ${p_idx+8}, ${p_idx+9})")
            params.extend([
                ct['id'], ct['name'], ct['email'], ct['phone'], ct['job_title'],
                ct['status'], ct['stage'], ct['timezone'], ct['company_id'], ct['sales_owner_id']
            ])
            p_idx += 10
        
        query = f"""
            INSERT INTO contacts (id, name, email, phone, job_title, status, stage, timezone, company_id, sales_owner_id)
            VALUES {', '.join(placeholders)}
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                email = EXCLUDED.email,
                phone = EXCLUDED.phone,
                updated_at = NOW();
        """
        execute_neon_sql(conn_string, query, params)
        sys.stdout.write(f"   ➜ Synced {min(i + BATCH_SIZE, len(contacts_batch)):,} / {len(contacts_batch):,} Contacts...\r")
    print(f"\n   ✅ All {len(contacts_batch):,} Contacts synced to Neon!")

    # 3c. Jobs
    for i in range(0, len(jobs_batch), BATCH_SIZE):
        chunk = jobs_batch[i:i + BATCH_SIZE]
        placeholders = []
        params = []
        p_idx = 1
        for jb in chunk:
            placeholders.append(f"(${p_idx}, ${p_idx+1}, ${p_idx+2}, ${p_idx+3}, ${p_idx+4}, ${p_idx+5}, ${p_idx+6}, ${p_idx+7})")
            params.extend([
                jb['id'], jb['title'], jb['value'], jb['stage_id'], jb['company_id'],
                jb['deal_owner_id'], jb['deal_contact_id'], jb['notes']
            ])
            p_idx += 8
        
        query = f"""
            INSERT INTO jobs (id, title, value, stage_id, company_id, deal_owner_id, deal_contact_id, notes)
            VALUES {', '.join(placeholders)}
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                value = EXCLUDED.value,
                notes = EXCLUDED.notes,
                updated_at = NOW();
        """
        execute_neon_sql(conn_string, query, params)
        sys.stdout.write(f"   ➜ Synced {min(i + BATCH_SIZE, len(jobs_batch)):,} / {len(jobs_batch):,} Jobs...\r")
    print(f"\n   ✅ All {len(jobs_batch):,} LMIA Jobs synced to Neon!")

    # 4. Verify Live Counts
    print("\n[4/4] Verifying Cloud Database Row Counts...")
    count_res = execute_neon_sql(conn_string, "SELECT (SELECT COUNT(*) FROM jobs) as jc, (SELECT COUNT(*) FROM companies) as cc, (SELECT COUNT(*) FROM contacts) as ctc, (SELECT MAX(updated_at) FROM jobs) as j_up;")
    if count_res and count_res.get('rows'):
        r = count_res['rows'][0]
        print(f"  -> Total LMIA Jobs in Cloud:     {int(r.get('jc', 0)):,} rows")
        print(f"  -> Total Employers in Cloud:     {int(r.get('cc', 0)):,} rows")
        print(f"  -> Total Contacts in Cloud:      {int(r.get('ctc', 0)):,} rows")
        print(f"  -> Latest Cloud Update Time:     {r.get('j_up')}")

    print("\n🎉 SUCCESS: Scraped data is 100% synchronized with Live Neon Cloud CRM!")
    return True

if __name__ == "__main__":
    sync_to_neon()
