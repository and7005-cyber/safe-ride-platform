import { useEffect, useState } from "react";
import {
  disablePush,
  enablePush,
  fetchPushConfig,
  isPushActive,
  registerServiceWorker,
} from "@/lib/push";

interface PushState {
  supported: boolean;
  /** Server has at least one push channel (FCM or VAPID) configured. */
  configured: boolean;
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
  const [configured, setConfigured] = useState(true);
  const [subscribed, setSubscribed] = useState(false);

  useEffect(() => {
    if (!supported) return;
    registerServiceWorker();
    fetchPushConfig()
      .then((config) =>
        setConfigured(Boolean((config.firebase && config.firebaseVapidKey) || config.vapidPublicKey)),
      )
      .catch(() => setConfigured(false));
    isPushActive().then(setSubscribed).catch(() => {});
  }, [supported]);

  const subscribe = async () => {
    if (!supported) return;
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result !== "granted") return;
    try {
      const mode = await enablePush();
      setSubscribed(mode !== null);
    } catch {
      setSubscribed(false);
    }
  };

  const unsubscribe = async () => {
    if (!supported) return;
    await disablePush();
    setSubscribed(false);
  };

  return { supported, configured, permission, subscribed, subscribe, unsubscribe };
}
