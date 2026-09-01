import { useQuery } from "@tanstack/react-query";

import { getBackendBaseURL } from "@/core/config";

import type { Agent } from "./types";

/** A device connected to the tentacle bridge (echo-mobile phone, etc.). */
interface TentacleDevice {
  tentacle_id: string;
  type: string;
  platform: string;
  status: string;
  is_online: boolean;
  is_busy?: boolean;
}

/** Turn a connected tentacle device into an `Agent` so it shows up in the team
 * pickers + roster like any member. The synthetic `name` (`mobile_<id>`) is the
 * assignee ref the team will dispatch on. */
function deviceToAgent(d: TentacleDevice): Agent {
  const label = d.platform?.trim() || d.tentacle_id || "我的设备";
  const status = d.is_online ? (d.is_busy ? "忙碌" : "在线") : "离线";
  const icon = d.type === "desktop" ? "🖥️" : "📱";
  return {
    name: `mobile_${d.tentacle_id}`,
    display_name: label,
    description: `${d.type === "desktop" ? "本机电脑" : "本机手机"} · ${d.platform || d.type} · ${status},用无障碍直接操控设备`,
    icon,
    model: null,
    tool_groups: null,
  };
}

/**
 * Devices connected to the tentacle bridge (phones via echo-mobile) as
 * addable team members. Empty when none are connected or the bridge is offline
 * — the pickers just fall back to the built-in agents.
 */
export type MobileDeviceQueryOptions = {
  enabled?: boolean;
  refetchInterval?: number | false;
};

export function useMobileDevices(opts: MobileDeviceQueryOptions = {}): {
  mobileAgents: Agent[];
  isLoading: boolean;
} {
  const enabled = opts.enabled ?? true;
  const { data, isLoading } = useQuery({
    queryKey: ["tentacle-devices"],
    queryFn: async ({ signal }): Promise<TentacleDevice[] | null> => {
      try {
        const res = await fetch(`${getBackendBaseURL()}/api/tentacle/devices`, {
          signal,
        });
        if (!res.ok) return null;
        return (await res.json()) as TentacleDevice[];
      } catch {
        return null;
      }
    },
    enabled,
    refetchOnWindowFocus: false,
    // Devices come and go — refresh more often than the static agent list.
    staleTime: 15_000,
    refetchInterval: enabled ? (opts.refetchInterval ?? 20_000) : false,
  });
  const mobileAgents = (data ?? []).map(deviceToAgent);
  return { mobileAgents, isLoading };
}
