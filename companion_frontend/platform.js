import { createAndroidPlatform } from "./platform.android.js";
import { createBrowserPlatform } from "./platform.browser.js";

const bridge = globalThis.CompanionBridge;

export const platform = bridge
  ? createAndroidPlatform(bridge)
  : createBrowserPlatform();
