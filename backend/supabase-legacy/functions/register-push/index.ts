import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type RegisterPushBody = {
  token?: unknown;
  subscription?: {
    endpoint?: unknown;
    keys?: {
      p256dh?: unknown;
      auth?: unknown;
    };
  };
};

const supabaseUrl = Deno.env.get("SUPABASE_URL")?.trim() ?? "";
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")?.trim() ?? "";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const jsonResponse = (body: unknown, init?: ResponseInit) => {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  for (const [key, value] of Object.entries(corsHeaders)) {
    headers.set(key, value);
  }

  return new Response(JSON.stringify(body), {
    ...init,
    headers,
  });
};

async function readBody(request: Request): Promise<RegisterPushBody | null> {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  if (request.method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, { status: 405 });
  }

  if (!supabaseUrl || !serviceRoleKey) {
    return jsonResponse({ error: "Missing Supabase service configuration" }, { status: 500 });
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey);

  const body = await readBody(request);
  const token = typeof body?.token === "string" ? body.token.trim() : "";
  const endpoint =
    typeof body?.subscription?.endpoint === "string" ? body.subscription.endpoint.trim() : "";
  const p256dh =
    typeof body?.subscription?.keys?.p256dh === "string"
      ? body.subscription.keys.p256dh.trim()
      : "";
  const auth =
    typeof body?.subscription?.keys?.auth === "string" ? body.subscription.keys.auth.trim() : "";

  if (!token || !endpoint || !p256dh || !auth) {
    return jsonResponse({ error: "Missing token or subscription fields" }, { status: 400 });
  }

  try {
    new URL(endpoint);
  } catch {
    return jsonResponse({ error: "Invalid subscription endpoint" }, { status: 400 });
  }

  const { data: parentLink, error: parentLinkError } = await supabase
    .from("parent_links")
    .select("id, school_id")
    .eq("token", token)
    .is("revoked_at", null)
    .maybeSingle();

  if (parentLinkError) {
    return jsonResponse({ error: parentLinkError.message }, { status: 500 });
  }

  if (!parentLink) {
    return jsonResponse({ error: "Invalid or revoked parent link" }, { status: 403 });
  }

  const { error: upsertError } = await supabase
    .from("push_subscriptions")
    .upsert(
      {
        school_id: parentLink.school_id,
        parent_link_id: parentLink.id,
        endpoint,
        p256dh,
        auth,
      },
      { onConflict: "parent_link_id,endpoint" },
    );

  if (upsertError) {
    return jsonResponse({ error: upsertError.message }, { status: 500 });
  }

  return jsonResponse({ ok: true });
});
