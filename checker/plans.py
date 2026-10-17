"""Plan limits and prices (MNT). Edit here to change pricing.

Each plan sets how many apps can be deployed and the server size of each app
(ECS Fargate cpu units / memory MB, see aws.register_task). Real AWS cost per app
(Tokyo, ARM Fargate Spot, 24/7): 0.25 vCPU/512 MB ≈ 9,300₮, 0.5/1 GB ≈ 18,600₮,
1 vCPU/2 GB ≈ 37,000₮ a month.
"""

PLANS = {
    "free": {
        "name": "Үнэгүй", "price": 0, "apps": 1, "cpu": 256, "memory_mb": 512,
        "desc": "Туршиж үзэх: нэг жижиг апп 14 хоног интернетэд ажиллана.",
        "includes": "",
        "features": ["Апп 14 хоног ажиллаад зогсоно", "Repo шалгалт, AI зөвлөгөө", "нэр.aidolab.mn хаяг + SSL"],
        "database": False, "storage_mb": 0, "domains": 0, "dedicated_ip": False,
    },
    "starter": {
        "name": "Starter", "price": 29000, "apps": 1, "cpu": 512, "memory_mb": 1024,
        "desc": "Жинхэнэ хэрэглэгчтэй нэг апп-д: өгөгдлийн сан, файл, өөрийн домэйнтэй.",
        "includes": "Апп хугацаагүй, тасралтгүй ажиллана",
        "features": ["PostgreSQL өгөгдлийн сан", "Файл хадгалах 1 GB", "Өөрийн домэйн + автомат SSL"],
        "database": True, "storage_mb": 1024, "domains": 1, "dedicated_ip": False,
    },
    "standard": {
        "name": "Standard", "price": 79000, "apps": 3, "cpu": 512, "memory_mb": 1024, "popular": True,
        "desc": "Хэд хэдэн апп эсвэл апп + API хамт ажиллуулахад.",
        "includes": "Starter-ийн бүх боломж",
        "features": ["3 апп зэрэг ажиллана", "Файл хадгалах 5 GB", "Апп бүрт өөрийн домэйн"],
        "database": True, "storage_mb": 5120, "domains": 1, "dedicated_ip": False,
    },
    "pro": {
        "name": "Pro", "price": 199000, "apps": 5, "cpu": 1024, "memory_mb": 2048,
        "desc": "Илүү их ачаалалтай бизнесийн апп-уудад, 2 дахин хүчтэй сервер.",
        "includes": "Standard-ийн бүх боломж",
        "features": ["5 апп, апп бүр 1 vCPU · 2 GB", "Файл хадгалах 10 GB", "Dedicated IP"],
        "database": True, "storage_mb": 10240, "domains": 5, "dedicated_ip": True,
    },
}
PLAN_DAYS = 30
# Free hosting runs this many days per account, counted from the first deploy (hosting.expire_free_apps).
FREE_DAYS = 14
# Plans that existed before and may still be stored on profiles/invoices.
OLD_KEYS = {"student": "standard"}


def server_label(plan: dict) -> str:
    cpu = plan["cpu"] / 1024
    mem = plan["memory_mb"]
    return f"{cpu:g} vCPU · {mem // 1024} GB" if mem >= 1024 else f"{cpu:g} vCPU · {mem} MB"


def get_plan(key: str) -> dict:
    plan = PLANS[OLD_KEYS.get(key, key)]
    return {"key": OLD_KEYS.get(key, key), "price_label": f"{plan['price']:,}₮", "server": server_label(plan), **plan}


def task_size(user) -> tuple[str, str]:
    """ECS cpu units and memory MB for the user's apps, from their current plan."""
    from .models import profile_for  # models imports nothing from here; avoid a cycle at import time
    plan = get_plan(profile_for(user).effective_plan)
    return str(plan["cpu"]), str(plan["memory_mb"])
