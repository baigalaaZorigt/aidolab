from django.contrib import admin
from django.urls import path

from checker import accounts, views, workspace

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", accounts.login_view, name="login"),
    path("signup/", accounts.signup_view, name="signup"),
    path("logout/", accounts.logout_view, name="logout"),
    path("account/", accounts.account_view, name="account"),
    path("billing/", workspace.billing, name="billing"),
    path("billing/invoice/<int:pk>/", workspace.invoice, name="invoice"),
    path("mentor/", workspace.mentor, name="mentor"),
    path("logs/", workspace.system_log, name="system_log"),
    path("metrics/", views.metrics_overview, name="metrics"),
    path("networking/dedicated-ip/", workspace.dedicated_ip, name="dedicated_ip"),
    path("deploys/", views.deploys_list, name="deploys"),
    path("databases/", views.databases_list, name="databases"),
    path("storage/", views.storage_list, name="storage"),
    path("new/", views.new_service, name="new"),
    path("services/<int:pk>/", views.service_overview, name="service"),
    path("services/<int:pk>/events/", views.service_events, name="service_events"),
    path("services/<int:pk>/logs/", views.service_logs, name="service_logs"),
    path("services/<int:pk>/metrics/", views.service_metrics, name="service_metrics"),
    path("services/<int:pk>/env/", views.service_env, name="service_env"),
    path("services/<int:pk>/database/", views.service_database, name="service_database"),
    path("services/<int:pk>/storage/", views.service_storage, name="service_storage"),
    path("services/<int:pk>/domains/", views.service_domains, name="service_domains"),
    path("services/<int:pk>/settings/", views.service_settings, name="service_settings"),
    path("services/<int:pk>/deploy/", views.deploy, name="deploy"),
    path("services/<int:pk>/suspend/", views.suspend, name="suspend"),
    path("services/<int:pk>/rescan/", views.rescan, name="rescan"),
    path("services/<int:pk>/delete/", views.delete_service, name="delete"),
    path("admin/", admin.site.urls),
]
