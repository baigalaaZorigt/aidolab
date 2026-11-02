"""Real hosting on AWS (HOSTING_BACKEND=aws). Resources come from infra/ (Terraform).

Per app (service pk N, slug S):
  image        <ECR>:s{N}-{deploy number}       built by CodeBuild from the student's repo
  ECS service  app-{N}  (Fargate Spot, private subnets, port 8000)
  Cloud Map    S.apps.aidolab.internal          nginx on the gateway proxies S.aidolab.mn here
  task role    /aidolab/apps/aidolab-app-{N}    boundary-limited to its S3 prefix
  SSM params   /aidolab/apps/{N}/{KEY}          env vars, injected as ECS secrets
  database     db_S / role u_S on the shared RDS
  backups      <BACKUP_BUCKET>/{N}/…dump   pg_dump taken by the worker before each migration

Student code never runs on the gateway; this module only calls AWS APIs.
"""
import base64
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from functools import cache

import boto3
from django.conf import settings

from .plans import task_size

PORT = 8000  # cpu/memory come from the owner's plan (plans.task_size)
APP_DOMAIN = "apps.aidolab.internal"  # Cloud Map namespace (infra/apps.tf); nginx proxies to <slug>.APP_DOMAIN


def cfg(key: str) -> str:
    return settings.AIDOLAB[key]


def cfg_or_none(key: str) -> str | None:
    return settings.AIDOLAB.get(key)


@cache
def client(name: str):
    return boto3.client(name, region_name=settings.AWS_REGION)


def name_for(service) -> str:
    return f"app-{service.pk}"


def role_name(service) -> str:
    return f"aidolab-app-{service.pk}"


def param_prefix(service_pk: int) -> str:
    return f"{cfg('SSM_PREFIX')}/{service_pk}"


# --- Build -----------------------------------------------------------------------

def start_build(service, number: int, token: str | None = None, commit: str = "",
                dockerfile: str = "") -> tuple[str, str]:
    """`token`: 1-hour read-only GitHub App token for a private repo; buildspec.yml uses it only to clone.
    `commit`: the scanned commit to build. `dockerfile`: from buildplan, used when the repo has none."""
    # ECR tags are immutable; the suffix keeps a retried or re-numbered deploy from hitting an existing tag.
    tag = f"s{service.pk}-{number}-{secrets.token_hex(3)}"
    env = [{"name": "REPO_URL", "value": service.repo_url, "type": "PLAINTEXT"},
           {"name": "IMAGE_TAG", "value": tag, "type": "PLAINTEXT"},
           {"name": "COMMIT", "value": commit, "type": "PLAINTEXT"},
           {"name": "DOCKERFILE_B64", "value": base64.b64encode(dockerfile.encode()).decode(), "type": "PLAINTEXT"},
           {"name": "GITHUB_TOKEN", "value": token or "", "type": "PLAINTEXT"}]
    build = client("codebuild").start_build(projectName=cfg("BUILD_PROJECT"), environmentVariablesOverride=env)["build"]
    return build["id"], f"{cfg('ECR_REPO')}:{tag}"


def build_state(build_id: str) -> tuple[str, str | None]:
    """(IN_PROGRESS | SUCCEEDED | FAILED | FAULT | TIMED_OUT | STOPPED, log stream name)"""
    build = client("codebuild").batch_get_builds(ids=[build_id])["builds"][0]
    return build["buildStatus"], build.get("logs", {}).get("streamName")


def build_log(stream: str, token: str | None) -> tuple[list[str], str | None]:
    kwargs = {"logGroupName": cfg("BUILD_LOG_GROUP"), "logStreamName": stream, "startFromHead": True}
    if token:
        kwargs["nextToken"] = token
    try:
        resp = client("logs").get_log_events(**kwargs)
    except client("logs").exceptions.ResourceNotFoundException:
        return [], token  # stream not created yet
    return [e["message"].rstrip() for e in resp["events"]], resp["nextForwardToken"]


# --- Config and permissions ------------------------------------------------------

