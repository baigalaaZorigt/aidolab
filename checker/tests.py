from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from . import hosting, worker
from .models import Deploy, EnvVar, Job, Service

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

    @mock.patch("checker.worker.time.sleep")
    @mock.patch("checker.worker.aws")
    def test_worker_builds_and_starts(self, aws, _sleep):
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
