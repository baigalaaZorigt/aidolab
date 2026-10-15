"""Plan limits and prices (MNT). Edit here to change pricing."""

PLANS = {
    "free": {"name": "Үнэгүй", "price": 0, "apps": 1, "database": False, "storage_mb": 0,
             "domains": 0, "sleeps": True, "dedicated_ip": False},
    "student": {"name": "Сурагч", "price": 29000, "apps": 3, "database": True, "storage_mb": 1024,
                "domains": 1, "sleeps": True, "dedicated_ip": False},
    "pro": {"name": "Pro", "price": 79000, "apps": 10, "database": True, "storage_mb": 10240,
            "domains": 5, "sleeps": False, "dedicated_ip": True},
}
PLAN_DAYS = 30


def get_plan(key: str) -> dict:
    plan = PLANS[key]
    return {"key": key, "price_label": f"{plan['price']:,}₮", **plan}
