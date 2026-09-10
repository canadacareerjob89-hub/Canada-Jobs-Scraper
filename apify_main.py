import os
import asyncio
import sys
from apify import Actor

import db
import core_scraper
import update_noc_codes
import main
import google_sheets

async def main_async():
    async with Actor:
        # Read Actor Input
        actor_input = await Actor.get_input() or {}
        mode = actor_input.get("mode", "daily")
        max_jobs = int(actor_input.get("max_jobs", 0))
        
        # Database URL (Neon PostgreSQL or fallback)
        db_url = actor_input.get("database_url", "").strip()
        if db_url:
            os.environ["DATABASE_URL"] = db_url

        # Google Sheets Configuration
        sheet_id = actor_input.get("google_sheet_id", "").strip()
        if sheet_id:
            os.environ["GOOGLE_SHEET_ID"] = sheet_id
        
        tab_name = actor_input.get("google_sheet_tab_name", "Today_LMIA_Jobs").strip()
        if tab_name:
            os.environ["GOOGLE_SHEET_TAB_NAME"] = tab_name
            
        sa_json = actor_input.get("google_service_account_json", "").strip()
        if sa_json:
            os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = sa_json

        webhook_url = actor_input.get("google_sheet_webhook_url", "").strip()
        if webhook_url:
            os.environ["GOOGLE_SHEET_WEBHOOK_URL"] = webhook_url

        db_type = "Neon PostgreSQL" if db.is_postgres() else "SQLite"
        Actor.log.info(f"=== Starting Canada LMIA Scraper Actor (Mode: {mode}, DB: {db_type}) ===")
        
        # 1. Initialize DB
        db.init_db()

        limit = max_jobs if max_jobs > 0 else None
        new_scraped_jobs = []

        # 2. Run selected mode
        if mode == "daily":
            Actor.log.info("Running daily delta scrape...")
            new_scraped_jobs = core_scraper.scrape_lmia_jobs(limit=limit, stop_on_seen=True)
            Actor.log.info(f"Discovered {len(new_scraped_jobs)} new jobs. Resolving NOC codes...")
            update_noc_codes.update_all_noc_codes(concurrency=15)
            
        elif mode == "full":
            Actor.log.info("Running full scrape across all active postings...")
            new_scraped_jobs = core_scraper.scrape_lmia_jobs(limit=limit, stop_on_seen=False)
            Actor.log.info("Resolving NOC codes for all jobs...")
            update_noc_codes.update_all_noc_codes(concurrency=15)
            
        elif mode == "update_noc":
            Actor.log.info("Running NOC Code Multi-Tier Resolver...")
            update_noc_codes.update_all_noc_codes(concurrency=15)

        # 3. Auto-sync newly scraped jobs to Google Sheet (overwrites yesterday's tab)
        jobs_to_sync = new_scraped_jobs if new_scraped_jobs else db.get_all_jobs(active_only=True)
        if jobs_to_sync:
            Actor.log.info(f"Syncing {len(jobs_to_sync)} active/today records to Google Sheets (Tab: '{tab_name}')...")
            synced = google_sheets.sync_today_jobs_to_sheets(
                jobs_to_sync,
                sheet_id=sheet_id or None,
                tab_name=tab_name,
                webhook_url=webhook_url or None
            )
            if synced:
                Actor.log.info(f"SUCCESS: Google Sheet tab '{tab_name}' updated with today's jobs.")

        # 4. Export to CSV file on disk
        all_active_jobs = db.get_all_jobs(active_only=True)
        main.export_jobs_to_csv(all_active_jobs, "lmia_jobs_master.csv")

        # 5. Push all records to Apify Dataset (Allows 1-click CSV/Excel/JSON export in Apify UI)
        Actor.log.info(f"Pushing {len(all_active_jobs)} active LMIA jobs to Apify Dataset...")
        await Actor.push_data(all_active_jobs)

        Actor.log.info(f"SUCCESS: {len(all_active_jobs)} jobs published to Apify Dataset & Neon DB.")

if __name__ == "__main__":
    asyncio.run(main_async())
