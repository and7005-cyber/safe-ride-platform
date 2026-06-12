import { useEffect, useState } from "react";
import { api } from "@/lib/apiClient";

interface PushState {
  supported: boolean;
  permission: NotificationPermission | "unsupported";
  subscribed: boolean;
  subscribe: () => Promise<void>;
  unsubscribe: () => Promise<void>;
}

export function usePushNotifications(): PushState {
  const supported =
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window;

  const [permission, setPermission] = useState<NotificationPermission | "unsupported">(
    supported ? Notification.permission : "unsupported",
  );
  const [subscribed, setSubscribed] = useState(false);

  useEffect(() => {
    if (!supported) return;
    navigator.serviceWorker
      .register("/sw.js")
      .then((reg) => reg.pushManager.getSubscription())
      .then((sub) => setSubscribed(Boolean(sub)))
      .catch(() => {});
  }, [supported]);

  const subscribe = async () => {
    if (!supported) return;
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result !== "granted") return;
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true });
      const json = sub.toJSON();
      await api.post("/api/push/subscribe", {
        endpoint: sub.endpoint,
        p256dh: json.keys?.p256dh ?? null,
        auth: json.keys?.auth ?? null,
        user_agent: navigator.userAgent,
      });
      setSubscribed(true);
    } catch {
      // Without a configured VAPID key the browser may refuse to subscribe;
      // permission is still granted, but no subscription is stored.
      setSubscribed(false);
    }
  };

  const unsubscribe = async () => {
    if (!supported) return;
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await api.post("/api/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
      await sub.unsubscribe();
    }
    setSubscribed(false);
  };

  return { supported, permission, subscribed, subscribe, unsubscribe };
}
