// COST_TABLE: rough USD/month estimates for greenfield resource bundles.
// Numbers are ±10%, updated via PR when cloud pricing changes materially.
// Do NOT call live cloud pricing APIs from here — see deployment wizard spec §4.

import type { Cloud } from "@/lib/deploy-wizard-state";

export const COST_TABLE = {
  aws: {
    "us-east-1": { fargateBase: 18, natGw: 32, rdsMicro: 13, alb: 18 },
    "us-west-2": { fargateBase: 19, natGw: 32, rdsMicro: 14, alb: 18 },
    "eu-west-1": { fargateBase: 20, natGw: 33, rdsMicro: 15, alb: 19 },
  },
  gcp: {
    "us-central1":  { cloudRunBase: 0, vpcConnector: 9,  cloudSqlMicro: 9 },
    "us-east1":     { cloudRunBase: 0, vpcConnector: 9,  cloudSqlMicro: 9 },
    "europe-west1": { cloudRunBase: 0, vpcConnector: 10, cloudSqlMicro: 10 },
  },
  azure: {
    "eastus":     { acaBase: 0, postgresB1ms: 13 },
    "westus2":    { acaBase: 0, postgresB1ms: 13 },
    "westeurope": { acaBase: 0, postgresB1ms: 14 },
  },
} as const;

export interface CostLine {
  resource: string;
  usd: number;
}

export interface CostEstimate {
  low: number;
  high: number;
  lines: CostLine[];
  status?: "unsupported";
}

export function estimateMonthly(
  cloud: Cloud,
  region: string,
  opts: { hasMemory: boolean; isPublic: boolean }
): CostEstimate {
  const table = (COST_TABLE as Record<string, Record<string, Record<string, number>>>)[cloud]?.[region];
  if (!table) return { low: 0, high: 0, lines: [], status: "unsupported" };

  const lines: CostLine[] = [];
  if (cloud === "aws") {
    lines.push({ resource: "ECS Fargate baseline", usd: table.fargateBase });
    lines.push({ resource: "NAT Gateway (single AZ)", usd: table.natGw });
    if (opts.hasMemory) lines.push({ resource: "RDS Postgres t3.micro", usd: table.rdsMicro });
    if (opts.isPublic) lines.push({ resource: "ALB", usd: table.alb });
  } else if (cloud === "gcp") {
    lines.push({ resource: "Cloud Run baseline (pay-per-request)", usd: table.cloudRunBase });
    if (opts.hasMemory) {
      lines.push({ resource: "VPC Connector e2-micro", usd: table.vpcConnector });
      lines.push({ resource: "Cloud SQL db-f1-micro", usd: table.cloudSqlMicro });
    }
  } else if (cloud === "azure") {
    lines.push({ resource: "ACA baseline (pay-per-request)", usd: table.acaBase });
    if (opts.hasMemory) lines.push({ resource: "Postgres Flexible B1ms", usd: table.postgresB1ms });
  }

  const sum = lines.reduce((acc, l) => acc + l.usd, 0);
  return {
    low: Math.round(sum * 0.9),
    high: Math.round(sum * 1.1) + (sum > 0 ? 1 : 0), // ensure high > low for non-zero sums
    lines,
  };
}
