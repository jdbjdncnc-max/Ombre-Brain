package io.github.jdbjdncncmax.ombrebrain;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;

import androidx.core.content.ContextCompat;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

final class CallAudioEngine {
    interface Listener {
        void onSpeechStart();
        void onAudioFrame(byte[] frame);
        void onSpeechEnd();
        void onBargeIn();
        void onError(String message);
    }

    static final int SAMPLE_RATE = 16000;
    private static final int FRAME_SAMPLES = 320;
    private static final int FRAME_BYTES = FRAME_SAMPLES * 2;
    private static final double SPEECH_RMS = 760.0;
    private static final int START_FRAMES = 5;
    private static final int END_SILENCE_FRAMES = 34;

    private final Context context;
    private final Listener listener;
    private final ExecutorService recordExecutor = Executors.newSingleThreadExecutor();
    private final ExecutorService playbackExecutor = Executors.newSingleThreadExecutor();
    private volatile boolean running;
    private volatile boolean muted;
    private volatile boolean playing;
    private final AtomicInteger playbackGeneration = new AtomicInteger();
    private AudioRecord recorder;
    private AudioTrack player;
    private AcousticEchoCanceler echoCanceler;
    private NoiseSuppressor noiseSuppressor;
    private AutomaticGainControl gainControl;

