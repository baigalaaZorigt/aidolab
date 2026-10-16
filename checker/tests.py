import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from . import domains, hosting, worker
from .models import CustomDomain, Deploy, EnvVar, Job, Service

AWS = override_settings(HOSTING_BACKEND="aws", AIDOLAB={"FILES_BUCKET": "files"})


@AWS
class RealDeployTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("a@example.com", password="x")
        self.service = Service.objects.create(owner=self.user, name="todo", slug="todo-app",
                                              repo_url="https://github.com/x/todo", score=90)

    def test_deploy_is_queued_for_the_worker(self):
        d = hosting.start_deploy(self.service)
        self.assertEqual(d.state, Deploy.QUEUED)
        self.assertEqual(self.service.deploy_state, "building")

    @mock.patch("checker.worker.answers_http", return_value=True)
    @mock.patch("checker.worker.time.sleep")
    @mock.patch("checker.worker.aws")
    def test_worker_builds_and_starts(self, aws, _sleep, _http):
        aws.start_build.return_value = ("build:1", "repo:s1-1")
        aws.build_state.side_effect = [("IN_PROGRESS", "s"), ("SUCCEEDED", "s")]
        aws.build_log.return_value = (["step"], "t")
        aws.rollout_state.side_effect = [("IN_PROGRESS", ""), ("COMPLETED", "")]
        EnvVar.objects.create(service=self.service, key="API_KEY", value="v")
        hosting.start_deploy(self.service)

        worker.process(worker.claim_deploy())

        d = self.service.deploys.get()
        self.assertEqual(d.state, Deploy.LIVE)
        self.assertEqual(d.image, "repo:s1-1")
        aws.sync_params.assert_called_once_with(self.service, {"API_KEY": "v"})
        self.assertIn("step", [line for _, line in d.logs])

    @mock.patch("checker.worker.time.sleep")
    @mock.patch("checker.worker.aws")
    def test_failed_build_is_reported(self, aws, _sleep):
        aws.start_build.return_value = ("build:1", "repo:s1-1")
        aws.build_state.return_value = ("FAILED", None)
        hosting.start_deploy(self.service)

        worker.process(worker.claim_deploy())

        d = self.service.deploys.get()
        self.assertEqual(d.status, "failed")
        aws.run.assert_not_called()

    @mock.patch("checker.worker.HTTP_TIMEOUT", -1)
    @mock.patch("checker.worker.answers_http", return_value=False)
    @mock.patch("checker.worker.time.sleep")
    @mock.patch("checker.worker.aws")
    def test_running_but_silent_app_fails(self, aws, _sleep, _http):
        aws.start_build.return_value = ("build:1", "repo:s1-1")
        aws.build_state.return_value = ("SUCCEEDED", None)
        aws.rollout_state.return_value = ("COMPLETED", "")
        hosting.start_deploy(self.service)

        worker.process(worker.claim_deploy())

        d = self.service.deploys.get()
        self.assertEqual(d.state, Deploy.FAILED)
        self.assertNotIn("🎉", " ".join(line for _, line in d.logs))

    def test_claim_takes_each_deploy_once(self):
        hosting.start_deploy(self.service)
        self.assertIsNotNone(worker.claim_deploy())
        self.assertIsNone(worker.claim_deploy())

    def test_delete_queues_teardown(self):
        hosting.delete_service(self.service)
        job = Job.objects.get()
        self.assertEqual(job.kind, "teardown")
        self.assertEqual(job.payload["slug"], "todo-app")
        self.assertFalse(Service.objects.exists())

    @mock.patch("checker.worker.aws")
    def test_failed_job_backs_off(self, aws):
        aws.teardown.side_effect = RuntimeError("not yet")
        Job.objects.create(kind="teardown", payload={})
        worker.run_jobs()
        job = Job.objects.get()
        self.assertFalse(job.done)
        self.assertEqual(job.attempts, 1)


class DemoDeployTests(TestCase):
    def test_demo_deploy_stays_simulated(self):
        user = User.objects.create_user("b@example.com", password="x")
        service = Service.objects.create(owner=user, name="x", slug="xyz", repo_url="https://github.com/x/y", score=90)
        d = hosting.start_deploy(service)
        self.assertFalse(d.is_real)
        self.assertEqual(d.status, "building")


