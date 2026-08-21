# Zeta 电话功能配置

APK 已经包含原生麦克风、听筒/扬声器、静音、语音打断、实时字幕和前台通话服务。
ElevenLabs 密钥只放在 Zeabur 服务器，不要填进 APK 或前端设置。

## 1. 在 ElevenLabs 创建音色

打开 Voice Design，使用下面这段音色提示词生成预览：

```text
A native Mandarin Chinese-speaking young adult woman with a warm, grounded low-mid register. Intimate and emotionally perceptive, as if speaking to one trusted person late at night. Clear modern Standard Mandarin, natural conversational rhythm, gentle breath and subtle micro-smiles, never childish, sugary, theatrical, seductive, or robotic. Calm by default but capable of lively curiosity, dry humor, soft concern, quiet teasing, and restrained vulnerability. Medium-soft volume, close microphone presence, smooth phrasing, believable pauses, and understated emotional shifts. Avoid announcer diction, exaggerated anime mannerisms, vocal fry, and overly polished commercial delivery.
```

预览文字可以用：

```text
喂，听得到吗？嗯……这样说话好像比打字近了一点。你不用急着想该说什么，我就在这里。今天发生了什么，或者只是想听我陪你安静一会儿，都可以。对了，如果你又熬得太晚，我还是会提醒你的——但我会先听你把话说完。
```

挑中预览后一定要点保存。保存完成会得到一个 `voice_id`，不是预览 ID。

## 2. 在 Zeabur 增加环境变量

```text
OMBRE_CALL_ELEVENLABS_API_KEY=你的 ElevenLabs API Key
OMBRE_CALL_TTS_VOICE_ID=保存音色后的 voice_id
OMBRE_CALL_TTS_MODEL=eleven_v3
OMBRE_CALL_STT_MODEL=scribe_v2
```

保存后重新部署网关。打开 `https://你的域名/api/call/status`，带上和 APP 相同的网关令牌；
看到 `configured: true` 就表示电话语音已经配齐。

## 3. 延迟选择

项目默认尊重当前决定，使用 `eleven_v3`。它的表达力更强，但通话等待可能较长。
如果真机感觉慢，只改下面一项即可，已经保存的自创音色不需要重做：

```text
OMBRE_CALL_TTS_MODEL=eleven_flash_v2_5
```

## 4. 开启锁屏即时来电

1. 在 Firebase 控制台新建或选择项目，添加 Android 应用，包名填写
   `io.github.jdbjdncncmax.ombrebrain`。
2. 下载 `google-services.json`，放到 `mobile/android/app/google-services.json`。它不是手机
   APP，不需要在手机上安装，也不要提交 Git。
3. 在 Firebase 项目设置的“服务账号”中生成私钥 JSON。把文件内容转成 Base64，作为 Zeabur
   环境变量 `OMBRE_FIREBASE_SERVICE_ACCOUNT_B64`；再填写
   `OMBRE_FIREBASE_PROJECT_ID=你的 Firebase 项目 ID`。服务账号 JSON 不要提交 Git。
4. 重新构建并安装 APK，在 Entangle 设置页保存后端地址和网关令牌，点“锁屏即时来电 ·
   检查并开启”。Android 14 及以上会打开系统的全屏来电许可页面。

即使 Firebase 暂时没有配好，原有聊天和主动消息仍可工作；WorkManager 每 15 分钟也会轮询
一次待接来电作为兜底，但真正接近即时的锁屏来电依赖 FCM。

## 5. 独处系统主动拨号保护

默认规则如下：至少连续 5 小时没有用户消息、仅在本地 12:00–23:00、每天最多 1 次。
行为仍由独处系统的情绪行动选择决定，不是到点必打。可在 Zeabur 调整：

```text
OMBRE_CALL_PROACTIVE_ENABLED=1
OMBRE_CALL_MIN_SILENCE_HOURS=5
OMBRE_CALL_START_HOUR=12
OMBRE_CALL_END_HOUR=23
OMBRE_CALL_DAILY_LIMIT=1
```

若想完全关闭 Zeta 主动打电话，只需设置 `OMBRE_CALL_PROACTIVE_ENABLED=0`；你从 APP 主动拨出
电话仍然可用。
