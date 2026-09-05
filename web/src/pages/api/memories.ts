import type { NextApiRequest, NextApiResponse } from "next";
import { getServerAuthSession } from "@/src/server/auth";
import { env } from "@/src/env.mjs";

type MemoryResponse = {
  results: Array<Record<string, unknown>>;
  detail?: string;
};

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse<MemoryResponse>,
) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ results: [], detail: "method_not_allowed" });
  }

  const session = await getServerAuthSession({ req, res });
  if (!session) {
    return res.status(401).json({ results: [], detail: "unauthorized" });
  }

  if (!env.MEMORY_CATALOG_API_URL || !env.MEMORY_CATALOG_TOKEN) {
    return res
      .status(503)
      .json({ results: [], detail: "memory_catalog_not_configured" });
  }

  try {
    const response = await fetch(env.MEMORY_CATALOG_API_URL, {
      headers: { "X-Memory-Internal-Token": env.MEMORY_CATALOG_TOKEN },
      cache: "no-store",
    });
    const body = (await response.json()) as MemoryResponse;
    return res.status(response.ok ? 200 : response.status).json(body);
  } catch {
    return res.status(503).json({ results: [], detail: "memory_catalog_unavailable" });
  }
}
