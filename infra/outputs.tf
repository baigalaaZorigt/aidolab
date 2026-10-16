output "gateway_ip" {
  value = aws_eip.gateway.public_ip
}

# Appended to /etc/aidolab/env on the new gateway (see deploy/README.md, "Hosting").
output "gateway_env" {
  value = <<-ENV
    HOSTING_BACKEND=aws
    AWS_REGION=${var.region}
    GATEWAY_IP=${aws_eip.gateway.public_ip}
    AIDOLAB_CLUSTER=${aws_ecs_cluster.apps.name}
    AIDOLAB_SUBNETS=${join(",", aws_subnet.private[*].id)}
    AIDOLAB_APP_SG=${aws_security_group.apps.id}
    AIDOLAB_BUILD_PROJECT=${aws_codebuild_project.build.name}
    AIDOLAB_ECR_REPO=${aws_ecr_repository.apps.repository_url}
    AIDOLAB_APP_LOG_GROUP=${aws_cloudwatch_log_group.apps.name}
    AIDOLAB_BUILD_LOG_GROUP=${aws_cloudwatch_log_group.builds.name}
    AIDOLAB_EXEC_ROLE_ARN=${aws_iam_role.task_execution.arn}
    AIDOLAB_APP_ROLE_PATH=${local.role_path}
    AIDOLAB_APP_BOUNDARY_ARN=${aws_iam_policy.app_boundary.arn}
    AIDOLAB_FILES_BUCKET=${aws_s3_bucket.files.bucket}
    AIDOLAB_NAMESPACE_ID=${aws_service_discovery_private_dns_namespace.apps.id}
    AIDOLAB_APP_DOMAIN=${local.app_domain}
    AIDOLAB_SSM_PREFIX=${local.ssm_prefix}
    AIDOLAB_DB_HOST=${aws_db_instance.main.address}
    AIDOLAB_DB_ADMIN_SECRET_ARN=${aws_db_instance.main.master_user_secret[0].secret_arn}
  ENV
}
