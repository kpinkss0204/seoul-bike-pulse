# STEP C - AWS Network Architecture

## Project
- Project: SEOUL BIKE PULSE
- Environment: dev
- AWS Region: ap-northeast-2

## VPC
- Name: bikepulse-dev-vpc
- CIDR: 10.20.0.0/16

## Public Subnets
Public A:
- Name: bikepulse-dev-public-a
- AZ: ap-northeast-2a
- CIDR: 10.20.0.0/24

Public C:
- Name: bikepulse-dev-public-c
- AZ: ap-northeast-2c
- CIDR: 10.20.1.0/24

## Private Subnets
Private A:
- Name: bikepulse-dev-private-a
- AZ: ap-northeast-2a
- CIDR: 10.20.10.0/24

Private C:
- Name: bikepulse-dev-private-c
- AZ: ap-northeast-2c
- CIDR: 10.20.11.0/24

## Internet Gateway
- Name: bikepulse-dev-igw

## NAT Gateway
- Name: bikepulse-dev-nat-a
- Mode: Zonal
- Placement: Public A
- Private A and Private C use this NAT Gateway.

## Route Tables
- Public: bikepulse-dev-rtb-public
- Private A: bikepulse-dev-rtb-private-a
- Private C: bikepulse-dev-rtb-private-c

## VPC Endpoints
- S3: bikepulse-dev-vpce-s3
- DynamoDB: bikepulse-dev-vpce-dynamodb
- Both are Gateway endpoints.

## Security Groups
- ALB: bikepulse-dev-sg-alb
- Frontend ECS: bikepulse-dev-sg-frontend
- Backend ECS: bikepulse-dev-sg-api
- MLOps ECS: bikepulse-dev-sg-mlops

## Application Load Balancer
- Name: bikepulse-dev-alb
- Scheme: internet-facing
- Listener: HTTP 80
- Temporary default action: HTTP 200 fixed response
- API route: /api/*

## Target Groups
Frontend:
- Name: bikepulse-dev-tg-frontend
- Port: HTTP 80
- Target type: ip
- Health check: /

Backend:
- Name: bikepulse-dev-tg-api
- Port: HTTP 8000
- Target type: ip
- Health check: /api/v1/health

## Security Policies
- ALB is deployed in public subnets.
- Frontend and API tasks use private subnets.
- MLOps tasks use private subnets.
- ECS tasks do not receive public IPv4 addresses.
- ECS inbound traffic is restricted by security group.
- MLOps tasks do not expose inbound ports.
- Private workloads use NAT for general internet egress.
- S3 and DynamoDB use Gateway endpoints.

## Future ECS Network Configuration
- Network mode: awsvpc
- Assign public IP: DISABLED
- Subnets: bikepulse-dev-private-a, bikepulse-dev-private-c
- Frontend container port: 80
- Backend container port: 8000

## Future HTTPS Configuration
- HTTPS listener: 443
- Certificate: AWS Certificate Manager
- HTTP listener: Redirect to HTTPS

## Cost and Availability
Lab NAT:
- One zonal NAT Gateway

Production options:
- One zonal NAT Gateway per AZ
- Regional NAT Gateway

ALB and NAT are billable resources.