def sync_params(service, values: dict[str, str]) -> list[dict]:
    """Write the app's env vars to SSM (SecureString), drop stale ones, return ECS `secrets`."""
    ssm = client("ssm")
    prefix = param_prefix(service.pk)
    existing = set()
    for page in ssm.get_paginator("get_parameters_by_path").paginate(Path=prefix):
        existing.update(p["Name"] for p in page["Parameters"])
    secrets = []
    for key, value in values.items():
        name = f"{prefix}/{key}"
        ssm.put_parameter(Name=name, Value=value, Type="SecureString", Overwrite=True)
        existing.discard(name)
        secrets.append({"name": key, "valueFrom": name})  # same region: the name is enough
    for i in range(0, len(existing), 10):
        ssm.delete_parameters(Names=sorted(existing)[i:i + 10])
    return secrets


def ensure_task_role(service) -> str:
    iam = client("iam")
    name = role_name(service)
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(
            RoleName=name, Path=cfg("APP_ROLE_PATH"), PermissionsBoundary=cfg("APP_BOUNDARY_ARN"),
            AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"Service": "ecs-tasks.amazonaws.com"}}]}),
            Tags=[{"Key": "Project", "Value": "aidolab"}],
        )["Role"]["Arn"]
        time.sleep(10)  # IAM is eventually consistent; ECS rejects brand-new roles for a few seconds
    bucket = cfg("FILES_BUCKET")
    prefix = f"{service.slug}/"
    iam.put_role_policy(RoleName=name, PolicyName="own-files", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
             "Resource": f"arn:aws:s3:::{bucket}/{prefix}*"},
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{bucket}",
             "Condition": {"StringLike": {"s3:prefix": [f"{prefix}*"]}}},
        ]}))
    return arn


def ensure_registry(service) -> str:
    sd = client("servicediscovery")
    namespace = cfg("NAMESPACE_ID")
    for page in sd.get_paginator("list_services").paginate(
            Filters=[{"Name": "NAMESPACE_ID", "Values": [namespace], "Condition": "EQ"}]):
        for s in page["Services"]:
            if s["Name"] == service.slug:
                return s["Arn"]
    return sd.create_service(
        Name=service.slug, NamespaceId=namespace,
        DnsConfig={"RoutingPolicy": "MULTIVALUE", "DnsRecords": [{"Type": "A", "TTL": 10}]},
        HealthCheckCustomConfig={"FailureThreshold": 1},
        Tags=[{"Key": "Project", "Value": "aidolab"}],
    )["Service"]["Arn"]


# --- Run -------------------------------------------------------------------------

def register_task(service, image: str, secrets: list[dict], role_arn: str) -> str:
    name = name_for(service)
    cpu, memory = task_size(service.owner)
    return client("ecs").register_task_definition(
        family=name, networkMode="awsvpc", requiresCompatibilities=["FARGATE"], cpu=cpu, memory=memory,
        runtimePlatform={"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        executionRoleArn=cfg("EXEC_ROLE_ARN"), taskRoleArn=role_arn,
        containerDefinitions=[{
            "name": "app", "image": image, "essential": True,
            "portMappings": [{"containerPort": PORT}],
            "environment": [{"name": "PORT", "value": str(PORT)}],
            "secrets": secrets,
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": cfg("APP_LOG_GROUP"), "awslogs-region": settings.AWS_REGION,
                "awslogs-stream-prefix": name}},
        }],
        tags=[{"key": "Project", "value": "aidolab"}],  # ECS uses lowercase tag keys
    )["taskDefinition"]["taskDefinitionArn"]


def describe(service) -> dict | None:
    found = client("ecs").describe_services(cluster=cfg("CLUSTER"), services=[name_for(service)])["services"]
    return found[0] if found and found[0]["status"] != "INACTIVE" else None


# The old task keeps serving until the new one runs (minimumHealthyPercent 100); if the new one
# keeps crashing, the circuit breaker puts the old version back without the app going down.
DEPLOYMENT = {"minimumHealthyPercent": 100, "maximumPercent": 200,
              "deploymentCircuitBreaker": {"enable": True, "rollback": True}}


def run(service, task_arn: str, registry_arn: str) -> None:
    ecs = client("ecs")
    if describe(service):
        ecs.update_service(cluster=cfg("CLUSTER"), service=name_for(service), taskDefinition=task_arn,
                           desiredCount=1, forceNewDeployment=True, deploymentConfiguration=DEPLOYMENT)
        return
    ecs.create_service(
        cluster=cfg("CLUSTER"), serviceName=name_for(service), taskDefinition=task_arn, desiredCount=1,
        capacityProviderStrategy=[{"capacityProvider": "FARGATE_SPOT", "weight": 1}],
        networkConfiguration=network(),
        serviceRegistries=[{"registryArn": registry_arn}],
        deploymentConfiguration=DEPLOYMENT,
        propagateTags="SERVICE", tags=[{"key": "Project", "value": "aidolab"}],
    )


