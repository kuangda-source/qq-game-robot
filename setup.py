from setuptools import find_packages, setup

setup(
    name="qq-discount-bot",
    version="0.1.0",
    description="QQ discount game bot powered by Steam APIs and XiaoHeiHe public scraping",
    python_requires=">=3.9",
    packages=find_packages(include=["app", "app.*"]),
    install_requires=[
        "fastapi>=0.115.0",
        "uvicorn[standard]>=0.30.0",
        "httpx>=0.27.0",
        "sqlalchemy>=2.0.30",
        "psycopg[binary]>=3.2.0",
        "redis>=5.0.0",
        "pydantic>=2.7.0",
        "pydantic-settings>=2.3.0",
        "apscheduler>=3.10.4",
        "openai>=1.51.0",
        "beautifulsoup4>=4.12.0",
        "eval_type_backport>=0.3.1; python_version < '3.10'",
    ],
    extras_require={
        "dev": [
            "pytest>=8.2.0",
            "pytest-asyncio>=0.23.7",
            "respx>=0.21.1",
        ]
    },
)
