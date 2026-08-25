package io.github.jdbjdncncmax.ombrebrain;

import android.content.Context;
import android.health.connect.AggregateRecordsRequest;
import android.health.connect.AggregateRecordsResponse;
import android.health.connect.HealthConnectException;
import android.health.connect.HealthConnectManager;
import android.health.connect.ReadRecordsRequestUsingFilters;
import android.health.connect.ReadRecordsResponse;
import android.health.connect.TimeInstantRangeFilter;
import android.health.connect.datatypes.HeartRateRecord;
import android.health.connect.datatypes.SleepSessionRecord;
import android.health.connect.datatypes.StepsRecord;
import android.os.Build;
import android.os.OutcomeReceiver;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;

import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

final class HealthSnapshotReader {
    private static final int HEART_RATE_WINDOW_HOURS = 24;
    private static final int SLEEP_WINDOW_HOURS = 48;
    private static final int MAX_SERIES_POINTS = 180;

    interface Callback {
        void onSuccess(JSObject snapshot);
        void onError(Throwable error);
    }

    private HealthSnapshotReader() {}

    static boolean isSupported(Context context) {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE
            && context.getSystemService(HealthConnectManager.class) != null;
    }

    @SuppressWarnings("NewApi")
    static void read(Context context, Callback callback) {
        if (!isSupported(context)) {
            callback.onError(new IllegalStateException("这台设备暂不支持 Health Connect。"));
            return;
        }

        HealthConnectManager manager = context.getSystemService(HealthConnectManager.class);
        if (manager == null) {
            callback.onError(new IllegalStateException("找不到 Health Connect 服务。"));
            return;
        }

        Instant now = Instant.now();
        Instant stepDayStart = ZonedDateTime.now(ZoneId.systemDefault())
            .toLocalDate()
            .atStartOfDay(ZoneId.systemDefault())
            .toInstant();
        CompletableFuture<Long> stepTotal = aggregateSteps(
            context,
            manager,
            stepDayStart,
            now
        );
        CompletableFuture<List<StepsRecord>> stepRecords = readRecords(
            context,
            manager,
            StepsRecord.class,
            stepDayStart,
            now
        );
        CompletableFuture<List<HeartRateRecord>> heartRecords = readRecords(
            context,
            manager,
            HeartRateRecord.class,
            now.minus(Duration.ofHours(HEART_RATE_WINDOW_HOURS)),
            now
        );
        CompletableFuture<List<SleepSessionRecord>> sleepRecords = readRecords(
            context,
            manager,
            SleepSessionRecord.class,
            now.minus(Duration.ofHours(SLEEP_WINDOW_HOURS)),
            now
        );

        CompletableFuture.allOf(stepTotal, stepRecords, heartRecords, sleepRecords)
            .whenComplete((ignored, error) -> {
                if (error != null) {
                    callback.onError(rootCause(error));
                    return;
                }
                try {
                    callback.onSuccess(buildSnapshot(
                        now,
                        stepDayStart,
                        stepTotal.join(),
                        stepRecords.join(),
                        heartRecords.join(),
                        sleepRecords.join()
                    ));
                } catch (Throwable buildError) {
                    callback.onError(rootCause(buildError));
                }
            });
    }

    @SuppressWarnings("NewApi")
    private static CompletableFuture<Long> aggregateSteps(
        Context context,
        HealthConnectManager manager,
        Instant start,
        Instant end
    ) {
        CompletableFuture<Long> future = new CompletableFuture<>();
        AggregateRecordsRequest<Long> request = new AggregateRecordsRequest.Builder<Long>(
            new TimeInstantRangeFilter.Builder().setStartTime(start).setEndTime(end).build()
        )
            .addAggregationType(StepsRecord.STEPS_COUNT_TOTAL)
            .build();
        manager.aggregate(
            request,
            context.getMainExecutor(),
            new OutcomeReceiver<AggregateRecordsResponse<Long>, HealthConnectException>() {
                @Override
                public void onResult(AggregateRecordsResponse<Long> response) {
                    Long value = response.get(StepsRecord.STEPS_COUNT_TOTAL);
                    future.complete(value == null ? 0L : Math.max(0L, value));
                }

                @Override
                public void onError(HealthConnectException error) {
                    future.completeExceptionally(error);
                }
            }
        );
        return future;
    }

