package io.github.jdbjdncncmax.ombrebrain;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

import com.getcapacitor.JSObject;

final class GadgetbridgeSyncRequester {
    private static final String ACTION_SYNC =
        "nodomain.freeyourgadget.gadgetbridge.command.ACTIVITY_SYNC";
    private static final String[] PACKAGES = {
        "nodomain.freeyourgadget.gadgetbridge",
        "nodomain.freeyourgadget.gadgetbridge.nightly",
        "nodomain.freeyourgadget.gadgetbridge.banglejs"
    };

    private GadgetbridgeSyncRequester() {}

    static JSObject request(Context context) {
        JSObject result = new JSObject();
        result.put("requested", false);
        result.put("available", false);
        result.put("action", ACTION_SYNC);
        for (String packageName : PACKAGES) {
            try {
                context.getPackageManager().getPackageInfo(packageName, 0);
                result.put("available", true);
                result.put("packageName", packageName);
                Intent intent = new Intent(ACTION_SYNC);
                intent.setPackage(packageName);
                // Activity includes steps and sleep; 0x80 requests stored heart-rate samples.
                intent.putExtra("dataTypesHex", "0x00000081");
                context.sendBroadcast(intent);
                result.put("requested", true);
                result.put("note", "Gadgetbridge 需要在开发者设置中允许同步 Intent API");
                return result;
            } catch (PackageManager.NameNotFoundException ignored) {
                // Try the next official package flavor.
            } catch (RuntimeException error) {
                result.put("error", error.getClass().getSimpleName());
                return result;
            }
        }
        result.put("note", "未检测到 Gadgetbridge；仍会读取 Health Connect 已有记录");
        return result;
    }
}
