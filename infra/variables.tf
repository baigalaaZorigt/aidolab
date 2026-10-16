variable "region" {
  type    = string
  default = "ap-northeast-1"
}

variable "domain" {
  type    = string
  default = "aidolab.mn"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "admin_cidrs" {
  description = "Addresses allowed to SSH into the gateway."
  type        = list(string)
}

variable "gateway_instance_type" {
  type    = string
  default = "t4g.small"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "alert_email" {
  description = "Receives CloudWatch alarms and budget warnings."
  type        = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 50
}

variable "cutover" {
  description = "When true, app.aidolab.mn points at the new gateway. Leave false until the new gateway is bootstrapped."
  type        = bool
  default     = false
}