def network() -> dict:
    return {"awsvpcConfiguration": {"subnets": cfg("SUBNETS").split(","), "securityGroups": [cfg("APP_SG")],
                                    "assignPublicIp": "DISABLED"}}


def start_release(service, task_arn: str, command: str) -> str:
    """Run the release command (migrations) once, in the new image, before the service switches to it.
    On-demand Fargate: a Spot interruption must not cut a migration in half."""
    resp = client("ecs").run_task(
        cluster=cfg("CLUSTER"), taskDefinition=task_arn, count=1, launchType="FARGATE",
        networkConfiguration=network(), startedBy="aidolab-release",
        overrides={"containerOverrides": [{"name": "app", "command": ["sh", "-c", command]}]},
        tags=[{"key": "Project", "value": "aidolab"}])
    if not resp["tasks"]:
        reason = resp["failures"][0].get("reason", "") if resp["failures"] else ""
        raise RuntimeError(f"run_task failed: {reason}")
    return resp["tasks"][0]["taskArn"]


def release_state(task_arn: str) -> tuple[str, int | None, str]:
    """(lastStatus, exit code once STOPPED, stop reason)"""
    task = client("ecs").describe_tasks(cluster=cfg("CLUSTER"), tasks=[task_arn])["tasks"][0]
    container = task["containers"][0]
    return task["lastStatus"], container.get("exitCode"), task.get("stoppedReason", "")


def stop_release(task_arn: str) -> None:
    client("ecs").stop_task(cluster=cfg("CLUSTER"), task=task_arn, reason="release timeout")


def task_log(service, task_arn: str, token: str | None) -> tuple[list[str], str | None]:
    """Output of one task (awslogs stream <prefix>/<container>/<task id>)."""
    stream = f"{name_for(service)}/app/{task_arn.rsplit('/', 1)[1]}"
    kwargs = {"logGroupName": cfg("APP_LOG_GROUP"), "logStreamName": stream, "startFromHead": True}
    if token:
        kwargs["nextToken"] = token
    try:
        resp = client("logs").get_log_events(**kwargs)
    except client("logs").exceptions.ResourceNotFoundException:
        return [], token
    return [e["message"].rstrip() for e in resp["events"]], resp["nextForwardToken"]


def rollout_state(service) -> tuple[str, str]:
    """(IN_PROGRESS | COMPLETED | FAILED, reason) for the newest ECS deployment."""
    svc = describe(service)
    if svc is None:
        return "FAILED", "ECS service алга"
    primary = next(d for d in svc["deployments"] if d["status"] == "PRIMARY")
    return primary.get("rolloutState", "IN_PROGRESS"), primary.get("rolloutStateReason", "")


def scale(service, count: int) -> None:
    if describe(service):
        client("ecs").update_service(cluster=cfg("CLUSTER"), service=name_for(service), desiredCount=count)


def app_log(service, limit: int = 200) -> list[str]:
    resp = client("logs").filter_log_events(
        logGroupName=cfg("APP_LOG_GROUP"), logStreamNamePrefix=f"{name_for(service)}/",
        startTime=int((time.time() - 24 * 3600) * 1000), limit=limit)
    return [e["message"].rstrip() for e in resp["events"]][-limit:]


# --- Database --------------------------------------------------------------------

def _admin_conn():
    import psycopg
    secret = json.loads(client("secretsmanager").get_secret_value(SecretId=cfg("DB_ADMIN_SECRET_ARN"))["SecretString"])
    return psycopg.connect(host=cfg("DB_HOST"), dbname="postgres", user=secret["username"],
                           password=secret["password"], sslmode="require", autocommit=True, connect_timeout=10)


def create_database(name: str, username: str, password: str) -> None:
    from psycopg import sql
    with _admin_conn() as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(username), sql.Literal(password)))
        conn.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(sql.Identifier(username)))  # RDS: needed to own the DB
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(username)))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))


