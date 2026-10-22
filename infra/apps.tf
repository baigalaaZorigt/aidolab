# Build (CodeBuild → ECR) and run (ECS Fargate Spot) student apps.
# Student code only ever runs here: in a throwaway CodeBuild machine and in its own Fargate task.

locals {
  account    = data.aws_caller_identity.me.account_id
  ssm_prefix = "/aidolab/apps"
  role_path  = "/aidolab/apps/"
  app_domain = "apps.aidolab.internal"
}

resource "aws_ecr_repository" "apps" {
  name                 = "aidolab-apps"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "apps" {
  repository = aws_ecr_repository.apps.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Cap stored images (tags are <slug>-<deploy number>)"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 300 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "apps" {
  name              = "/aidolab/apps"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "builds" {
  name              = "/aidolab/builds"
  retention_in_days = 7
}

# --- CodeBuild ---------------------------------------------------------------------

resource "aws_iam_role" "build" {
  name = "aidolab-build"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "codebuild.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "build" {
  name = "push-images"
  role = aws_iam_role.build.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      {
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = aws_ecr_repository.apps.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.builds.arn}:*"
      },
    ]
  })
}

resource "aws_codebuild_project" "build" {
  name          = "aidolab-build"
  description   = "Builds a student repo into an image (no VPC, push-only permissions)"
  service_role  = aws_iam_role.build.arn
  build_timeout = 25

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    type            = "ARM_CONTAINER"
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/amazonlinux-aarch64-standard:3.0"
    privileged_mode = true # docker build
    environment_variable {
      name  = "ECR_REPO"
      value = aws_ecr_repository.apps.repository_url
    }
    # REPO_URL and IMAGE_TAG are passed per build by the worker.
  }

  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/buildspec.yml")
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.builds.name
    }
  }
}

# --- ECS ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "apps" {
  name = "aidolab"
  setting {
    name  = "containerInsights"
    value = "disabled" # CPU/memory come from the free AWS/ECS service metrics
  }
}

resource "aws_ecs_cluster_capacity_providers" "apps" {
  cluster_name       = aws_ecs_cluster.apps.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }
}

# nginx on the gateway resolves <slug>.apps.aidolab.internal to the app's task IP.
resource "aws_service_discovery_private_dns_namespace" "apps" {
  name = local.app_domain
  vpc  = aws_vpc.main.id
}

# Execution role: pulls the image, writes logs, reads the app's SSM parameters.
resource "aws_iam_role" "task_execution" {
  name = "aidolab-task-execution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_ssm" {
  name = "read-app-params"
  role = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = "arn:aws:ssm:${var.region}:${local.account}:parameter${local.ssm_prefix}/*"
    }]
  })
}

# Every per-app task role must carry this boundary: at most, an app can use its bucket prefix.
resource "aws_iam_policy" "app_boundary" {
  name        = "aidolab-app-boundary"
  description = "Permission boundary for student app task roles"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.files.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.files.arn
      },
    ]
  })
}
