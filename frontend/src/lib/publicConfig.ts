import { useQuery } from "@tanstack/react-query";
import { fetchPushConfigStrict } from "@/lib/push";

/**
 * The global mail-availability flag (hardening DL1) from the public config
 * response.
 *
 * - `true` / `false`: the server's answer, kept for the session.
 * - `null` while it loads — and while a failed read is retried (React
 *   Query's default policy). Screens treat null as "not known yet": the
 *   forms and the Resend link stay as with mail on, while the end-state
 *   notices (sign-up done, reset sent) only promise an email on `true`.
 * - `false` once every retry failed: no screen claims a mail on a guess
 *   (R2, R8). A failure is not kept: the next screen to mount, a refocused
 *   window or a restored connection reads it again, and the real answer
 *   replaces it.
 */
export function useMailEnabled(): boolean | null {
  const { data, isError } = useQuery({
    queryKey: ["public-config"],
    queryFn: fetchPushConfigStrict,
    // Successes only: a query that failed holds no data and stays stale.
    staleTime: Infinity,
  });
  if (data) return data.mailEnabled;
  return isError ? false : null;
}
