import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type NotificationMessage = {
  id: string;
  channel: string;
  recipient_phone: string | null;
  payload: Record<string, unknown> | null;
  attempts: number;
};

type ProviderRecipient = {
  status?: unknown;
  statusCode?: unknown;
};

const supabaseUrl = Deno.env.get("SUPABASE_URL")?.trim() ?? "";
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")?.trim() ?? "";
const africasTalkingApiKey = Deno.env.get("AFRICAS_TALKING_API_KEY")?.trim() ?? "";
const africasTalkingUsername = Deno.env.get("AFRICAS_TALKING_USERNAME")?.trim() ?? "";

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

const messageBodyFor = (payload: Record<string, unknown> | null) => {
  const body = payload?.body;
  return typeof body === "string" && body.trim().length > 0
    ? body
    : "SafeRide update from your school.";
};

const isSuccessfulRecipient = (recipient: ProviderRecipient) =>
  recipient.status === "Success" ||
  recipient.statusCode === 101 ||
  recipient.statusCode === 102 ||
  recipient.statusCode === "101" ||
  recipient.statusCode === "102";

const providerSummary = (status: number, body: string) =>
  `Africa's Talking response ${status}: ${body.slice(0, 500)}`;

const unsupportedChannelError = (channel: string) =>
  channel === "push"
    ? "Push delivery is not implemented in Phase 1 sender"
    : `${channel} delivery is not implemented in Phase 1 sender`;

function parseProviderBody(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function recipientsFromProviderBody(body: unknown): ProviderRecipient[] | null {
  if (!body || typeof body !== "object") {
    return null;
  }

  const record = body as Record<string, unknown>;
  const smsMessageData = record.SMSMessageData;
  const recipients =
    smsMessageData && typeof smsMessageData === "object"
      ? (smsMessageData as Record<string, unknown>).Recipients
      : record.Recipients;

  return Array.isArray(recipients) ? (recipients as ProviderRecipient[]) : null;
}

async function sendSms(message: NotificationMessage) {
  if (message.channel !== "sms" || !message.recipient_phone) {
    throw new Error("Unsupported channel or missing recipient phone");
  }

  if (!africasTalkingApiKey || !africasTalkingUsername) {
    throw new Error("Missing Africa's Talking API credentials");
  }

  const body = new URLSearchParams({
    username: africasTalkingUsername,
    to: message.recipient_phone,
    message: messageBodyFor(message.payload),
  });

  const response = await fetch("https://api.africastalking.com/version1/messaging", {
    method: "POST",
    headers: {
      apiKey: africasTalkingApiKey,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });

  const responseText = await response.text();

  if (!response.ok) {
    throw new Error(providerSummary(response.status, responseText));
  }

  const providerBody = parseProviderBody(responseText);
  const recipients = recipientsFromProviderBody(providerBody);

  if (recipients && !recipients.every(isSuccessfulRecipient)) {
    throw new Error(providerSummary(response.status, responseText));
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

  const staleClaimCutoff = new Date(Date.now() - 5 * 60 * 1000).toISOString();
  const { error: staleRetryClaimError } = await supabase
    .from("notification_outbox")
    .update({
      status: "pending",
      last_error: "Recovered stale processing claim",
      claimed_at: null,
    })
    .eq("status", "processing")
    .lt("claimed_at", staleClaimCutoff)
    .lt("attempts", 3);

  if (staleRetryClaimError) {
    return jsonResponse({ error: staleRetryClaimError.message }, { status: 500 });
  }

  const { error: staleFinalClaimError } = await supabase
    .from("notification_outbox")
    .update({
      status: "failed",
      last_error: "Final attempt interrupted during processing",
      claimed_at: null,
    })
    .eq("status", "processing")
    .lt("claimed_at", staleClaimCutoff)
    .gte("attempts", 3);

  if (staleFinalClaimError) {
    return jsonResponse({ error: staleFinalClaimError.message }, { status: 500 });
  }

  const { data: messages, error } = await supabase
    .from("notification_outbox")
    .select("id, channel, recipient_phone, payload, attempts")
    .eq("status", "pending")
    .lt("attempts", 3)
    .order("created_at", { ascending: true })
    .limit(50);

  if (error) {
    return jsonResponse({ error: error.message }, { status: 500 });
  }

  let processed = 0;

  for (const message of (messages ?? []) as NotificationMessage[]) {
    const attempts = (message.attempts ?? 0) + 1;
    const { data: claimedMessage, error: claimError } = await supabase
      .from("notification_outbox")
      .update({
        status: "processing",
        attempts,
        claimed_at: new Date().toISOString(),
        last_error: null,
      })
      .eq("id", message.id)
      .eq("status", "pending")
      .eq("attempts", message.attempts ?? 0)
      .select("id, channel, recipient_phone, payload, attempts")
      .maybeSingle();

    if (claimError) {
      throw new Error(`Failed to claim notification ${message.id}: ${claimError.message}`);
    }

    if (!claimedMessage) {
      continue;
    }

    processed += 1;

    if (claimedMessage.channel !== "sms") {
      const { error: skippedUpdateError } = await supabase
        .from("notification_outbox")
        .update({
          status: "skipped",
          last_error: unsupportedChannelError(claimedMessage.channel),
          claimed_at: null,
        })
        .eq("id", claimedMessage.id);

      if (skippedUpdateError) {
        throw new Error(`Failed to mark notification skipped: ${skippedUpdateError.message}`);
      }

      continue;
    }

    try {
      await sendSms(claimedMessage as NotificationMessage);
    } catch (error) {
      const { error: failureUpdateError } = await supabase
        .from("notification_outbox")
        .update({
          status: attempts >= 3 ? "failed" : "pending",
          last_error: String(error),
          claimed_at: null,
        })
        .eq("id", claimedMessage.id);

      if (failureUpdateError) {
        throw new Error(`Failed to record notification failure: ${failureUpdateError.message}`);
      }

      continue;
    }

    const { error: sentError } = await supabase
      .from("notification_outbox")
      .update({
        status: "sent",
        sent_at: new Date().toISOString(),
        last_error: null,
        claimed_at: null,
      })
      .eq("id", claimedMessage.id);

    if (sentError) {
      throw new Error(`Failed to mark notification sent: ${sentError.message}`);
    }
  }

  return jsonResponse({ processed });
});
