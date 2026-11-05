"""
FastAPI Backend Server running on Raspberry Pi.
Receives obstacle detection events from Jetson Nano Edge device,
manages wrist vibration motor control, and checks MariaDB connectivity.
"""

from enum import Enum
import logging
import time
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.DatabaseConnect import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("back_server")

app = FastAPI(
    title="Walking Aid Backend Server",
    version="1.0.0",
    description="Backend service running on Raspberry Pi for obstacle alerts and vibration control",
)


class AlertStatus(str, Enum):
    ALERT = "ALERT"
    CLEAR = "CLEAR"
    TRACKING_LOST = "TRACKING_LOST"


class DistanceLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    DANGER = "DANGER"
    CRITICAL = "CRITICAL"


class AlertPayload(BaseModel):
    device_id: str = Field(default="jetson_edge_01", description="Identifier of the edge device")
    timestamp: float = Field(default_factory=time.time, description="Unix timestamp of detection")
    status: AlertStatus = Field(description="Detection alert status")
    obstacle_type: Optional[str] = Field(default=None, description="YOLO detected class label")
    confidence: Optional[float] = Field(default=None, description="Detection confidence score")
    distance_level: Optional[DistanceLevel] = Field(
        default=DistanceLevel.WARNING, description="Hazard distance severity level"
    )
    detail: Optional[str] = Field(default=None, description="Additional context or diagnostics")


class VibrationController:
    """Manages wrist vibration hardware state and safety auto-timeout."""

    def __init__(self, safety_timeout_seconds: float = 2.0):
        self.is_active = False
        self.current_level = DistanceLevel.INFO
        self.last_update_time = 0.0
        self.safety_timeout = safety_timeout_seconds

    def trigger_vibration(self, level: DistanceLevel):
        self.is_active = True
        self.current_level = level
        self.last_update_time = time.time()
        logger.info(f"Vibration ACTIVATED with level: {level.value}")
        # Hardware GPIO control (e.g., RPi.GPIO PWM) connects here

    def stop_vibration(self, reason: str = "normal"):
        if self.is_active:
            self.is_active = False
            logger.info(f"Vibration STOPPED (reason: {reason})")
            # Hardware GPIO control pulls pin LOW here

    def handle_tracking_lost(self):
        logger.warning("Target tracking lost on edge device. Stopping vibration for safety.")
        self.stop_vibration(reason="tracking_lost_fallback")

    def get_state(self) -> dict:
        # Check auto timeout
        if self.is_active and (time.time() - self.last_update_time > self.safety_timeout):
            self.stop_vibration(reason="safety_timeout_exceeded")
        return {
            "is_active": self.is_active,
            "current_level": self.current_level.value if self.is_active else None,
            "last_update_time": self.last_update_time,
        }


vibration_ctrl = VibrationController()


@app.get("/api/v1/health")
def health_check():
    return {
        "status": "ok",
        "service": "walking-aid-backend",
        "vibration": vibration_ctrl.get_state(),
    }


@app.get("/health/db")
def health_check_db(db: Session = Depends(get_db)):
    """MariaDB connectivity check preserved from Capstone_Design on the Pi."""
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "connected",
            "database": "MariaDB (capstone_db)",
            "result": "success",
        }
    except Exception as e:
        return {
            "status": "disconnected",
            "error": str(e),
        }


@app.post("/api/v1/alerts")
def receive_alert(payload: AlertPayload):
    """
    Receives detection alerts from Jetson Nano Edge device.
    Handles ALERT, CLEAR, and TRACKING_LOST exception states.
    """
    logger.info(
        f"Alert received: status={payload.status.value}, "
        f"obstacle={payload.obstacle_type}, level={payload.distance_level}"
    )

    if payload.status == AlertStatus.ALERT:
        vibration_ctrl.trigger_vibration(payload.distance_level or DistanceLevel.WARNING)
        return {
            "success": True,
            "action": "vibration_triggered",
            "level": payload.distance_level,
        }

    elif payload.status == AlertStatus.TRACKING_LOST:
        vibration_ctrl.handle_tracking_lost()
        return {
            "success": True,
            "action": "tracking_lost_handled",
            "message": "Vibration dampened or stopped due to lost tracking",
        }

    elif payload.status == AlertStatus.CLEAR:
        vibration_ctrl.stop_vibration(reason="obstacle_cleared")
        return {"success": True, "action": "vibration_stopped"}

    else:
        raise HTTPException(status_code=400, detail="Unknown alert status")


@app.post("/api/v1/vibration/stop")
def manual_stop_vibration():
    vibration_ctrl.stop_vibration(reason="manual_stop_request")
    return {"success": True, "action": "stopped"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
