import os
import json
import base64
from datetime import datetime
from typing import List, Dict, Optional
import requests

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

SHEET_HEADERS = [
    "Job ID",
    "Job Title",
    "NOC Code",
    "Employer Name",
    "Employer Email",
    "Company Phone",
    "Company Website",
    "City",
    "Province",
    "Full Address",
    "Salary",
    "Job Type",
    "Vacancies",
    "LMIA Status",
    "Date Posted",
    "Advertised Until",
    "Job Bank Link",
    "Enrichment Status",
    "Scraped Date"
]

def format_job_row(job: Dict) -> List[str]:
    return [
        str(job.get("job_id") or ""),
        str(job.get("job_title") or ""),
        str(job.get("noc_code") or ""),
        str(job.get("employer_name") or ""),
        str(job.get("employer_email") or ""),
        str(job.get("company_phone") or ""),
        str(job.get("company_website") or ""),
        str(job.get("city") or ""),
        str(job.get("province") or ""),
        str(job.get("full_address") or ""),
        str(job.get("salary") or ""),
        str(job.get("job_type") or ""),
        str(job.get("vacancies") or ""),
        str(job.get("lmia_status") or "LMIA requested"),
        str(job.get("date_posted") or ""),
        str(job.get("advertised_until") or ""),
        str(job.get("job_url") or ""),
        str(job.get("enrichment_status") or ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ]

def get_gspread_client():
    if not GSPREAD_AVAILABLE:
        return None

    # Check for service account json in env (raw json or base64)
    sa_json_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "service_account.json")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = None
    if sa_json_env:
        try:
            # Try raw json
            sa_info = json.loads(sa_json_env)
        except Exception:
            try:
                # Try base64 decoded
                sa_info = json.loads(base64.b64decode(sa_json_env).decode("utf-8"))
            except Exception as e:
                print(f"[!] Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON: {e}")
                return None
        creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    elif os.path.exists(sa_file_path):
        creds = Credentials.from_service_account_file(sa_file_path, scopes=scopes)

    if creds:
        return gspread.authorize(creds)
    return None

def sync_today_jobs_to_sheets(
    jobs_list: List[Dict],
    sheet_id: Optional[str] = None,
    tab_name: Optional[str] = None,
    webhook_url: Optional[str] = None
) -> bool:
    """
    Synchronize today's newly scraped jobs to a designated Google Sheet tab.
    OVERWRITES the previous day's data so the tab strictly reflects current active listings.
    """
    if not jobs_list:
        print("[i] Google Sheets Sync: No jobs in batch to sync.")
        return False

    sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID") or os.getenv("SPREADSHEET_ID")
    tab_name = tab_name or os.getenv("GOOGLE_SHEET_TAB_NAME", "Today_LMIA_Jobs")
    webhook_url = webhook_url or os.getenv("GOOGLE_SHEET_WEBHOOK_URL")

    # Method 1: Google Apps Script Webhook (Zero GCP Setup required)
    if webhook_url:
        print(f"[*] Syncing {len(jobs_list)} records to Google Sheets via Webhook...")
        try:
            payload = {
                "tab_name": tab_name,
                "action": "overwrite_today",
                "headers": SHEET_HEADERS,
                "rows": [format_job_row(j) for j in jobs_list],
                "date": datetime.now().strftime("%Y-%m-%d")
            }
            res = requests.post(webhook_url, json=payload, timeout=30)
            if res.status_code == 200:
                print(f"[+] SUCCESS: Google Sheet tab '{tab_name}' updated with {len(jobs_list)} jobs via Webhook!")
                return True
            else:
                print(f"[!] Webhook error ({res.status_code}): {res.text}")
        except Exception as e:
            print(f"[!] Failed to sync via Google Sheets Webhook: {e}")

    # Method 2: Google Service Account (gspread)
    gc = get_gspread_client()
    if gc and sheet_id:
        print(f"[*] Syncing {len(jobs_list)} records to Google Sheet ({sheet_id}) tab '{tab_name}'...")
        try:
            sh = gc.open_by_key(sheet_id)
            try:
                wks = sh.worksheet(tab_name)
            except gspread.exceptions.WorksheetNotFound:
                wks = sh.add_worksheet(title=tab_name, rows="1000", cols="20")

            # Format all rows
            rows_data = [format_job_row(j) for j in jobs_list]

            # Clear existing data from yesterday to ensure only today's data is displayed
            wks.clear()

            # Write header row + all new rows in a single batch
            all_content = [SHEET_HEADERS] + rows_data
            wks.update(all_content, value_input_option="USER_ENTERED")

            # Format header row (Bold + freeze)
            try:
                wks.format("A1:S1", {"textFormat": {"bold": True}})
                wks.freeze(rows=1)
            except Exception:
                pass

            print(f"[+] SUCCESS: Google Sheet '{sh.title}' -> tab '{tab_name}' updated with {len(jobs_list)} fresh records (overwrote previous run).")
            return True
        except Exception as e:
            print(f"[!] Google Sheets API error: {e}")
            return False

    if not sheet_id and not webhook_url:
        print("[i] Google Sheets Sync skipped (No GOOGLE_SHEET_ID or GOOGLE_SHEET_WEBHOOK_URL provided).")

    return False
