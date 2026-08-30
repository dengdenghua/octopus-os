import { useState, useEffect, useCallback, useRef } from "react";

import { useLocalSettings } from "../settings";

interface NotificationOptions {
  body?: string;
  icon?: string;
  badge?: string;
  tag?: string;
  data?: unknown;
  requireInteraction?: boolean;
  silent?: boolean;
}

interface UseNotificationReturn {
  permission: NotificationPermission;
  isSupported: boolean;
  isReady: boolean;
  requestPermission: () => Promise<NotificationPermission>;
  showNotification: (title: string, options?: NotificationOptions) => boolean;
}

export function useNotification(): UseNotificationReturn {
  const [permission, setPermission] =
    useState<NotificationPermission>("default");
  const [isSupported, setIsSupported] = useState(false);
  const [isReady, setIsReady] = useState(false);

  // Implementation note.
  const lastNotificationTime = useRef<number>(0);

  useEffect(() => {
    // Check if browser supports Notification API
    if (typeof window !== "undefined" && "Notification" in window) {
      setIsSupported(true);
      setPermission(Notification.permission);
    }
    setIsReady(true);
  }, []);

  const requestPermission =
    useCallback(async (): Promise<NotificationPermission> => {
      if (!isSupported) {
        console.warn("Notification API is not supported in this browser");
        return "denied";
      }

      const result = await Notification.requestPermission();
      setPermission(result);
      return result;
    }, [isSupported]);

  const [settings] = useLocalSettings();

  const showNotification = useCallback(
    (title: string, options?: NotificationOptions) => {
      if (!isSupported) {
        console.warn("Notification API is not supported");
        return false;
      }

      if (!settings.notification.enabled) {
        console.warn("Notification is disabled");
        return false;
      }

      if (Date.now() - lastNotificationTime.current < 1000) {
        console.warn("Notification sent too soon");
        return false;
      }
      lastNotificationTime.current = Date.now();

      if (permission !== "granted") {
        console.warn("Notification permission not granted");
        return false;
      }

      try {
        const notification = new Notification(title, options);
        notification.onclick = () => {
          window.focus();
          notification.close();
        };
        notification.onerror = (error) => {
          console.error("Notification error:", error);
        };
        return true;
      } catch (e) {
        console.error("Failed to create notification:", e);
        return false;
      }
    },
    [isSupported, settings.notification.enabled, permission],
  );

  return {
    permission,
    isSupported,
    isReady,
    requestPermission,
    showNotification,
  };
}
