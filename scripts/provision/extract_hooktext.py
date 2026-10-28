"""Classify the on-screen text across every publication that has it.

Free. `ocr_text` has held the words on 444 publications since the
OCR pass ran, and nothing has ever read them - the project measured
how much text there was and never what kind of sentence it was,
which is the only part a creator can act on.
"""
from __future__ import annotations
import json, os, sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from server.db.models import VideoFeature, VideoFeatureRun
from server.features import hooktext as ht

engine = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
S = sessionmaker(engine, expire_on_commit=False, future=True)

with S() as s:
    rows = s.execute(
        select(VideoFeature.video_id, VideoFeature.value_text)
        .where(VideoFeature.feature == "ocr_text",
               VideoFeature.value_text.is_not(None))).all()
    run = VideoFeatureRun(method_version=ht.METHOD_VERSION,
                          kind="hooktext", status="running")
    s.add(run); s.commit(); run_id = run.id

written = 0
with S() as s:
    for vid, text in rows:
        feats = ht.devices(text)
        if not feats:
            continue
        for feature, value in feats.items():
            row = s.scalar(select(VideoFeature).where(
                VideoFeature.video_id == vid,
                VideoFeature.feature == feature,
                VideoFeature.method_version == ht.METHOD_VERSION))
            if row is None:
                row = VideoFeature(video_id=vid, feature=feature,
                                   method_version=ht.METHOD_VERSION)
                s.add(row)
            row.value_num = row.value_bool = row.value_text = None
            if feature in ht.BOOLEANS:
                row.value_bool = bool(value)
            else:
                row.value_num = float(value)
            row.method = "deterministic"
            row.note = ht.describes(feature)
            row.extracted_at = datetime.now(timezone.utc)
            row.run_id = run_id
            row.status = "ok"
        written += 1
    s.commit()

with S() as s:
    r = s.get(VideoFeatureRun, run_id)
    r.status = "ok"; r.finished_at = datetime.now(timezone.utc); s.commit()
    per = s.execute(
        select(VideoFeature.feature,
               func.count(VideoFeature.id).filter(
                   VideoFeature.value_bool.is_(True)),
               func.count(VideoFeature.id))
        .where(VideoFeature.method_version == ht.METHOD_VERSION,
               VideoFeature.value_bool.is_not(None))
        .group_by(VideoFeature.feature)
        .order_by(func.count(VideoFeature.id).filter(
            VideoFeature.value_bool.is_(True)).desc())).all()
print(json.dumps({"classified": written,
                  "devices": {f: f"{y}/{n}" for f, y, n in per}}, indent=1))
