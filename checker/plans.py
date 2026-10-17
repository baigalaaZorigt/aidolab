"""Plan limits and prices (MNT). Edit here to change pricing.

Every plan has one app; higher plans add server power and human services (mentor,
engineer help), not more apps. Server size = ECS Fargate cpu units / memory MB
(see aws.register_task). Real AWS cost per app (Tokyo, ARM Fargate Spot, 24/7):
0.25 vCPU/512 MB ≈ 9,300₮, 0.5/1 GB ≈ 18,600₮, 1/2 GB ≈ 37,000₮, 2/4 GB ≈ 74,500₮ a month.
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
        "name": "Starter", "price": 58000, "apps": 1, "cpu": 512, "memory_mb": 1024,
        "desc": "Жинхэнэ хэрэглэгчтэй апп-д: өгөгдлийн сан, файл, өөрийн домэйнтэй.",
        "includes": "Апп хугацаагүй, тасралтгүй ажиллана",
        "features": ["PostgreSQL өгөгдлийн сан", "Файл хадгалах 1 GB", "Өөрийн домэйн + автомат SSL", "Mentor-оос асуулт асуух"],
        "database": True, "storage_mb": 1024, "domains": 1, "dedicated_ip": False,
    },
    "standard": {
        "name": "Standard", "price": 158000, "apps": 1, "cpu": 1024, "memory_mb": 2048, "popular": True,
        "desc": "Илүү хүчтэй сервер, инженерийн тусламжтай. Апп-аа ганцаараа бүү хариуц.",
        "includes": "Starter-ийн бүх боломж",
        "features": ["Mentor түрүүлж хариулна", "Инженер deploy-г тань хамт тохируулна", "Сар бүр аюулгүй байдлын тайлан", "Файл хадгалах 5 GB"],
        "database": True, "storage_mb": 5120, "domains": 2, "dedicated_ip": False,
    },
    "pro": {
        "name": "Pro", "price": 398000, "apps": 1, "cpu": 2048, "memory_mb": 4096,
        "desc": "Бизнесийн чухал апп-д: хамгийн хүчтэй сервер, инженерийн байнгын дэмжлэг.",
        "includes": "Standard-ийн бүх боломж",
        "features": ["Сард 2 цаг инженерийн 1-on-1 зөвлөгөө", "Код засвар, алдаа арилгахад тусламж", "Dedicated IP", "Файл хадгалах 10 GB"],
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
