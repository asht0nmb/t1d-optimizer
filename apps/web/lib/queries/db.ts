import { Pool, type QueryResultRow } from "pg";

let pool: Pool | null = null;

export function getQueryPool(): Pool {
  const url = process.env.SUPABASE_DB_URL;
  if (!url) {
    throw new Error(
      "SUPABASE_DB_URL is required for aggregation queries (transaction pooler URL)",
    );
  }
  if (!pool) {
    pool = new Pool({
      connectionString: url,
      ssl: { rejectUnauthorized: false },
      max: 3,
    });
  }
  return pool;
}

export async function queryRows<T extends QueryResultRow>(
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  const res = await getQueryPool().query<T>(sql, params);
  return res.rows;
}
