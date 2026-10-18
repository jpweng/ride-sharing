# A single Graviton EC2 instance running Docker Compose: Caddy (TLS + rate
# limiting) in front of the FastAPI app. The SQLite database lives on a
# separate EBS volume, so replacing the instance keeps the data.

locals {
  https_enabled = var.domain_name != ""
  registry      = split("/", aws_ecr_repository.app.repository_url)[0]
}

data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
}

resource "aws_instance" "app" {
  ami                         = data.aws_ssm_parameter.ubuntu_ami.insecure_value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.web.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name
  associate_public_ip_address = true # internet for the first boot, until the EIP takes over

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    data_volume_serial = replace(aws_ebs_volume.data.id, "-", "")
    registry           = local.registry
  })
  # The data volume is separate, so a fresh instance on bootstrap changes is safe.
  user_data_replace_on_change = true

  # Never bill for CPU-credit surplus: throttle to baseline instead.
  credit_specification {
    cpu_credits = "standard"
  }

  # IMDSv2 only; hop limit 1 keeps containers from reaching the instance role.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 10
    encrypted   = true
  }

  tags = {
    Name = var.project # CI finds the server by this tag
  }

  # The AMI parameter moves with every Ubuntu point release; don't replace
  # the server for that (unattended-upgrades patches it in place).
  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_ebs_volume" "data" {
  availability_zone = aws_subnet.public.availability_zone
  size              = var.data_volume_size_gb
  type              = "gp3"
  encrypted         = true

  tags = {
    Name   = "${var.project}-data"
    Backup = "${var.project}-daily" # picked up by the DLM policy below
  }

  # Holds the database. Remove this guard deliberately if you ever want
  # `terraform destroy` to delete it.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.app.id

  # Stop the instance before detaching, so SQLite is never cut off mid-write.
  stop_instance_before_detaching = true
}

resource "aws_eip" "app" {
  domain = "vpc"

  tags = {
    Name = var.project
  }
}

resource "aws_eip_association" "app" {
  allocation_id = aws_eip.app.id
  instance_id   = aws_instance.app.id
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.project}"
  retention_in_days = var.log_retention_days
}

# --- Instance role -------------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.project}-server"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# SSM agent: Run Command (CI deploys) and Session Manager (shell access).
resource "aws_iam_role_policy_attachment" "app_ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "app" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.app.arn, aws_ecr_repository.proxy.arn]
  }

  statement {
    sid = "ContainerLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.app.arn}:*"]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "pull-images-and-log"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.project}-server"
  role = aws_iam_role.app.name
}

# --- Daily snapshots of the data volume ---------------------------------------

data "aws_iam_policy_document" "dlm_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  name               = "${var.project}-dlm"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "data" {
  description        = "Daily snapshots of the ${var.project} data volume"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags = {
      Backup = "${var.project}-daily"
    }

    schedule {
      name      = "daily, keep 7"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["02:00"] # UTC
      }

      retain_rule {
        count = 7
      }
    }
  }
}