    @SuppressWarnings("NewApi")
    private static <T extends android.health.connect.datatypes.Record> CompletableFuture<List<T>> readRecords(
        Context context,
        HealthConnectManager manager,
        Class<T> recordType,
        Instant start,
        Instant end
    ) {
        CompletableFuture<List<T>> future = new CompletableFuture<>();
        ReadRecordsRequestUsingFilters<T> request = new ReadRecordsRequestUsingFilters.Builder<>(recordType)
            .setTimeRangeFilter(
                new TimeInstantRangeFilter.Builder().setStartTime(start).setEndTime(end).build()
            )
            .setAscending(false)
            .setPageSize(1000)
            .build();
        manager.readRecords(
            request,
            context.getMainExecutor(),
            new OutcomeReceiver<ReadRecordsResponse<T>, HealthConnectException>() {
                @Override
                public void onResult(ReadRecordsResponse<T> response) {
                    future.complete(new ArrayList<>(response.getRecords()));
                }

                @Override
                public void onError(HealthConnectException error) {
                    future.completeExceptionally(error);
                }
            }
        );
        return future;
    }

    @SuppressWarnings("NewApi")
    private static JSObject buildSnapshot(
        Instant capturedAt,
        Instant stepDayStart,
        long steps,
        List<StepsRecord> stepRecords,
        List<HeartRateRecord> heartRecords,
        List<SleepSessionRecord> sleepRecords
    ) {
        JSObject heartRate = buildHeartRate(heartRecords);
        JSObject stepCount = buildSteps(steps, stepRecords, stepDayStart, capturedAt);
        JSObject sleep = buildSleep(sleepRecords);

        Instant latestDataAt = newest(
            parseInstant(heartRate.optString("lastUpdatedAt", "")),
            parseInstant(stepCount.optString("lastUpdatedAt", "")),
            parseInstant(sleep.optString("lastUpdatedAt", ""))
        );

        JSObject continuous = new JSObject();
        continuous.put("heartRate", heartRate);
        JSObject discrete = new JSObject();
        discrete.put("steps", stepCount);
        discrete.put("sleep", sleep);

        JSObject snapshot = new JSObject();
        snapshot.put("schemaVersion", 1);
        snapshot.put("status", "ready");
        snapshot.put("supported", true);
        snapshot.put("source", "android_health_connect");
        snapshot.put("capturedAt", capturedAt.toString());
        if (latestDataAt != null) {
            snapshot.put("latestDataAt", latestDataAt.toString());
        }
        snapshot.put("continuous", continuous);
        snapshot.put("discrete", discrete);
        return snapshot;
    }

    @SuppressWarnings("NewApi")
    private static JSObject buildHeartRate(List<HeartRateRecord> records) {
        List<HeartRatePoint> samples = new ArrayList<>();
        for (HeartRateRecord record : records) {
            for (HeartRateRecord.HeartRateSample sample : record.getSamples()) {
                long bpm = sample.getBeatsPerMinute();
                if (bpm > 0 && bpm < 320) {
                    samples.add(new HeartRatePoint(sample.getTime(), bpm));
                }
            }
        }
        samples.sort(Comparator.comparing(point -> point.at));

        JSObject result = new JSObject();
        result.put("available", !samples.isEmpty());
        result.put("unit", "bpm");
        result.put("windowHours", HEART_RATE_WINDOW_HOURS);
        result.put("sampleCount", samples.size());
        JSArray series = new JSArray();
        if (samples.isEmpty()) {
            result.put("series", series);
            return result;
        }

        long sum = 0;
        long min = Long.MAX_VALUE;
        long max = Long.MIN_VALUE;
        for (HeartRatePoint sample : samples) {
            sum += sample.value;
            min = Math.min(min, sample.value);
            max = Math.max(max, sample.value);
        }
        HeartRatePoint latest = samples.get(samples.size() - 1);
        result.put("latestValue", latest.value);
        result.put("measurementType", "latest_exact_sample");
        result.put("averageValue", Math.round((double) sum / samples.size()));
        result.put("minValue", min);
        result.put("maxValue", max);
        result.put("lastUpdatedAt", latest.at.toString());

        for (HeartRatePoint sample : downsample(samples, MAX_SERIES_POINTS)) {
            JSObject point = new JSObject();
            point.put("at", sample.at.toString());
            point.put("value", sample.value);
            series.put(point);
        }
        result.put("series", series);
        return result;
    }

    @SuppressWarnings("NewApi")
    private static JSObject buildSteps(
        long total,
        List<StepsRecord> records,
        Instant dayStart,
        Instant capturedAt
    ) {
        Instant lastUpdatedAt = null;
        for (StepsRecord record : records) {
            lastUpdatedAt = newest(lastUpdatedAt, record.getEndTime());
        }
        JSObject result = new JSObject();
        result.put("available", !records.isEmpty() || total > 0);
        result.put("value", Math.max(0L, total));
        result.put("unit", "steps");
        result.put("windowHours", Math.max(0.0, Duration.between(dayStart, capturedAt).toMinutes() / 60.0));
        result.put("windowType", "local_calendar_day");
        result.put("startAt", dayStart.toString());
        result.put("endAt", capturedAt.toString());
        if (lastUpdatedAt != null) {
            result.put("lastUpdatedAt", lastUpdatedAt.toString());
        }
        return result;
    }

