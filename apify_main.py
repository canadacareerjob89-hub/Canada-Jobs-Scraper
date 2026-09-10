import os
import asyncio
import sys
from apify import Actor

import db
import core_scraper
import update_noc_codes
import hermes_enrichment
import main

async def main_async():
    async with Actor:
        # Read Actor Input
        actor_input = await Actor.get_input() or {}
        mode = actor_input.get("mode", "daily")
        max_jobs = int(actor_input.get("max_jobs", 0))
        enable_hermes_ai = actor_input.get("enable_hermes_ai", False)
        api_key = actor_input.get("openrouter_api_key", "").strip()

        if api_key:
            hermes_enrichment.OPENROUTER_API_KEY = api_key
            os.environ["OPENROUTER_API_KEY"] = api_key

        Actor.log.info(f"=== Starting Canada LMIA Scraper Actor (Mode: {mode}) ===")
        
        # 1. Initialize DB and clean expired jobs
        db.init_db()
        purged = db.clean_expired_jobs()
        if purged > 0:
            Actor.log.info(f"Purged {purged} expired job postings from database.")

        limit = max_jobs if max_jobs > 0 else None

        # 2. Run selected mode
        if mode == "daily":
            Actor.log.info("Running daily delta scrape...")
            core_scraper.scrape_lmia_jobs(limit=limit, stop_on_seen=True)
            Actor.log.info("Resolving NOC codes for newly discovered jobs...")
            update_noc_codes.update_all_noc_codes(concurrency=15)
            
        elif mode == "full":
            Actor.log.info("Running full scrape across all active postings...")
            core_scraper.scrape_lmia_jobs(limit=limit, stop_on_seen=False)
            Actor.log.info("Resolving NOC codes for all jobs...")
            update_noc_codes.update_all_noc_codes(concurrency=15)
            
        elif mode == "update_noc":
            Actor.log.info("Running NOC Code Multi-Tier Resolver...")
            update_noc_codes.update_all_noc_codes(concurrency=15)

        # 3. Optional Hermes AI Enrichment
        if enable_hermes_ai:
            Actor.log.info("Running Hermes AI Enrichment on pending jobs...")
            hermes_enrichment.run_hermes_enrichment()

        # 4. Export to CSV file on disk
        all_active_jobs = db.get_all_jobs(active_only=True)
        main.export_jobs_to_csv(all_active_jobs, "lmia_jobs_master.csv")

        # 5. Push all records to Apify Dataset (Allows 1-click CSV/Excel/JSON export in Apify UI)
        Actor.log.info(f"Pushing {len(all_active_jobs)} active LMIA jobs to Apify Dataset...")
        await Actor.push_data(all_active_jobs)

        Actor.log.info(f"SUCCESS: {len(all_active_jobs)} jobs published to Apify Dataset.")

if __name__ == "__main__":
    asyncio.run(main_async())
