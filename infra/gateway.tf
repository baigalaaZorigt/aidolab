# New gateway inside the aidolab VPC: nginx proxy + Django dashboard + deploy worker + NAT.
# The existing gateway (default VPC) keeps serving app.aidolab.mn until var.cutover = true.

data "aws_ssm_parameter" "ubuntu_arm64" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
}

resource "aws_key_pair" "gateway" {
  key_name   = "aidolab-gw"
  public_key = file(pathexpand("~/.ssh/aidolab-gateway.pub"))
}

resource "aws_instance" "gateway" {
  ami                    = data.aws_ssm_parameter.ubuntu_arm64.value
  instance_type          = var.gateway_instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.gateway.id]
  key_name               = aws_key_pair.gateway.key_name
  iam_instance_profile   = aws_iam_instance_profile.gateway.name
  source_dest_check      = false # required for NAT

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
    tags        = { Name = "aidolab-gw", Project = "aidolab" } # DLM snapshots volumes by this tag
  }

  user_data = templatefile("${path.module}/gateway-nat.sh.tftpl", {
    vpc_cidr      = var.vpc_cidr
    private_cidrs = [for s in aws_subnet.private : s.cidr_block]
  })

  lifecycle {
    # A newer Ubuntu AMI must not replace a running gateway; rebuild deliberately instead.
    ignore_changes = [ami, user_data]
  }

  tags = { Name = "aidolab-gw" }
}

resource "aws_eip" "gateway" {
  domain   = "vpc"
  instance = aws_instance.gateway.id
  tags     = { Name = "aidolab-gw" }
}

# Student apps: <slug>.aidolab.mn. Safe before cutover — no apps exist yet.
resource "aws_route53_record" "apps_wildcard" {
  zone_id = data.aws_route53_zone.platform.zone_id
  name    = "*.${var.domain}"
  type    = "A"
  ttl     = 300
  records = [aws_eip.gateway.public_ip]
}

# Company landing page (www is covered by the wildcard above). nginx: deploy/nginx-site.conf.
resource "aws_route53_record" "site" {
  zone_id = data.aws_route53_zone.platform.zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.gateway.public_ip]
}

# Dashboard. The record was created by hand for the first gateway; cutover takes it over.
resource "aws_route53_record" "dashboard" {
  count           = var.cutover ? 1 : 0
  zone_id         = data.aws_route53_zone.platform.zone_id
  name            = "app.${var.domain}"
  type            = "A"
  ttl             = 300
  records         = [aws_eip.gateway.public_ip]
  allow_overwrite = true
}
