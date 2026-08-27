/** Echo OS native hardware-control boundary tests; no host controls are touched. */
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  formatSystemControlsReadyMarker,
  getSystemControlCapabilities,
  getSystemControlState,
  parseNmcliTerseLine,
  readBatteryState,
  setAudioVolume,
  setBluetoothEnabled,
  setDisplayBrightness,
  setWifiEnabled,
} = require("./system-controls.cjs");

const TOOLS = {
  nmcli: "/usr/bin/nmcli",
  bluetoothctl: "/usr/bin/bluetoothctl",
  wpctl: "/usr/bin/wpctl",
  brightnessctl: "/usr/bin/brightnessctl",
};

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log("  ✓", name);
}

function fakeExec(responses, calls) {
  return (file, args, options, callback) => {
    calls.push({ file, args, options });
    const key = `${path.basename(file)} ${args.join(" ")}`;
    const response = responses[key] || { stdout: "" };
    callback(
      response.error ? new Error(response.error) : null,
      response.stdout || "",
      response.stderr || "",
    );
  };
}

(async () => {
  await test("non-native sessions expose no host controls", async () => {
    const capabilities = getSystemControlCapabilities({
      platform: "linux",
      nativeShell: false,
      tools: TOOLS,
      existsSync: () => true,
    });
    assert.strictEqual(capabilities.nativeShell, false);
    assert.strictEqual(capabilities.wifi, false);
    assert.strictEqual(capabilities.audio, false);
  });

  await test("nmcli escaped fields are parsed without evaluating text", async () => {
    assert.deepStrictEqual(
      parseNmcliTerseLine("wifi:connected:Echo\\:Lab\\\\5G"),
      ["wifi", "connected", "Echo:Lab\\5G"],
    );
  });

  await test("live state comes from fixed Linux control commands", async () => {
    const calls = [];
    const execFileImpl = fakeExec(
      {
        "nmcli radio wifi": { stdout: "enabled\n" },
        "nmcli -t -f TYPE,STATE,CONNECTION device status": {
          stdout: "wifi:connected:Echo\\:Home\nethernet:connected:Wired\n",
        },
        "bluetoothctl show": {
          stdout: "Controller 00:11:22:33:44:55 Echo Radio\n\tPowered: yes\n",
        },
        "wpctl get-volume @DEFAULT_AUDIO_SINK@": {
          stdout: "Volume: 0.42 [MUTED]\n",
        },
        "brightnessctl -m info": {
          stdout: "intel_backlight,backlight,937,78%,1200\n",
        },
      },
      calls,
    );
    const state = await getSystemControlState({
      platform: "linux",
      nativeShell: true,
      tools: TOOLS,
      execFileImpl,
      powerSupplyRoot: "/missing-test-power-supply",
      existsSync: (candidate) => candidate !== "/missing-test-power-supply",
    });
    assert.strictEqual(state.wifi.enabled, true);
    assert.strictEqual(state.wifi.connection, "Echo:Home");
    assert.strictEqual(state.bluetooth.enabled, true);
    assert.strictEqual(state.audio.volume, 42);
    assert.strictEqual(state.audio.muted, true);
    assert.strictEqual(state.display.brightness, 78);
    assert.strictEqual(calls.length, 5);
  });

  await test("battery state is read from sysfs without a command shell", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "echo-battery-test-"));
    const battery = path.join(root, "BAT0");
    fs.mkdirSync(battery);
    fs.writeFileSync(path.join(battery, "type"), "Battery\n");
    fs.writeFileSync(path.join(battery, "capacity"), "67\n");
    fs.writeFileSync(path.join(battery, "status"), "Discharging\n");
    assert.deepStrictEqual(readBatteryState({ powerSupplyRoot: root }), {
      available: true,
      present: true,
      percentage: 67,
      state: "Discharging",
    });
  });

  await test("mutations use only bounded fixed argument vectors", async () => {
    const calls = [];
    const execFileImpl = fakeExec(
      {
        "nmcli radio wifi off": { stdout: "" },
        "nmcli radio wifi": { stdout: "disabled\n" },
        "bluetoothctl power on": { stdout: "Changing power on succeeded\n" },
        "bluetoothctl show": { stdout: "\tPowered: yes\n" },
        "wpctl set-volume @DEFAULT_AUDIO_SINK@ 100%": { stdout: "" },
        "wpctl get-volume @DEFAULT_AUDIO_SINK@": { stdout: "Volume: 1.00\n" },
        "brightnessctl -q set 0%": { stdout: "" },
        "brightnessctl -m info": {
          stdout: "intel_backlight,backlight,0,0%,1200\n",
        },
      },
      calls,
    );
    const options = {
      platform: "linux",
      nativeShell: true,
      tools: TOOLS,
      execFileImpl,
    };
    assert.strictEqual((await setWifiEnabled(false, options)).ok, true);
    assert.strictEqual((await setBluetoothEnabled(true, options)).ok, true);
    assert.strictEqual((await setAudioVolume(120, options)).audio.volume, 100);
    assert.strictEqual(
      (await setDisplayBrightness(-10, options)).display.brightness,
      0,
    );
    assert.ok(calls.every((call) => call.options.timeout === 5_000));
    assert.deepStrictEqual(calls[0].args, ["radio", "wifi", "off"]);
    assert.deepStrictEqual(calls[4].args, [
      "set-volume",
      "@DEFAULT_AUDIO_SINK@",
      "100%",
    ]);
    assert.deepStrictEqual(calls[6].args, ["-q", "set", "0%"]);
  });

  await test("invalid renderer values are rejected before execution", async () => {
    let executed = false;
    const options = {
      platform: "linux",
      nativeShell: true,
      tools: TOOLS,
      execFileImpl: () => {
        executed = true;
      },
    };
    await assert.rejects(() => setWifiEnabled("off", options), /boolean/);
    await assert.rejects(() => setAudioVolume("0; shutdown", options), /number/);
    assert.strictEqual(executed, false);
  });

  await test("cold-boot marker reports bridge capabilities without device data", async () => {
    assert.strictEqual(
      formatSystemControlsReadyMarker({
        nativeShell: true,
        wifi: { available: true, connection: "private-network-name" },
        bluetooth: { available: true, controller: "private-controller-name" },
        audio: { available: true },
        display: { available: true },
        battery: { available: true, present: false },
      }),
      "ECHO_SYSTEM_CONTROLS_READY provider=linux-native bridge=ready wifi=ready bluetooth=ready audio=ready display=ready battery=absent",
    );
    assert.strictEqual(
      formatSystemControlsReadyMarker({ nativeShell: false }),
      null,
    );
  });

  console.log(`\nEcho OS native hardware controls: ${passed} passed`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
