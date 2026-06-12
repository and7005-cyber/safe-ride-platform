import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/use-toast";
import { listenForegroundMessages } from "@/lib/push";

/** Surfaces FCM messages as toasts while the app is in the foreground. */
export function PushForegroundListener() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  useEffect(() => {
    let unsubscribe: (() => void) | undefined;
    let cancelled = false;
    listenForegroundMessages(({ title, body }) => {
      toast({ title, description: body });
      queryClient.invalidateQueries({ queryKey: ["parent-notifications"] });
      queryClient.invalidateQueries({ queryKey: ["parent-alerts"] });
    })
      .then((stop) => {
        if (cancelled) stop();
        else unsubscribe = stop;
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [toast, queryClient]);

  return null;
}
