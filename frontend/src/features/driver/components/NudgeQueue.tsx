import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { BellRing, Check, MapPin, Undo2, UserX, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { useDriverContext } from "@/features/driver/driverHooks";
import { postDriverAction } from "@/lib/actionEnvelope";
import { ApiError, api } from "@/lib/apiClient";
import {
  isPromptConflict,
  nudgeStore,
  useNudgeHead,
  type NudgePrompt,
} from "./nudgeStore";
import { useAttentionCue } from "./useAttentionCue";

// One non-modal card above the page content, on every driver tab (GPS plan
// U3: R13, R15, R23, R34; U9: R14). Fed by the context poll here and by the
// action responses on the pages (Arrive today); the store decides which
// prompt shows. Two cards exist: the bypassed stop (safety cue, outcome
// shortcuts, dismiss) and the custody confirm (silent, Confirm or Undo, no
// dismiss — undo is the answer that takes the tap back).
//
// Deliberately not a dialog and not a shadcn Card: nothing overlays the page
// (a tap outside changes nothing — only the card's own dismiss dismisses), and
// the outer element carries no `bg-card` class so it can never be mistaken for
// a roster row by anything that locates rows that way.

export function NudgeQueue() {
  const { data } = useDriverContext();
  const head = useNudgeHead();

  useEffect(() => {
    if (!data) return;
    if (!data.active_run) {
      nudgeStore.clear();
      return;
    }
    nudgeStore.ingest(data.pending_prompts ?? [], "context");
  }, [data]);

  if (!head || !data?.active_run) return null;
  return (
    <NudgeCard
      key={head.event_id}
      prompt={head}
      afternoon={data.active_run.type === "afternoon"}
      runId={data.active_run.id}
    />
  );
}

/** Driver-facing copy for a bypassed stop (F3): the stop, the children with
 * no record, and the two outcomes on offer. */
export function bypassedStopCopy(prompt: NudgePrompt, afternoon: boolean): {
  title: string;
  body: string;
  outcomeLabel: string;
} {
  const names = prompt.students.map((s) => s.name);
  const list =
    names.length <= 1
      ? names.join("")
      : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  const verb = names.length === 1 ? "has" : "have";
  const outcome = afternoon ? "dropped off" : "boarded";
  const stop = prompt.stop_order != null ? `Stop ${prompt.stop_order}` : "Stop";
  return {
    title: prompt.stop_name ? `${stop}: ${prompt.stop_name}` : stop,
    body: `${list} ${verb} no record. Mark ${outcome} or absent?`,
    outcomeLabel: afternoon ? "Dropped off" : "Boarded",
  };
}

/** "about 60 m", "about 1.8 km": the distance a driver can picture, never a
 * metre count that pretends to a precision the fix does not have. */
export function aboutDistance(metres: number | null | undefined): string {
  if (metres == null || !Number.isFinite(metres)) return "some way";
  if (metres < 1000) return `${Math.max(10, Math.round(metres / 10) * 10)} m`;
  return `${(metres / 1000).toFixed(1)} km`;
}

/** Driver-facing copy for a custody tap away from the stop (F2): the child,
 * the outcome tapped, the distance, and the two answers. */
export function custodyCopy(prompt: NudgePrompt, afternoon: boolean): {
  title: string;
  body: string;
} {
  const name = prompt.students[0]?.name ?? "this child";
  const outcome = afternoon ? "dropped off" : "boarded";
  const stop = prompt.stop_order != null ? `Stop ${prompt.stop_order}` : "Stop";
  return {
    title: prompt.stop_name ? `${stop}: ${prompt.stop_name}` : stop,
    body: `You marked ${name} ${outcome} about ${aboutDistance(prompt.distance_m)} from their stop. Confirm, or undo?`,
  };
}

function NudgeCard({
  prompt,
  afternoon,
  runId,
}: {
  prompt: NudgePrompt;
  afternoon: boolean;
  runId: string;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const cue = useAttentionCue();
  const [busy, setBusy] = useState(false);

  const refresh = () => qc.invalidateQueries({ queryKey: ["driver-context"] });

  // First show of this prompt on this screen: the cue (by kind — the custody
  // confirm stays silent) and the shown-at acknowledgement. A prompt the
  // server already knows was shown (reload, second device) gets neither; a
  // failed ack is not retried here — the next first-show does it.
  useEffect(() => {
    if (prompt.shown_at || !nudgeStore.markShown(prompt.event_id)) return;
    cue(prompt.kind);
    api.post(`/api/runs/driver/prompts/${prompt.event_id}/shown`).catch(() => {});
  }, [prompt.event_id, prompt.kind, prompt.shown_at, cue]);

  const respond = async (answer: string) => {
    setBusy(true);
    try {
      await api.post(`/api/runs/driver/prompts/${prompt.event_id}/respond`, { answer });
    } catch (err) {
      if (!isPromptConflict(err)) {
        // Transient only (R23): the card stays for a retry.
        toast({ title: "Could not record", description: (err as Error).message, variant: "destructive" });
        setBusy(false);
        return;
      }
      // Answered elsewhere or closed by an Arrive / End Run: same outcome —
      // drop the card and let the poll say what is left.
    }
    nudgeStore.settle(prompt.event_id);
    await refresh();
  };

  // The custody card's Undo is the reverse path (U9/R14, R35) — the same
  // route the board page's Undo calls — with the event id as a hint for the
  // prompt ledger; the server records the prompt `retracted` by membership.
  // A 409 means there is nothing left to undo (the board page got there
  // first, or a second device did): the poll will drop the card, so settle
  // it now and say nothing.
  const undo = async () => {
    setBusy(true);
    try {
      await api.post("/api/runs/driver/reverse", {
        student_id: prompt.student_id,
        event_id: prompt.event_id,
      });
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 409)) {
        toast({ title: "Cannot undo", description: (err as Error).message, variant: "destructive" });
        setBusy(false);
        return;
      }
    }
    nudgeStore.settle(prompt.event_id);
    await refresh();
  };

  // The resolution shortcuts call the normal outcome routes (R15); the server
  // records the tap on the exception's ledger by membership — the child's
  // stop on this run — and the event id travels only as a hint. The card is
  // not settled here — the refetch shrinks it to the children still without
  // a record and removes it once the last one is recorded. They are taps, so
  // they ride the action envelope like the board page's (U6).
  const resolve = async (studentId: string, outcome: "recorded" | "absent") => {
    setBusy(true);
    try {
      if (outcome === "absent") {
        await postDriverAction(
          "/api/runs/driver/absent",
          { student_id: studentId, event_id: prompt.event_id },
          { runId },
        );
      } else if (afternoon) {
        await postDriverAction(
          "/api/runs/driver/dropoff",
          { student_id: studentId, event_id: prompt.event_id },
          { runId },
        );
      } else {
        await postDriverAction(
          "/api/runs/driver/boarding",
          { student_id: studentId, on_bus: true, event_id: prompt.event_id },
          { runId },
        );
      }
    } catch (err) {
      toast({ title: "Cannot update", description: (err as Error).message, variant: "destructive" });
    } finally {
      await refresh();
      setBusy(false);
    }
  };

  if (prompt.kind === "custody-away") {
    const copy = custodyCopy(prompt, afternoon);
    return (
      <section
        role="region"
        aria-label="Driver prompt"
        aria-live="polite"
        data-testid="nudge-card"
        data-event-id={prompt.event_id}
        data-kind={prompt.kind}
        className="mb-4 rounded-lg border border-amber-500/60 bg-amber-50 p-4 shadow-sm dark:bg-amber-950/30"
      >
        <div className="flex items-start gap-2">
          <MapPin className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
          <div>
            <p className="font-semibold leading-tight">{copy.title}</p>
            <p className="mt-1 text-sm text-muted-foreground">{copy.body}</p>
          </div>
        </div>
        <div className="mt-3 flex justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            data-testid="nudge-undo"
            disabled={busy}
            onClick={undo}
          >
            <Undo2 className="h-4 w-4" /> Undo
          </Button>
          <Button
            size="sm"
            data-testid="nudge-confirm"
            disabled={busy}
            onClick={() => respond("confirmed")}
          >
            <Check className="h-4 w-4" /> Confirm
          </Button>
        </div>
      </section>
    );
  }

  const copy = bypassedStopCopy(prompt, afternoon);
  return (
    <section
      role="region"
      aria-label="Driver prompt"
      aria-live="polite"
      data-testid="nudge-card"
      data-event-id={prompt.event_id}
      data-kind={prompt.kind}
      className="mb-4 rounded-lg border border-amber-500/60 bg-amber-50 p-4 shadow-sm dark:bg-amber-950/30"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <BellRing className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
          <div>
            <p className="font-semibold leading-tight">{copy.title}</p>
            <p className="mt-1 text-sm text-muted-foreground">{copy.body}</p>
          </div>
        </div>
        <Button
          size="sm"
          variant="ghost"
          aria-label="Dismiss prompt"
          data-testid="nudge-dismiss"
          disabled={busy}
          onClick={() => respond("dismissed")}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
      <ul className="mt-3 space-y-2">
        {prompt.students.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium">{s.name}</span>
            <span className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
                data-testid={`nudge-absent-${s.id}`}
                disabled={busy}
                onClick={() => resolve(s.id, "absent")}
              >
                <UserX className="h-4 w-4" /> Absent
              </Button>
              <Button
                size="sm"
                data-testid={`nudge-outcome-${s.id}`}
                disabled={busy}
                onClick={() => resolve(s.id, "recorded")}
              >
                <Check className="h-4 w-4" /> {copy.outcomeLabel}
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