    CallAudioEngine(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    void start() {
        if (running) return;
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            listener.onError("麦克风权限尚未开启。");
            return;
        }
        int minimum = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        );
        int bufferSize = Math.max(minimum, FRAME_BYTES * 12);
        try {
            recorder = new AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize
            );
            enableEffects(recorder.getAudioSessionId());
            recorder.startRecording();
        } catch (Exception error) {
            releaseRecorder();
            listener.onError("无法启动麦克风：" + cleanError(error));
            return;
        }
        running = true;
        recordExecutor.execute(this::recordLoop);
    }

    void setMuted(boolean value) {
        muted = value;
    }

    boolean isMuted() {
        return muted;
    }

    boolean isPlaying() {
        return playing;
    }

    void play(byte[] pcm) {
        if (!running || pcm == null || pcm.length == 0) return;
        int generation = playbackGeneration.get();
        playbackExecutor.execute(() -> {
            if (generation != playbackGeneration.get()) return;
            AudioTrack active = ensurePlayer();
            if (active == null) return;
            playing = true;
            try {
                active.write(pcm, 0, pcm.length, AudioTrack.WRITE_BLOCKING);
            } catch (Exception error) {
                listener.onError("播放通话语音失败：" + cleanError(error));
            }
        });
    }

    void stopPlayback() {
        playbackGeneration.incrementAndGet();
        playing = false;
        AudioTrack active = player;
        if (active != null) {
            try {
                active.pause();
                active.flush();
                active.play();
            } catch (Exception ignored) {}
        }
    }

    void afterPlayback(Runnable action) {
        int generation = playbackGeneration.get();
        playbackExecutor.execute(() -> {
            if (generation != playbackGeneration.get()) return;
            playing = false;
            if (action != null) action.run();
        });
    }

    void stop() {
        running = false;
        AudioRecord activeRecorder = recorder;
        if (activeRecorder != null) {
            try {
                activeRecorder.stop();
            } catch (Exception ignored) {}
        }
        releaseRecorder();
        releasePlayer();
        recordExecutor.shutdownNow();
        playbackExecutor.shutdownNow();
    }

    private void recordLoop() {
        byte[] frame = new byte[FRAME_BYTES];
        int speechFrames = 0;
        int silenceFrames = 0;
        boolean speech = false;
        ArrayDeque<byte[]> preRoll = new ArrayDeque<>();
        while (running) {
            AudioRecord active = recorder;
            if (active == null) break;
            int read;
            try {
                read = active.read(frame, 0, frame.length, AudioRecord.READ_BLOCKING);
            } catch (Exception error) {
                if (running) listener.onError("读取麦克风失败：" + cleanError(error));
                break;
            }
            if (read <= 0 || muted) {
                speechFrames = 0;
                silenceFrames = 0;
                if (speech) {
                    speech = false;
                    listener.onSpeechEnd();
                }
                continue;
            }
            byte[] packet = read == frame.length ? Arrays.copyOf(frame, frame.length) : Arrays.copyOf(frame, read);
            boolean loud = rms(packet) >= SPEECH_RMS;
            if (!speech) {
                preRoll.addLast(packet);
                while (preRoll.size() > START_FRAMES) preRoll.removeFirst();
                speechFrames = loud ? speechFrames + 1 : 0;
                if (speechFrames >= START_FRAMES) {
                    speech = true;
                    silenceFrames = 0;
                    if (playing) {
                        stopPlayback();
                        listener.onBargeIn();
                    }
                    listener.onSpeechStart();
                    for (byte[] buffered : preRoll) listener.onAudioFrame(buffered);
                    preRoll.clear();
                }
                continue;
            }
            listener.onAudioFrame(packet);
            if (loud) {
                silenceFrames = 0;
            } else {
                silenceFrames += 1;
                if (silenceFrames >= END_SILENCE_FRAMES) {
                    speech = false;
                    speechFrames = 0;
                    silenceFrames = 0;
                    listener.onSpeechEnd();
                }
            }
        }
    }

    private AudioTrack ensurePlayer() {
        AudioTrack active = player;
        if (active != null) return active;
        int minimum = AudioTrack.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        );
        try {
            active = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build())
                .setAudioFormat(new AudioFormat.Builder()
                    .setSampleRate(SAMPLE_RATE)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build())
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(Math.max(minimum, SAMPLE_RATE * 2))
                .build();
            active.play();
            player = active;
            return active;
        } catch (Exception error) {
            listener.onError("无法启动通话扬声器：" + cleanError(error));
            return null;
        }
    }

    private void enableEffects(int sessionId) {
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                echoCanceler = AcousticEchoCanceler.create(sessionId);
                if (echoCanceler != null) echoCanceler.setEnabled(true);
            }
            if (NoiseSuppressor.isAvailable()) {
                noiseSuppressor = NoiseSuppressor.create(sessionId);
                if (noiseSuppressor != null) noiseSuppressor.setEnabled(true);
            }
            if (AutomaticGainControl.isAvailable()) {
                gainControl = AutomaticGainControl.create(sessionId);
                if (gainControl != null) gainControl.setEnabled(true);
            }
        } catch (Exception ignored) {}
    }

    private void releaseRecorder() {
        releaseEffect(echoCanceler);
        releaseEffect(noiseSuppressor);
        releaseEffect(gainControl);
        echoCanceler = null;
        noiseSuppressor = null;
        gainControl = null;
        AudioRecord active = recorder;
        recorder = null;
        if (active != null) {
            try {
                active.release();
            } catch (Exception ignored) {}
        }
    }

    private static void releaseEffect(android.media.audiofx.AudioEffect effect) {
        if (effect != null) {
            try {
                effect.release();
            } catch (Exception ignored) {}
        }
    }

    private void releasePlayer() {
        playing = false;
        AudioTrack active = player;
        player = null;
        if (active != null) {
            try {
                active.stop();
            } catch (Exception ignored) {}
            try {
                active.release();
            } catch (Exception ignored) {}
        }
    }

    private static double rms(byte[] pcm) {
        ByteBuffer buffer = ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN);
        long sum = 0;
        int count = 0;
        while (buffer.remaining() >= 2) {
            int sample = buffer.getShort();
            sum += (long) sample * sample;
            count += 1;
        }
        return count == 0 ? 0 : Math.sqrt((double) sum / count);
    }

    private static String cleanError(Throwable error) {
        String message = error == null ? "" : error.getMessage();
        return message == null || message.trim().isEmpty() ? "系统音频错误" : message.trim();
    }
}