@AWS
class CustomDomainTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("a@example.com", password="x")
        self.service = Service.objects.create(owner=user, name="shop", slug="shop-app",
                                              repo_url="https://github.com/x/shop", score=90)
        self.tmp = Path(tempfile.mkdtemp())
        paths = override_settings(DOMAINS_DIR=self.tmp / "domains", DOMAINS_STATUS=self.tmp / "status.json",
                                  GATEWAY_IP="198.51.100.7")
        paths.enable()
        self.addCleanup(paths.disable)

    def dns(self, answers):
        return mock.patch("checker.domains.dns_lookup", side_effect=lambda name, rtype: answers.get((name, rtype), []))

    def wanted(self):
        return (self.tmp / "domains" / "wanted").read_text()

    def test_txt_verification_waits_for_ssl(self):
        d = CustomDomain.objects.create(service=self.service, domain="www.shop.mn")
        with self.dns({(d.txt_name, "TXT"): [d.token]}):
            domains.verify(d)
        self.assertEqual((d.status, d.ssl_status), (CustomDomain.VERIFIED, domains.SSL_PENDING))

    def test_only_domains_pointing_here_are_wanted(self):
        here = CustomDomain.objects.create(service=self.service, domain="www.shop.mn", status="verified")
        CustomDomain.objects.create(service=self.service, domain="old.shop.mn", status="verified")
        with self.dns({(here.domain, "A"): ["198.51.100.7"]}):
            domains.sync()
        self.assertEqual(self.wanted(), "www.shop.mn shop-app\n")

    def test_pending_txt_is_rechecked_automatically(self):
        d = CustomDomain.objects.create(service=self.service, domain="www.shop.mn")
        with self.dns({(d.txt_name, "TXT"): [d.token], (d.domain, "A"): ["198.51.100.7"]}):
            domains.sync()
        d.refresh_from_db()
        self.assertEqual(d.status, CustomDomain.VERIFIED)

    def test_root_status_is_shown(self):
        ok = CustomDomain.objects.create(service=self.service, domain="www.shop.mn", status="verified",
                                         ssl_status="pending")
        bad = CustomDomain.objects.create(service=self.service, domain="shop.mn", status="verified",
                                          ssl_status="pending")
        (self.tmp / "status.json").write_text(json.dumps({
            "www.shop.mn": {"ok": True}, "shop.mn": {"ok": False, "error": "Timeout during connect"}}))
        with self.dns({}):
            domains.sync()
        ok.refresh_from_db(), bad.refresh_from_db()
        self.assertEqual(ok.ssl_status, domains.SSL_ACTIVE)
        self.assertEqual((bad.ssl_status, bad.ssl_error), (domains.SSL_ERROR, "Timeout during connect"))
        self.assertEqual(self.wanted(), "www.shop.mn shop-app\n")  # active stays even without DNS answer

    @override_settings(HOSTING_BACKEND="demo")
    def test_demo_verification_is_active_at_once(self):
        d = CustomDomain.objects.create(service=self.service, domain="www.shop.mn")
        with self.dns({(d.txt_name, "TXT"): [d.token]}):
            domains.verify(d)
        self.assertEqual(d.ssl_status, domains.SSL_ACTIVE)


@AWS
class TeardownTests(TestCase):
    @mock.patch("checker.aws.client")
    def test_only_this_apps_task_definitions_are_deleted(self, client):
        from . import aws
        ecs = client.return_value
        ecs.get_paginator.return_value.paginate.side_effect = lambda **kw: [{"taskDefinitionArns": [
            "arn:aws:ecs:r:1:task-definition/app-1:3", "arn:aws:ecs:r:1:task-definition/app-12:1"]}] \
            if kw["status"] == "ACTIVE" else [{"taskDefinitionArns": []}]
        aws.delete_task_definitions("app-1")
        ecs.delete_task_definitions.assert_called_once_with(
            taskDefinitions=["arn:aws:ecs:r:1:task-definition/app-1:3"])

    @mock.patch("checker.aws.client")
    def test_only_this_apps_images_are_deleted(self, client):
        from . import aws
        ecr = client.return_value
        ecr.get_paginator.return_value.paginate.return_value = [{"imageIds": [
            {"imageTag": "s1-1"}, {"imageTag": "s1-2"}, {"imageTag": "s12-1"}]}]
        with override_settings(AIDOLAB={"ECR_REPO": "1.dkr.ecr.r.amazonaws.com/aidolab-apps"}):
            aws.delete_images(1)
        ecr.batch_delete_image.assert_called_once_with(
            repositoryName="aidolab-apps", imageIds=[{"imageTag": "s1-1"}, {"imageTag": "s1-2"}])
