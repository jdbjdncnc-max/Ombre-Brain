import { createAndroidPlatform } from "./platform.android.js";
import { createBrowserPlatform } from "./platform.browser.js";

const legacyBridge = globalThis.CompanionBridge;
const capacitorBridge = globalThis.Capacitor?.Plugins?.CompanionNative;
const bridge = legacyBridge || capacitorBridge;

export const platform = bridge
  ? createAndroidPlatform(bridge, { capacitorPlugin: !legacyBridge && Boolean(capacitorBridge) })
  : createBrowserPlatform();
