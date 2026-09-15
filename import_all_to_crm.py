import sqlite3
import json
import os
import re
from urllib.parse import quote
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(override=True)

import db

CRM_JSON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'twenty CRM', 'src', 'providers', 'data', 'lmia-db.json'))
SQLITE_DB = 'jobs_tracker.db'

def infer_industry(title: str, employer: str, noc: str) -> str:
    text = f'{title} {employer} {noc}'.upper()
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
    match = re.search(r'[\d,.]+', salary_str.replace(',', ''))
    if match:
        try:
            return float(match.group(0))
        except Exception:
            pass
    return 25.0

def export_all_to_crm():
    admin_user = {
        'id': '1',
        'name': 'Admin Recruiter',
        'email': 'admin@canada-jobs.ca',
        'avatarUrl': 'https://api.dicebear.com/7.x/avataaars/svg?seed=Admin',
        'role': 'ADMIN',
        'createdAt': '2026-01-01T00:00:00.000Z',
        'updatedAt': '2026-09-10T00:00:00.000Z'
    }

    deal_stages = [
        {'id': '1', 'title': 'NEW', 'createdAt': '2026-01-01T00:00:00.000Z'},
        {'id': '2', 'title': 'FOLLOW-UP', 'createdAt': '2026-01-01T00:00:00.000Z'},
        {'id': '3', 'title': 'UNDER REVIEW', 'createdAt': '2026-01-01T00:00:00.000Z'},
        {'id': '4', 'title': 'DEMO', 'createdAt': '2026-01-01T00:00:00.000Z'},
        {'id': '5', 'title': 'WON', 'createdAt': '2026-01-01T00:00:00.000Z'},
        {'id': '6', 'title': 'LOST', 'createdAt': '2026-01-01T00:00:00.000Z'}
    ]

    task_stages = [
        {'id': '1', 'title': 'TODO', 'createdAt': '2026-01-01T00:00:00.000Z'},
        {'id': '2', 'title': 'IN_PROGRESS', 'createdAt': '2026-01-01T00:00:00.000Z'},
        {'id': '3', 'title': 'IN_REVIEW', 'createdAt': '2026-01-01T00:00:00.000Z'},
        {'id': '4', 'title': 'DONE', 'createdAt': '2026-01-01T00:00:00.000Z'}
    ]

    print(f'Loading all historical and active jobs from database...')
    raw_jobs = db.get_all_jobs(active_only=False)
    if not raw_jobs:
        with db.get_db() as conn:
            if db.is_postgres():
                with conn.cursor() as cur:
                    cur.execute('SELECT * FROM jobs ORDER BY date_posted DESC, scraped_at DESC')
                    raw_jobs = [dict(r) for r in cur.fetchall()]
            else:
                cursor = conn.execute('SELECT * FROM jobs ORDER BY date_posted DESC, scraped_at DESC')
                raw_jobs = [dict(r) for r in cursor.fetchall()]

    print(f'Total active jobs loaded: {len(raw_jobs)}')

    companies = []
    contacts = []
    deals = []

    company_map = {}
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
            
            # Company Contact
            ct_id = str(contact_id_counter)
            contact_id_counter += 1
            
            contact_obj = {
                'id': ct_id,
                'name': 'Hiring Manager',
                'email': (j.get('employer_email') or '').strip(),
                'phone': (j.get('company_phone') or '').strip(),
                'jobTitle': 'Recruitment & LMIA Coordinator',
                'companyId': cid,
                'company': {
                    'id': cid,
                    'name': emp_name,
                    'avatarUrl': c_avatar,
                    'country': loc_str
                },
                'salesOwnerId': '1',
                'salesOwner': admin_user,
                'stage': 'CUSTOMER',
                'status': 'QUALIFIED',
                'createdAt': '2026-08-01T00:00:00.000Z',
                'updatedAt': '2026-09-10T00:00:00.000Z'
            }
            contacts.append(contact_obj)
            
            company_obj = {
                'id': cid,
                'name': emp_name,
                'avatarUrl': c_avatar,
                'country': loc_str,
                'website': (j.get('company_website') or '').strip(),
                'industry': c_industry,
                'companySize': 'MEDIUM',
                'businessType': 'B2B',
                'salesOwnerId': '1',
                'salesOwner': admin_user,
                'createdAt': '2026-08-01T00:00:00.000Z',
                'updatedAt': '2026-09-10T00:00:00.000Z',
                'contacts': {'nodes': [contact_obj]},
                'deals': {'nodes': []}
            }
            companies.append(company_obj)
            company_map[comp_key] = (company_obj, contact_obj)
        
        comp_obj, cont_obj = company_map[comp_key]
        cid = comp_obj['id']
        ct_id = cont_obj['id']
        
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
        
        posted_date = j.get('date_posted') or '2026-09-01'
        created_iso = f'{posted_date}T12:00:00.000Z' if re.match(r'^\d{4}-\d{2}-\d{2}$', str(posted_date)) else '2026-09-02T12:00:00.000Z'
        
        deal_obj = {
            'id': did,
            'title': j.get('job_title') or 'Job Title',
            'value': wage_val,
            'stageId': '1',
            'stage': deal_stages[0],
            'companyId': cid,
            'company': {
                'id': cid,
                'name': emp_name,
                'avatarUrl': comp_obj['avatarUrl'],
                'country': loc_str
            },
            'dealOwnerId': '1',
            'dealOwner': admin_user,
            'dealContactId': ct_id,
            'dealContact': cont_obj,
            'notes': json.dumps(notes_dict),
            'createdAt': created_iso,
            'updatedAt': '2026-09-10T12:00:00.000Z'
        }
        
        deals.append(deal_obj)
        comp_obj['deals']['nodes'].append(deal_obj)

    prev_count = 0
    if os.path.exists(CRM_JSON_PATH):
        try:
            with open(CRM_JSON_PATH, 'rb') as f:
                old_db = json.loads(f.read().decode('utf-8'))
                prev_count = len(old_db.get('deals', []))
        except Exception:
            pass

    new_jobs_count = max(0, len(deals) - prev_count) if prev_count > 0 else len(deals)

    sync_meta = {
        'lastSyncTimestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'newJobsCount': new_jobs_count,
        'totalJobsCount': len(deals),
        'batchName': 'Daily LMIA Sync'
    }

    master_db = {
        'syncMeta': sync_meta,
        'companies': companies,
        'contacts': contacts,
        'deals': deals,
        'dealStages': deal_stages,
        'tasks': [],
        'taskStages': task_stages,
        'users': [admin_user],
        'events': [],
        'eventCategories': [],
        'quotes': [],
        'audits': []
    }

    if os.path.exists(os.path.dirname(CRM_JSON_PATH)):
        with open(CRM_JSON_PATH, 'wb') as f:
            f.write(json.dumps(master_db, ensure_ascii=False).encode('utf-8'))
        print(f'[+] Successfully wrote full CRM database with {len(deals)} jobs (new: {new_jobs_count}) to: {CRM_JSON_PATH}')
    else:
        print(f'[!] Warning: CRM path not found at {CRM_JSON_PATH}')

if __name__ == '__main__':
    export_all_to_crm()
