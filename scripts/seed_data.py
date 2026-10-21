"""Populate the database with sample data for demonstration purposes."""

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

DB_PATH = "chaosflow.db"
con = sqlite3.connect(DB_PATH)
cur = con.cursor()
now = datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def hash_pw(pw: str) -> str:
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


admin_id = uuid.uuid4().hex
user1_id = uuid.uuid4().hex
user2_id = uuid.uuid4().hex
pipe1_id = uuid.uuid4().hex
pipe2_id = uuid.uuid4().hex
pv1_id = uuid.uuid4().hex
pv2_v1_id = uuid.uuid4().hex
pv2_id = uuid.uuid4().hex
run1_id = uuid.uuid4().hex
run2_id = uuid.uuid4().hex
run3_id = uuid.uuid4().hex

cur.execute("DELETE FROM run_logs")
cur.execute("DELETE FROM runs")
cur.execute("DELETE FROM pipeline_versions")
cur.execute("DELETE FROM pipelines")
cur.execute("DELETE FROM refresh_tokens")
cur.execute("DELETE FROM users WHERE email != 'mohamed@test.com'")

cur.execute(
    "INSERT INTO users (id,email,password_hash,role,is_active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
    (admin_id, "admin@chaosflow.io", hash_pw("admin12345"), "admin", iso(now), iso(now)))
cur.execute(
    "INSERT INTO users (id,email,password_hash,role,is_active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
    (user2_id, "sara@test.com", hash_pw("password123"), "user", iso(now), iso(now)))

cfg1 = (
    '{"pipeline":{"id":"%s","name":"orders-etl","version":1,"schedule":null,'
    '"nodes":[{"id":"src_1","type":"source","plugin":"csv","config":{"path":"data/orders.csv"}},'
    '{"id":"tr_1","type":"transform","plugin":"filter","config":{"expression":"amount > 100"}},'
    '{"id":"snk_1","type":"sink","plugin":"csv","config":{"path":"outputs/clean.csv"}}],'
    '"edges":[{"from":"src_1","to":"tr_1"},{"from":"tr_1","to":"snk_1"}],'
    '"retry":{"max_attempts":3,"backoff_seconds":5}}}' % pipe1_id
)

cfg2_v1 = (
    '{"pipeline":{"id":"%s","name":"user-analytics","version":1,"schedule":null,'
    '"nodes":[{"id":"src_1","type":"source","plugin":"json","config":{"path":"data/users.jsonl"}},'
    '{"id":"snk_1","type":"sink","plugin":"console","config":{}}],'
    '"edges":[{"from":"src_1","to":"snk_1"}],'
    '"retry":{"max_attempts":1,"backoff_seconds":1}}}' % pipe2_id
)

cfg2 = (
    '{"pipeline":{"id":"%s","name":"user-analytics","version":2,"schedule":"0 2 * * *",'
    '"nodes":[{"id":"src_1","type":"source","plugin":"json","config":{"path":"data/users.jsonl"}},'
    '{"id":"tr_1","type":"transform","plugin":"cast","config":{"fields":{"age":"int"}}},'
    '{"id":"tr_2","type":"transform","plugin":"aggregate","config":{"group_by":["city"],"operation":"count","as":"count"}},'
    '{"id":"snk_1","type":"sink","plugin":"console","config":{}}],'
    '"edges":[{"from":"src_1","to":"tr_1"},{"from":"tr_1","to":"tr_2"},{"from":"tr_2","to":"snk_1"}],'
    '"retry":{"max_attempts":2,"backoff_seconds":3}}}' % pipe2_id
)

cur.execute(
    "INSERT INTO pipelines (id,owner_id,name,current_version,created_at,updated_at) VALUES (?,?,?,?,?,?)",
    (pipe1_id, user1_id, "orders-etl", 1, iso(now - timedelta(days=7)), iso(now)))
cur.execute(
    "INSERT INTO pipeline_versions (id,pipeline_id,version,config,created_at) VALUES (?,?,?,?,?)",
    (pv1_id, pipe1_id, 1, cfg1, iso(now - timedelta(days=7))))
cur.execute(
    "INSERT INTO pipelines (id,owner_id,name,current_version,created_at,updated_at) VALUES (?,?,?,?,?,?)",
    (pipe2_id, user2_id, "user-analytics", 2, iso(now - timedelta(days=10)), iso(now)))
cur.execute(
    "INSERT INTO pipeline_versions (id,pipeline_id,version,config,created_at) VALUES (?,?,?,?,?)",
    (pv2_v1_id, pipe2_id, 1, cfg2_v1, iso(now - timedelta(days=10))))
