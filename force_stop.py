# force_stop.py
from detection import shutdown_detection_pipeline
import tracelog as T

T.info("[SECURE] Forced shutdown triggered via sudo.")
shutdown_detection_pipeline(skip_auth=True)
