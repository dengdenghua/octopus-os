/**
 * Pet IPC Client - Sends agent events to the Echo Pet Sidecar via UDP
 *
 * Used by the Electron main process to broadcast agent state changes
 * to the Godot-based desktop pet runtime.
 *
 * Event types:
 *   - agent.idle
 *   - agent.thinking
 *   - agent.working   { intensity: number 0-1 }
 *   - agent.waiting_user
 *   - agent.success
 *   - agent.error
 *   - agent.emotion   { emotion: "happy"|"sad"|"curious"|"surprised"|"concerned", intensity: 0-1 }
 *   - agent.tired     { intensity: number 0-1 }
 *   - agent.presence  { online: boolean, device_id: string }
 *
 * The canonical semantic source is `runtime/pet/pet_state_map.py`; this
 * client mirrors its event types so both sides stay in sync.
 */

import dgram from "dgram";

const PET_IPC_HOST = "127.0.0.1";
const PET_IPC_PORT = 8765;

export type PetEmotion = "happy" | "sad" | "curious" | "surprised" | "concerned";

export interface PetEvent {
  type: string;
  intensity?: number;
  [key: string]: unknown;
}

class PetIPCClient {
  private socket: dgram.Socket | null = null;
  private enabled = true;
  private queue: PetEvent[] = [];
  private drainTimer: NodeJS.Timeout | null = null;

  constructor() {
    try {
      this.socket = dgram.createSocket("udp4");
      this.socket.unref();
    } catch {
      this.enabled = false;
    }
  }

  send(event: PetEvent): void {
    if (!this.enabled || !this.socket) return;
    this.queue.push(event);
    this.scheduleDrain();
  }

  private scheduleDrain(): void {
    if (this.drainTimer) return;
    this.drainTimer = setTimeout(() => this.drain(), 16);
  }

  private drain(): void {
    this.drainTimer = null;
    if (!this.socket || this.queue.length === 0) return;
    // Coalesce: send only the latest event
    const latest = this.queue[this.queue.length - 1]!;
    this.queue.length = 0;
    try {
      const payload = Buffer.from(JSON.stringify(latest) + "\n", "utf8");
      this.socket.send(payload, 0, payload.length, PET_IPC_PORT, PET_IPC_HOST, (err) => {
        if (err) {
          // Pet sidecar not running - silently ignore
        }
      });
    } catch {
      // Socket errors are non-fatal
    }
  }

  // Convenience methods for common events
  idle(): void {
    this.send({ type: "agent.idle" });
  }

  thinking(): void {
    this.send({ type: "agent.thinking" });
  }

  working(intensity = 0.5): void {
    this.send({ type: "agent.working", intensity });
  }

  waitingUser(): void {
    this.send({ type: "agent.waiting_user" });
  }

  success(): void {
    this.send({ type: "agent.success" });
  }

  error(): void {
    this.send({ type: "agent.error" });
  }

  emotion(emotion: PetEmotion, intensity = 1.0): void {
    this.send({
      type: "agent.emotion",
      emotion,
      intensity: Math.max(0, Math.min(1, intensity)),
    });
  }

  tired(intensity = 0.5): void {
    this.send({
      type: "agent.tired",
      intensity: Math.max(0, Math.min(1, intensity)),
    });
  }

  presence(online: boolean, deviceId = ""): void {
    this.send({ type: "agent.presence", online, device_id: deviceId });
  }

  destroy(): void {
    if (this.socket) {
      try {
        this.socket.close();
      } catch {
        // ignore
      }
      this.socket = null;
    }
    if (this.drainTimer) {
      clearTimeout(this.drainTimer);
      this.drainTimer = null;
    }
  }
}

export const petIPC = new PetIPCClient();
