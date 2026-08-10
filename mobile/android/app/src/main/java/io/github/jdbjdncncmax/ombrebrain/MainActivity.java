package io.github.jdbjdncncmax.ombrebrain;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(CompanionNativePlugin.class);
        registerPlugin(CallNativePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
