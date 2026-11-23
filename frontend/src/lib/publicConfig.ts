import { useQuery } from "@tanstack/react-query";
import { fetchPushConfig } from "@/lib/push";

/**
 * The global mail-availability flag (hardening DL1) from the public config
 * response. `null` while it loads; `false` when mail is off or the config
 * could not be read — the screens then point to the school office instead
 * of promising an email (R2, R8).
 */
export function useMailEnabled(): boolean | null {
  const { data } = useQuery({
    queryKey: ["public-config"],
    queryFn: fetchPushConfig,
    staleTime: Infinity,
  });
  return data ? data.mailEnabled : null;
}