    @SuppressWarnings("NewApi")
    private static JSObject buildSleep(List<SleepSessionRecord> records) {
        SleepSessionRecord latest = records.stream()
            .max(Comparator.comparing(SleepSessionRecord::getEndTime))
            .orElse(null);
        JSObject result = new JSObject();
        result.put("available", latest != null);
        result.put("unit", "minutes");
        result.put("windowHours", SLEEP_WINDOW_HOURS);
        if (latest == null) {
            result.put("stages", new JSObject());
            return result;
        }

        long durationMinutes = Math.max(
            0L,
            Duration.between(latest.getStartTime(), latest.getEndTime()).toMinutes()
        );
        result.put("value", durationMinutes);
        result.put("sessionDurationMinutes", durationMinutes);
        result.put("startAt", latest.getStartTime().toString());
        result.put("endAt", latest.getEndTime().toString());
        result.put("lastUpdatedAt", latest.getEndTime().toString());

        Map<String, Long> stageMinutes = new HashMap<>();
        for (SleepSessionRecord.Stage stage : latest.getStages()) {
            String key = sleepStageKey(stage.getType());
            long minutes = Math.max(
                0L,
                Duration.between(stage.getStartTime(), stage.getEndTime()).toMinutes()
            );
            stageMinutes.put(key, stageMinutes.getOrDefault(key, 0L) + minutes);
        }
        JSObject stages = new JSObject();
        for (Map.Entry<String, Long> entry : stageMinutes.entrySet()) {
            stages.put(entry.getKey(), entry.getValue());
        }
        long sleepingMinutes = stageMinutes.getOrDefault("light", 0L)
            + stageMinutes.getOrDefault("deep", 0L)
            + stageMinutes.getOrDefault("rem", 0L)
            + stageMinutes.getOrDefault("sleeping", 0L);
        if (sleepingMinutes > 0L) {
            result.put("value", Math.min(durationMinutes, sleepingMinutes));
            result.put("durationBasis", "sleep_stages_excluding_awake");
        } else {
            result.put("durationBasis", "session_duration");
        }
        result.put("stages", stages);
        return result;
    }

    @SuppressWarnings("NewApi")
    private static String sleepStageKey(int type) {
        if (type == SleepSessionRecord.StageType.STAGE_TYPE_AWAKE
            || type == SleepSessionRecord.StageType.STAGE_TYPE_AWAKE_IN_BED
            || type == SleepSessionRecord.StageType.STAGE_TYPE_AWAKE_OUT_OF_BED) {
            return "awake";
        }
        if (type == SleepSessionRecord.StageType.STAGE_TYPE_SLEEPING_LIGHT) {
            return "light";
        }
        if (type == SleepSessionRecord.StageType.STAGE_TYPE_SLEEPING_DEEP) {
            return "deep";
        }
        if (type == SleepSessionRecord.StageType.STAGE_TYPE_SLEEPING_REM) {
            return "rem";
        }
        if (type == SleepSessionRecord.StageType.STAGE_TYPE_SLEEPING) {
            return "sleeping";
        }
        return "unknown";
    }

    private static List<HeartRatePoint> downsample(List<HeartRatePoint> values, int limit) {
        if (values.size() <= limit) {
            return values;
        }
        List<HeartRatePoint> result = new ArrayList<>(limit);
        for (int index = 0; index < limit; index++) {
            int sourceIndex = (int) Math.round(
                index * (values.size() - 1.0) / (limit - 1.0)
            );
            result.add(values.get(sourceIndex));
        }
        return result;
    }

    private static Instant newest(Instant... values) {
        Instant newest = null;
        for (Instant value : values) {
            if (value != null && (newest == null || value.isAfter(newest))) {
                newest = value;
            }
        }
        return newest;
    }

    private static Instant parseInstant(String value) {
        try {
            return value == null || value.isEmpty() ? null : Instant.parse(value);
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static Throwable rootCause(Throwable error) {
        Throwable current = error;
        while (current.getCause() != null && current.getCause() != current) {
            current = current.getCause();
        }
        return current;
    }

    private static final class HeartRatePoint {
        final Instant at;
        final long value;

        HeartRatePoint(Instant at, long value) {
            this.at = at;
            this.value = value;
        }
    }
}
