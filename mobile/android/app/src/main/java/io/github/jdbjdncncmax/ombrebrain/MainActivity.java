package io.github.jdbjdncncmax.ombrebrain;

import android.os.Bundle;
import android.util.Log;
import android.view.ViewGroup;
import android.view.ViewParent;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.WebViewListener;

public class MainActivity extends BridgeActivity {
    private static final String TAG = "EntangleMain";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(CompanionNativePlugin.class);
        registerPlugin(CallNativePlugin.class);
        super.onCreate(savedInstanceState);
        getBridge().addWebViewListener(new WebViewListener() {
            @Override
            public boolean onRenderProcessGone(WebView webView, RenderProcessGoneDetail detail) {
                Log.e(TAG, "WebView renderer exited; recreating the app view. crashed=" + detail.didCrash());
                runOnUiThread(() -> {
                    ViewParent parent = webView.getParent();
                    if (parent instanceof ViewGroup) {
                        ((ViewGroup) parent).removeView(webView);
                    }
                    webView.destroy();
                    recreate();
                });
                return true;
            }
        });
    }
}
