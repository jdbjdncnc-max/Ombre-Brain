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

## 4. 当前来电范围

本版先完成“你从 APP 给 Zeta 拨出”的完整主链。拨出电话不需要 FCM。
锁屏即时来电和独处系统主动拨号需要后续接入 Firebase Cloud Messaging；到那一步才需要
Firebase 项目里的 `google-services.json`，不是去手机上额外下载一个 APP。
