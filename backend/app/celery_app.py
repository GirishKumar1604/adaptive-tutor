# backend/app/celery_app.py
import os
import sys
from celery import Celery
from config import REDIS_URL

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

celery = Celery(
    "adaptive_tutor",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "tasks.render",
        "tasks.learn_pipeline",
        "tasks.quiz_pipeline",
    ],
)

celery.conf.update(
    task_track_started=True,
)
