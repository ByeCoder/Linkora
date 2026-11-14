"""A live read-only integration probe, independent of a Telegram token."""
import asyncio
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot.config import Config
from bot.provider import Waybien,ProviderError,PLATFORMS


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("query",nargs="?",default="python")
    parser.add_argument("--platform",choices=list(PLATFORMS),default="all")
    parser.add_argument("--offset",type=int,default=0)
    args=parser.parse_args()
    provider=Waybien(Config("unused",{1},cache_seconds=0))
    try:
        page=await provider.search(args.query,args.platform,args.offset)
        print(f"OK | total={page.total} | rows={len(page.items)} | offset={page.offset}")
        for row in page.items:print(row["id"],row["platform"],row["title"])
    except ProviderError as exc:
        print("FAIL:",str(exc))
        raise SystemExit(1)
    finally:await provider.close()


if __name__=="__main__":asyncio.run(main())