cur.execute(
    "INSERT INTO pipeline_versions (id,pipeline_id,version,config,created_at) VALUES (?,?,?,?,?)",
    (pv2_id, pipe2_id, 2, cfg2, iso(now - timedelta(days=2))))

metrics1 = (
    '{"src_1":{"records_in":150,"records_out":150,"duration_seconds":0.8,"error_count":0,"attempts":1},'
    '"tr_1":{"records_in":150,"records_out":89,"duration_seconds":0.3,"error_count":0,"attempts":1},'
    '"snk_1":{"records_in":89,"records_out":89,"duration_seconds":0.5,"error_count":0,"attempts":1}}'
)

cur.execute(
    "INSERT INTO runs (id,pipeline_id,pipeline_version_id,owner_id,version,status,progress,records_in,records_out,metrics,created_at,started_at,finished_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (run1_id, pipe1_id, pv1_id, user1_id, 1, "succeeded", 100.0, 150, 89, metrics1,
     iso(now - timedelta(days=3)), iso(now - timedelta(days=3)),
     iso(now - timedelta(days=3) + timedelta(seconds=4))))
cur.execute(
    "INSERT INTO runs (id,pipeline_id,pipeline_version_id,owner_id,version,status,progress,records_in,records_out,error,error_type,failed_node_id,created_at,started_at,finished_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (run2_id, pipe2_id, pv2_id, user2_id, 2, "failed", 66.0, 500, 0,
     "Node 'snk_1' failed: RuntimeError: disk full", "RuntimeError", "snk_1",
     iso(now - timedelta(days=1)), iso(now - timedelta(days=1)),
     iso(now - timedelta(days=1) + timedelta(minutes=2))))
cur.execute(
    "INSERT INTO runs (id,pipeline_id,pipeline_version_id,owner_id,version,status,progress,records_in,records_out,created_at,started_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    (run3_id, pipe1_id, pv1_id, user1_id, 1, "running", 33.0, 50, 0,
     iso(now - timedelta(minutes=5)), iso(now - timedelta(minutes=5))))

logs = [
    (run1_id, "src_1", "node_started", "Reading CSV file", 0, 0),
    (run1_id, "src_1", "batch_processed", "Processed batch", 150, 150),
    (run1_id, "src_1", "node_finished", "Read complete", 150, 150),
    (run1_id, "tr_1", "node_started", "Filtering records", 0, 0),
    (run1_id, "tr_1", "batch_processed", "Filtered batch", 150, 89),
    (run1_id, "tr_1", "node_finished", "Filter complete", 150, 89),
    (run1_id, "snk_1", "node_started", "Writing CSV", 0, 0),
    (run1_id, "snk_1", "node_finished", "Write complete", 89, 89),
    (run2_id, "src_1", "node_started", "Reading JSONL", 0, 0),
    (run2_id, "src_1", "node_finished", "Read 500 records", 500, 500),
    (run2_id, "tr_1", "node_finished", "Cast complete", 500, 500),
    (run2_id, "snk_1", "node_failed", "RuntimeError: disk full", 0, 0),
    (run3_id, "src_1", "node_started", "Reading CSV file", 0, 0),
    (run3_id, "src_1", "batch_processed", "Processed batch", 50, 50),
]
base = now - timedelta(days=3)
for i, (rid, node, event, msg, ri, ro) in enumerate(logs):
    cur.execute(
        "INSERT INTO run_logs (id,run_id,timestamp,level,node_id,event,message,records_in,records_out) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), rid, iso(base + timedelta(seconds=i)), "INFO", node, event, msg, ri, ro))

cur.execute(
    "INSERT INTO refresh_tokens (id,user_id,jti,expires_at,revoked,created_at) VALUES (?,?,?,?,0,?)",
    (str(uuid.uuid4()), user1_id, uuid.uuid4().hex, iso(now + timedelta(days=7)), iso(now - timedelta(hours=2))))
cur.execute(
    "INSERT INTO refresh_tokens (id,user_id,jti,expires_at,revoked,created_at) VALUES (?,?,?,?,0,?)",
    (str(uuid.uuid4()), user2_id, uuid.uuid4().hex, iso(now + timedelta(days=7)), iso(now - timedelta(hours=1))))

con.commit()

counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
          for t in ["users", "pipelines", "pipeline_versions", "runs", "run_logs", "refresh_tokens"]}
print("Sample data inserted:")
for table, count in counts.items():
    print(f"  {table}: {count} rows")
con.close()