def drop_database(name: str, username: str | None) -> None:
    """`username` None keeps the role (dropping a database left behind by a restore)."""
    from psycopg import sql
    with _admin_conn() as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        if username:
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(username)))


# --- Backups (before migrations) ---------------------------------------------------
# pg_dump/pg_restore run on the gateway as the app's own role: no student code runs here,
# and a restore can do no more than the app itself could. The password goes in the
# environment only (never argv). The dump goes to BACKUP_BUCKET, which no app role can reach.

PG_BIN = "/usr/lib/postgresql/17/bin"  # must match the RDS major version (infra/data.tf); deploy/bootstrap-hosting.sh


def pg_tool(name: str) -> str:
    path = os.path.join(PG_BIN, name)
    return path if os.path.exists(path) else name


def pg_run(db, tool: str, *args: str, dbname: str | None = None) -> None:
    env = {**os.environ, "PGPASSWORD": db.password, "PGSSLMODE": "require", "PGCONNECT_TIMEOUT": "10"}
    proc = subprocess.run([pg_tool(tool), "-h", cfg("DB_HOST"), "-U", db.username, "-d", dbname or db.name, *args],
                          env=env, capture_output=True, text=True, timeout=900)
    if proc.returncode:
        raise RuntimeError(f"{tool} failed: {proc.stderr.strip()[-500:]}")


def backup_database(db, key: str) -> int:
    """Dump the app's database to BACKUP_BUCKET/key. Returns the dump size in bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "db.dump")
        pg_run(db, "pg_dump", "--format=custom", "--no-owner", "--no-privileges", f"--file={path}")
        client("s3").upload_file(path, cfg("BACKUP_BUCKET"), key, ExtraArgs={"ServerSideEncryption": "AES256"})
        return os.path.getsize(path)


def restore_database(db, key: str) -> str:
    """Put the database back to the dump at `key`. The dump goes into a fresh database, which then
    takes the app database's name; the replaced one is renamed and returned (drop it later)."""
    from psycopg import sql
    fresh, old = f"{db.name}_restore", f"{db.name}_old_{int(time.time())}"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "db.dump")
        client("s3").download_file(cfg("BACKUP_BUCKET"), key, path)
        with _admin_conn() as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(fresh)))
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(fresh), sql.Identifier(db.username)))
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(fresh)))
        pg_run(db, "pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction", path,
               dbname=fresh)
    with _admin_conn() as conn:
        # No new connections while swapping, so the app can't grab the old database in between.
        conn.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(sql.Identifier(db.name)))
        try:
            conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s "
                         "AND pid <> pg_backend_pid()", [db.name])
            conn.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(db.name), sql.Identifier(old)))
        except Exception:
            conn.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(sql.Identifier(db.name)))
            raise
        conn.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(fresh), sql.Identifier(db.name)))
        # The app connects by name, so the replaced copy can be opened again (a mentor may need data from it).
        conn.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(sql.Identifier(old)))
    return old


# --- Teardown (run by the worker; each step is safe to repeat) ---------------------

def teardown(payload: dict) -> None:
    ecs, sd, iam, ssm = (client(n) for n in ("ecs", "servicediscovery", "iam", "ssm"))
    pk, slug = payload["pk"], payload["slug"]
    name = f"app-{pk}"
    try:
        ecs.delete_service(cluster=cfg("CLUSTER"), service=name, force=True)
    except (ecs.exceptions.ServiceNotFoundException, ecs.exceptions.ServiceNotActiveException):
        pass
    for page in sd.get_paginator("list_services").paginate(
            Filters=[{"Name": "NAMESPACE_ID", "Values": [cfg("NAMESPACE_ID")], "Condition": "EQ"}]):
        for s in page["Services"]:
            if s["Name"] == slug:
                sd.delete_service(Id=s["Id"])  # fails until ECS has deregistered the task; the job retries
    names = []
    for page in ssm.get_paginator("get_parameters_by_path").paginate(Path=param_prefix(pk)):
        names += [p["Name"] for p in page["Parameters"]]
    for i in range(0, len(names), 10):
        ssm.delete_parameters(Names=names[i:i + 10])
    try:
        iam.delete_role_policy(RoleName=f"aidolab-app-{pk}", PolicyName="own-files")
    except iam.exceptions.NoSuchEntityException:
        pass
    try:
        iam.delete_role(RoleName=f"aidolab-app-{pk}")
    except iam.exceptions.NoSuchEntityException:
        pass
    delete_images(pk)
    delete_task_definitions(name)
    delete_log_streams(name)
    if cfg_or_none("BACKUP_BUCKET"):
        delete_prefix(f"{pk}/", cfg("BACKUP_BUCKET"))
    if payload.get("files"):
        delete_prefix(f"{slug}/")
    if payload.get("database"):
        drop_database(*payload["database"])


