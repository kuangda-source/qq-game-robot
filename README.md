# QQ 群折扣游戏机器人

Steam 官方数据 + 小黑盒公开页面抓取，支持：
- 每日 10:00 热门折扣播报
- `@机器人 查游戏 <名字>` 查询类型/价格/评价
- `@机器人 推荐 和<游戏名>类似且在打折` 推荐
- 同名歧义 Top3 二次选择

## 模块
- `app/clients/steam_client.py`: Steam 官方接口
- `app/clients/xhh_spider.py`: 小黑盒公开页面抓取（非登录态）
- `app/services/game_service.py`: 核心业务接口
- `app/services/nlp_recommendation.py`: 规则召回 + LLM 重排
- `app/qq_adapter.py`: QQ 命令解析与响应
- `app/scheduler.py`: 09:50 预热、10:00 日报、30 分钟增量更新

## 快速启动
1. 安装依赖
```bash
pip install -e .[dev]
```

2. 启动 PostgreSQL + Redis
```bash
docker compose up -d postgres redis
```

3. 配置环境变量
```bash
cp .env.example .env
# 填写 QQ 与 OpenAI 配置
```

4. 启动服务
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

5. 健康检查
```bash
curl http://127.0.0.1:8000/health
```

## QQ 事件入口
- Webhook: `POST /qq/events`
- 当前支持消息事件（含 @ 文本）

## 核心业务接口
- `get_daily_hot_discounts(limit, region="cn", currency="CNY")`
- `query_game_snapshot(name_or_appid)`
- `recommend_similar_discounted(seed_game, top_k=5)`
- `resolve_ambiguous_name(query)`

## 测试
```bash
pytest
```
