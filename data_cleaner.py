import re
from datetime import datetime
from typing import Dict, Any

MONTH_MAP = {
    'january': '01', 'jan': '01', 'janv': '01', 'janvier': '01',
    'february': '02', 'feb': '02', 'févr': '02', 'février': '02',
    'march': '03', 'mar': '03', 'mars': '03',
    'april': '04', 'apr': '04', 'avr': '04', 'avril': '04',
    'may': '05', 'mai': '05',
    'june': '06', 'jun': '06', 'juin': '06',
    'july': '07', 'jul': '07', 'juil': '07', 'juillet': '07',
    'august': '08', 'aug': '08', 'août': '08', 'aout': '08',
    'september': '09', 'sep': '09', 'sept': '09',
    'october': '10', 'oct': '10', 'octobre': '10',
    'november': '11', 'nov': '11', 'novembre': '11',
    'december': '12', 'dec': '12', 'déc': '12', 'décembre': '12'
}

def normalize_vacancies(val: Any) -> str:
    if not val:
        return '1'
    s = str(val).strip()
    match = re.search(r'\d+', s)
    return match.group(0) if match else '1'

def normalize_date(val: Any) -> str:
    if not val:
        return ''
    s = str(val).strip()
    
    # Already ISO YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
        
    # Format: Month DD, YYYY (e.g. September 02, 2026 or September 2, 2026)
    m = re.match(r'^([a-zA-ZÀ-ÿ]+)\s+(\d{1,2}),?\s+(\d{4})$', s)
    if m:
        month_str = m.group(1).lower()
        day_str = m.group(2).zfill(2)
        year_str = m.group(3)
        month_num = MONTH_MAP.get(month_str)
        if month_num:
            return f'{year_str}-{month_num}-{day_str}'
            
    # Format: DD Month YYYY (e.g. 02 September 2026 or 2 Sep 2026)
    m2 = re.match(r'^(\d{1,2})\s+([a-zA-ZÀ-ÿ]+),?\s+(\d{4})$', s)
    if m2:
        day_str = m2.group(1).zfill(2)
        month_str = m2.group(2).lower()
        year_str = m2.group(3)
        month_num = MONTH_MAP.get(month_str)
        if month_num:
            return f'{year_str}-{month_num}-{day_str}'
            
    # Format: YYYY/MM/DD or MM/DD/YYYY
    m3 = re.match(r'^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$', s)
    if m3:
        return f'{m3.group(1)}-{m3.group(2).zfill(2)}-{m3.group(3).zfill(2)}'
        
    return s

def normalize_noc(val: Any) -> str:
    if not val:
        return ''
    s = str(val).strip()
    digits = re.findall(r'\d{4,5}', s)
    if digits:
        return f'NOC {digits[0]}'
    return s

def normalize_salary(val: Any) -> str:
    if not val:
        return 'Competitive'
    s = str(val).strip()
    s = re.sub(r'(?i)salary\s*:?\s*', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s if s else 'Competitive'

def normalize_job_type(val: Any) -> str:
    if not val:
        return 'Full-time'
    s = str(val).strip()
    s = re.sub(r'(?i)terms of employment\s*:?\s*', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s if s else 'Full-time'

def clean_job_dict(job: Dict) -> Dict:
    cleaned = dict(job)
    
    # Vacancies
    cleaned['vacancies'] = normalize_vacancies(cleaned.get('vacancies'))
    
    # Dates
    cleaned['date_posted'] = normalize_date(cleaned.get('date_posted'))
    cleaned['advertised_until'] = normalize_date(cleaned.get('advertised_until'))
    if 'start_date' in cleaned:
        cleaned['start_date'] = normalize_date(cleaned.get('start_date'))
        
    # NOC Code
    cleaned['noc_code'] = normalize_noc(cleaned.get('noc_code'))
    
    # Salary & Job Type
    cleaned['salary'] = normalize_salary(cleaned.get('salary'))
    cleaned['job_type'] = normalize_job_type(cleaned.get('job_type'))
    
    # Strings whitespace trimming
    for k in ['job_title', 'employer_name', 'employer_email', 'company_phone', 'company_website', 'city', 'province', 'full_address']:
        if k in cleaned and cleaned[k]:
            cleaned[k] = re.sub(r'\s+', ' ', str(cleaned[k])).strip()
            
    # Email lowercasing
    if cleaned.get('employer_email'):
        cleaned['employer_email'] = str(cleaned['employer_email']).lower().strip()
        
    return cleaned
