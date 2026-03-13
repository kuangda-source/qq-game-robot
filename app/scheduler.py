from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
from app.qq_adapter import QQAdapter
from app.services.game_service import GameService

logger = logging.getLogger(__name__)


class BotScheduler:
    def __init__(self, settings: Settings, service: GameService, adapter: QQAdapter):
        self.settings = settings
        self.service = service
        self.adapter = adapter
        self.scheduler = BackgroundScheduler(timezone=settings.timezone)

    def start(self) -> None:
        self.scheduler.add_job(
            self.service.refresh_market_data,
            trigger=CronTrigger(minute="*/30"),
            kwargs={"limit": self.settings.refresh_batch_size},
            id="refresh_every_30m",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.service.refresh_market_data,
            trigger=CronTrigger(hour=9, minute=50),
            kwargs={"limit": self.settings.refresh_batch_size},
            id="daily_prewarm_0950",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.push_daily_digest,
            trigger=CronTrigger(hour=10, minute=0),
            id="daily_push_1000",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    def push_daily_digest(self) -> None:
        channels = self.settings.target_channel_list()
        groups = self.settings.target_group_list()
        if not channels and not groups:
            logger.info("No QQ target channels/groups configured, skip daily push")
            return

        for channel_id in channels:
            ok = self.adapter.on_daily_push(target_id=channel_id, scene="channel")
            if not ok:
                logger.warning("Daily push failed for channel %s", channel_id)

        if groups:
            logger.warning("Group proactive message quota is strict on QQ. Daily push may be limited by platform policy.")
        for group_openid in groups:
            ok = self.adapter.on_daily_push(target_id=group_openid, scene="group")
            if not ok:
                logger.warning("Daily push failed for group %s", group_openid)
