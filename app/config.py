from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    timezone: str = "Asia/Shanghai"

    database_url: str = "sqlite:///./bot.db"
    redis_url: str = "redis://localhost:6379/0"

    steam_base_url: str = "https://store.steampowered.com"
    steam_cc: str = "cn"
    steam_lang: str = "schinese"

    xhh_game_url_template: str = "https://www.xiaoheihe.cn/app/steam/{appid}"
    xhh_timeout_seconds: int = 10
    xhh_user_agents: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
    )

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"

    qq_api_base_url: str = "https://api.sgroup.qq.com"
    qq_access_token_url: str = "https://bots.qq.com/app/getAppAccessToken"
    qq_bot_app_id: str | None = None
    qq_bot_secret: str | None = None
    qq_bot_token: str | None = None
    qq_webhook_secret: str | None = None
    qq_target_channels: str = ""
    qq_target_groups: str = ""
    qq_bot_name: str = "机器人"

    enable_group_mode: bool = False
    allow_session_fallback: bool = True

    hot_default_limit: int = 10
    refresh_batch_size: int = 60

    min_recommend_review_percent: int = 70
    disambiguation_ttl_seconds: int = 300

    request_timeout_seconds: int = 12

    def target_channel_list(self) -> List[str]:
        return [item.strip() for item in self.qq_target_channels.split(",") if item.strip()]

    def target_group_list(self) -> List[str]:
        return [item.strip() for item in self.qq_target_groups.split(",") if item.strip()]

    def xhh_agent_pool(self) -> List[str]:
        return [item.strip() for item in self.xhh_user_agents.split("||") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
