-- iMosque national traffic aggregation.
-- Requires Flink Kafka and JSON connector JARs matching the deployed Flink version.

CREATE TABLE location_events (
  event_id STRING,
  user_id STRING,
  session_id STRING,
  dataset_id STRING,
  region_id STRING,
  road_segment_id STRING,
  speed_kph DOUBLE,
  accuracy_m DOUBLE,
  occurred_at TIMESTAMP_LTZ(3),
  ingested_at TIMESTAMP_LTZ(3),
  WATERMARK FOR occurred_at AS occurred_at - INTERVAL '10' SECOND
) WITH (
  'connector' = 'kafka',
  'topic' = 'imosque.location.v1',
  'properties.bootstrap.servers' = 'kafka:9092',
  'properties.group.id' = 'imosque-traffic-flink-v1',
  'scan.startup.mode' = 'latest-offset',
  'format' = 'json',
  'json.timestamp-format.standard' = 'ISO-8601',
  'json.ignore-parse-errors' = 'true'
);

CREATE TABLE traffic_snapshots (
  region_id STRING,
  road_segment_id STRING,
  window_started_at TIMESTAMP_LTZ(3),
  window_ended_at TIMESTAMP_LTZ(3),
  sample_count BIGINT,
  average_speed_kph DOUBLE,
  travel_time_multiplier DOUBLE,
  PRIMARY KEY (region_id, road_segment_id) NOT ENFORCED
) WITH (
  'connector' = 'upsert-kafka',
  'topic' = 'imosque.traffic-snapshot.v1',
  'properties.bootstrap.servers' = 'kafka:9092',
  'key.format' = 'json',
  'value.format' = 'json',
  'value.json.timestamp-format.standard' = 'ISO-8601'
);

INSERT INTO traffic_snapshots
SELECT
  region_id,
  road_segment_id,
  window_start,
  window_end,
  COUNT(*) AS sample_count,
  AVG(speed_kph) AS average_speed_kph,
  CAST(
    LEAST(4.0, GREATEST(1.0, 40.0 / NULLIF(AVG(speed_kph), 0.0)))
    AS DOUBLE
  ) AS travel_time_multiplier
FROM TABLE(
  TUMBLE(TABLE location_events, DESCRIPTOR(occurred_at), INTERVAL '30' SECOND)
)
WHERE
  region_id IS NOT NULL
  AND road_segment_id IS NOT NULL
  AND speed_kph IS NOT NULL
  AND accuracy_m <= 100.0
GROUP BY region_id, road_segment_id, window_start, window_end;
