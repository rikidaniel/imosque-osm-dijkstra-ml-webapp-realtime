ARG FLINK_IMAGE=flink:2.1.0-scala_2.12-java17
FROM ${FLINK_IMAGE}

ARG KAFKA_CONNECTOR_VERSION=5.0.0-2.1
ADD --chmod=0644 \
  https://repo.maven.apache.org/maven2/org/apache/flink/flink-sql-connector-kafka/${KAFKA_CONNECTOR_VERSION}/flink-sql-connector-kafka-${KAFKA_CONNECTOR_VERSION}.jar \
  /opt/flink/lib/flink-sql-connector-kafka.jar

COPY traffic_aggregation.sql /opt/flink/usrlib/traffic_aggregation.sql
