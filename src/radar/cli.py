"""Command line entry point.

radar bot       Telegram bot (long polling) with the radar worker in-process
radar api       HTTP API + Mini App (optionally Telegram webhook)
radar worker    radar checks and notification delivery only
radar migrate   apply database migrations
radar search    run one search from the terminal (debugging)
radar providers print the provider capability matrix
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys

from radar.app import build_app, configure_logging
from radar.config import get_settings


def _bot(no_worker: bool) -> None:
    from radar.bot.main import run_polling

    asyncio.run(run_polling(build_app(), with_worker=not no_worker))


def _worker() -> None:
    from radar.bot.main import build_bot, worker_loop

    async def main() -> None:
        app = build_app()
        bot = build_bot(app)
        stop = asyncio.Event()
        try:
            await worker_loop(app, bot, stop)
        finally:
            await bot.session.close()
            await app.close()

    asyncio.run(main())


def _api(webhook_url: str | None) -> None:
    import uvicorn

    from radar.api.app import create_app

    s = get_settings()
    app_ctx = build_app()
    api = create_app(app_ctx)
    if webhook_url:
        from radar.bot.main import build_bot, build_dispatcher, setup_bot_profile

        bot = build_bot(app_ctx)
        dp = build_dispatcher(app_ctx)
        api.state.bot, api.state.dispatcher = bot, dp
        api.state.webhook_secret = secrets.token_urlsafe(32)

        @api.on_event("startup")
        async def _set_webhook() -> None:
            await setup_bot_profile(bot, app_ctx)
            await bot.set_webhook(
                f"{webhook_url.rstrip('/')}/telegram/webhook",
                secret_token=api.state.webhook_secret,
                drop_pending_updates=False,
            )

    uvicorn.run(api, host=s.api_host, port=s.api_port, proxy_headers=True, log_level=s.log_level.lower())


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")


def _search(text: str, currency: str) -> None:
    from radar.nlu.interpreter import UserContext
    from radar.providers.base import SearchContext

    async def main() -> None:
        app = build_app()
        try:
            interp = await app.interpreter.interpret(text, UserContext())
            print("intent:", interp.intent.model_dump_json(exclude_defaults=True, exclude={"raw_text"}))
            if interp.intent.missing:
                print("missing:", interp.intent.missing)
                return
            out = await app.pipeline.run(interp.intent, SearchContext(currency=currency))
            print(f"queried={out.queried} errors={out.provider_errors} skipped={out.skipped} {out.total_ms:.0f}ms")
            for r in out.ranked[:10]:
                p = r.result.price
                print(
                    f"[{r.tier.name:<11}] {r.result.title[:50]:<50} "
                    f"{str(p.amount) if p else '':>14} {r.result.provenance.freshness.value:<15} "
                    f"unverified={r.report.unverified}"
                )
        finally:
            await app.close()

    asyncio.run(main())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="radar")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bot")
    b.add_argument("--no-worker", action="store_true")
    a = sub.add_parser("api")
    a.add_argument("--webhook-url", default=None, help="public base URL; enables the Telegram webhook")
    sub.add_parser("worker")
    sub.add_parser("migrate")
    s = sub.add_parser("search")
    s.add_argument("text")
    s.add_argument("--currency", default="EUR")
    sub.add_parser("providers")
    args = parser.parse_args(argv)
    configure_logging(get_settings())
    if args.cmd == "bot":
        _bot(args.no_worker)
    elif args.cmd == "api":
        _api(args.webhook_url)
    elif args.cmd == "worker":
        _worker()
    elif args.cmd == "migrate":
        _migrate()
    elif args.cmd == "search":
        _search(args.text, args.currency)
    elif args.cmd == "providers":
        app = build_app()
        json.dump(app.registry.capability_matrix(), sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