def delete_images(pk: int, keep: int = 0) -> None:
    """Image tags are s<pk>-<deploy number>[-<suffix>] (start_build). Keeps the `keep` newest."""
    ecr, repo = client("ecr"), cfg("ECR_REPO").split("/", 1)[1]
    ids = []
    for page in ecr.get_paginator("list_images").paginate(repositoryName=repo, filter={"tagStatus": "TAGGED"}):
        ids += [i for i in page["imageIds"] if re.fullmatch(rf"s{pk}-\d+(-[0-9a-f]+)?", i.get("imageTag", ""))]
    ids.sort(key=lambda i: int(i["imageTag"].split("-")[1]))
    ids = ids[:len(ids) - keep] if keep else ids
    for i in range(0, len(ids), 100):
        ecr.batch_delete_image(repositoryName=repo, imageIds=ids[i:i + 100])


def delete_task_definitions(family: str, keep: int = 0) -> None:
    """Deregisters and deletes the family's revisions, except the `keep` newest active ones."""
    ecs = client("ecs")
    arns = []
    for status in ("ACTIVE", "INACTIVE"):
        for page in ecs.get_paginator("list_task_definitions").paginate(familyPrefix=family, status=status):
            # familyPrefix also matches app-12 for app-1, so compare the exact family.
            arns += [a for a in page["taskDefinitionArns"] if a.rsplit("/", 1)[1].rsplit(":", 1)[0] == family]
    arns.sort(key=lambda a: int(a.rsplit(":", 1)[1]))
    arns = arns[:len(arns) - keep] if keep else arns
    for arn in arns:
        try:
            ecs.deregister_task_definition(taskDefinition=arn)
        except ecs.exceptions.ClientException:
            pass  # already inactive
    for i in range(0, len(arns), 10):
        ecs.delete_task_definitions(taskDefinitions=arns[i:i + 10])


def prune_old_versions(service, keep: int = 5) -> None:
    """After a successful deploy: the live version and the four before it stay (rollback targets)."""
    delete_images(service.pk, keep=keep)
    delete_task_definitions(name_for(service), keep=keep)


def delete_log_streams(name: str) -> None:
    logs = client("logs")
    for page in logs.get_paginator("describe_log_streams").paginate(
            logGroupName=cfg("APP_LOG_GROUP"), logStreamNamePrefix=f"{name}/"):
        for stream in page["logStreams"]:
            logs.delete_log_stream(logGroupName=cfg("APP_LOG_GROUP"), logStreamName=stream["logStreamName"])


def delete_prefix(prefix: str, bucket: str | None = None) -> None:
    s3, bucket = client("s3"), bucket or cfg("FILES_BUCKET")
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})


# --- Metrics ---------------------------------------------------------------------

def cpu_memory(service, start, end, step: int) -> dict[str, dict]:
    """{'cpu': {epoch: %}, 'mem': {epoch: MB}} from the free AWS/ECS service metrics."""
    dims = [{"Name": "ClusterName", "Value": cfg("CLUSTER")}, {"Name": "ServiceName", "Value": name_for(service)}]
    queries = [{"Id": key, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": "AWS/ECS", "MetricName": metric, "Dimensions": dims},
        "Period": max(60, step), "Stat": "Average"}}
        for key, metric in (("cpu", "CPUUtilization"), ("mem", "MemoryUtilization"))]
    resp = client("cloudwatch").get_metric_data(MetricDataQueries=queries, StartTime=start, EndTime=end)
    out = {"cpu": {}, "mem": {}}
    memory_mb = int(task_size(service.owner)[1])
    for r in resp["MetricDataResults"]:
        scale_by = memory_mb / 100 if r["Id"] == "mem" else 1
        for ts, value in zip(r["Timestamps"], r["Values"]):
            out[r["Id"]][int(ts.timestamp()) // step * step] = round(value * scale_by, 1)
    return out
