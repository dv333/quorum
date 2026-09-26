# Kubernetes or a managed container service for 5 engineers and 12 services

*Reference answer written independently with web research, sealed in git before Quorum answered.*

> Kubernetes or a managed container service (ECS, Cloud Run, Fly.io) for a 5-engineer team running 12 services?

**Bottom line: Use a managed container service: ECS on Fargate if you're on AWS, Cloud Run if you're on Google Cloud.
At 5 engineers and 12 services, Kubernetes costs more in engineering time than it saves; revisit it only when you
have a concrete need it alone meets, or a dedicated platform team.**

## Key points

- **The real cost of Kubernetes is people, not the bill.** A managed control plane is cheap (EKS and GKE charge $0.10
  per cluster-hour, about $73 a month; GKE's free tier covers one zonal or Autopilot cluster), but someone still owns
  ingress, certificates, autoscaling, secrets, IAM, observability and the upgrade treadmill. Kubernetes ships three
  minor versions a year; EKS gives about 14 months of standard support, then extended support costs $0.60 an hour
  (6×) per cluster. On a 5-person team that's often a meaningful share of one engineer.
- **ECS on Fargate (AWS):** no control-plane fee, no nodes to patch, deep AWS integration (IAM roles per task, ALB,
  CloudWatch). Its downside is wiring: task definitions, services, target groups and security groups add up, so use
  infrastructure as code (Terraform, CDK or Copilot) from day one.
- **Cloud Run (Google Cloud):** the simplest to operate: deploy a container, get HTTPS and autoscaling, including
  to zero, billed by use. Watch cold starts for latency-sensitive services, and fit for long-running or stateful
  work (it's built for request-driven services and jobs).
- **Fly.io:** fast to deploy and good for running close to users in several regions, but it's a smaller provider;
  check its SLA, support and incident history, and your compliance needs, before running core services there.
- **When Kubernetes is worth it:** you need portability across clouds or on-prem, you depend on Kubernetes-native
  tools (operators, service mesh, Argo), you run GPU or unusual scheduling workloads, you already have strong
  Kubernetes experience on the team, or you're heading to many dozens of services with a platform team. If you want
  the Kubernetes API with less work, GKE Autopilot or EKS Auto Mode are the middle ground.

## Where the evidence is uncertain

- The overhead of Kubernetes varies a lot with the team's experience; a team that already runs it well pays less.
- Cost comparisons depend on traffic shape: scale-to-zero helps spiky, low-traffic services, while steady high load
  can be cheaper on reserved or node-based capacity.

## What this means in practice

1. Pick the service that matches your cloud (ECS Fargate on AWS, Cloud Run on GCP).
2. Standardize one service template (Dockerfile, health checks, logging, IaC module) and a CI/CD pipeline for all 12.
3. Keep services portable (plain containers, config via environment, no provider-only APIs in app code), so moving to
   Kubernetes later is a migration, not a rewrite.
4. Re-evaluate if you pass roughly 30–50 services, hire a platform engineer, or hit a limit the service can't meet.

## Sources

1. AWS, Amazon EKS pricing (standard and extended support). https://aws.amazon.com/eks/pricing/
2. Google Cloud, GKE pricing and free tier. https://cloud.google.com/kubernetes-engine/pricing
3. CloudBurn, EKS pricing in 2026 and extended support costs. https://cloudburn.io/blog/amazon-eks-pricing
4. Encore, Kubernetes alternatives for small teams in 2026. https://encore.dev/articles/kubernetes-alternatives
5. CloudRPS, serverless containers vs Kubernetes: Cloud Run, Fargate and Azure Container Apps.
   https://cloudrps.com/blog/serverless-containers-vs-kubernetes-cloud-run-fargate/
