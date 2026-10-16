# What the gateway (dashboard + deploy worker) may do in AWS. Everything is scoped to aidolab resources.

resource "aws_iam_role" "gateway" {
  name = "aidolab-gw"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}

resource "aws_iam_instance_profile" "gateway" {
  name = "aidolab-gw"
  role = aws_iam_role.gateway.name
}

resource "aws_iam_role_policy_attachment" "gateway_ssm" {
  role       = aws_iam_role.gateway.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

locals {
  app_role_arns = "arn:aws:iam::${local.account}:role${local.role_path}*"
}

resource "aws_iam_role_policy" "gateway_worker" {
  name = "deploy-worker"
  role = aws_iam_role.gateway.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Builds"
        Effect   = "Allow"
        Action   = ["codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:StopBuild"]
        Resource = aws_codebuild_project.build.arn
      },
      {
        Sid      = "ReadLogs"
        Effect   = "Allow"
        Action   = ["logs:GetLogEvents", "logs:FilterLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.builds.arn}:*", "${aws_cloudwatch_log_group.apps.arn}:*"]
      },
      {
        Sid    = "EcsServices"
        Effect = "Allow"
        Action = ["ecs:CreateService", "ecs:UpdateService", "ecs:DeleteService", "ecs:DescribeServices",
        "ecs:ListTasks", "ecs:DescribeTasks"]
        Resource  = "*"
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.apps.arn } }
      },
      {
        Sid      = "EcsTaskDefinitions"
        Effect   = "Allow"
        Action   = ["ecs:RegisterTaskDefinition", "ecs:DeregisterTaskDefinition", "ecs:DescribeTaskDefinition", "ecs:TagResource"]
        Resource = "*"
      },
      {
        Sid       = "PassRoles"
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = [aws_iam_role.task_execution.arn, local.app_role_arns]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      },
      {
        Sid       = "AppRolesWithBoundary"
        Effect    = "Allow"
        Action    = ["iam:CreateRole", "iam:PutRolePolicy"]
        Resource  = local.app_role_arns
        Condition = { StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.app_boundary.arn } }
      },
      {
        Sid      = "AppRolesCleanup"
        Effect   = "Allow"
        Action   = ["iam:GetRole", "iam:DeleteRole", "iam:DeleteRolePolicy", "iam:TagRole"]
        Resource = local.app_role_arns
      },
      {
        Sid    = "ServiceDiscovery"
        Effect = "Allow"
        Action = ["servicediscovery:CreateService", "servicediscovery:DeleteService", "servicediscovery:GetService",
        "servicediscovery:ListServices", "servicediscovery:GetNamespace", "servicediscovery:TagResource"]
        Resource = "*"
      },
      {
        Sid    = "Route53ForServiceDiscovery"
        Effect = "Allow"
        Action = ["route53:CreateHealthCheck", "route53:GetHealthCheck", "route53:DeleteHealthCheck",
        "route53:UpdateHealthCheck", "route53:ChangeResourceRecordSets", "route53:GetHostedZone"]
        Resource = "*"
      },
      {
        Sid      = "AppParams"
        Effect   = "Allow"
        Action   = ["ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters", "ssm:GetParametersByPath", "ssm:AddTagsToResource"]
        Resource = ["arn:aws:ssm:${var.region}:${local.account}:parameter${local.ssm_prefix}/*"]
      },
      {
        Sid      = "DbAdminSecret"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_db_instance.main.master_user_secret[0].secret_arn
      },
      {
        Sid      = "FilesUsage"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.files.arn
      },
      {
        Sid      = "FilesCleanup"
        Effect   = "Allow"
        Action   = ["s3:DeleteObject"]
        Resource = "${aws_s3_bucket.files.arn}/*"
      },
      {
        Sid      = "Metrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricData"]
        Resource = "*"
      },
      {
        Sid      = "CertbotWildcardDns"
        Effect   = "Allow"
        Action   = ["route53:ListHostedZones", "route53:GetChange"]
        Resource = "*"
      },
      {
        Sid      = "CertbotWildcardRecords"
        Effect   = "Allow"
        Action   = ["route53:ChangeResourceRecordSets"]
        Resource = data.aws_route53_zone.platform.arn
      },
    ]
  })
}
