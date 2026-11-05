"""
Edge Detection Client running on Jetson Nano.
Captures camera frames, runs YOLO inference for obstacle detection,
and sends REST API alert events to the Raspberry Pi FastAPI backend.
Includes exception handling for target tracking loss and network failures.
"""

from enum import Enum
import logging
import threading
import time
from typing import Optional
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("edge_client")


def draw_korean_text(img_bgr, text, position, font_size=20, color_bgr=(255, 255, 255)):
    """Renders Korean text on OpenCV image using PIL and system fonts."""
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    img_pil = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(img_pil)

    font_candidates = [
        "C:/Windows/Fonts/malgun.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font = None
    for path in font_candidates:
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    color_rgb = (color_bgr[2], color_bgr[1], color_bgr[0])
    draw.text(position, text, font=font, fill=color_rgb)
    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)


class TrackingState(str, Enum):
    IDLE = "IDLE"
    TRACKING = "TRACKING"
    LOST = "LOST"


class EdgeDetector:
    def __init__(
        self,
        backend_url: str = "http://localhost:8000/api/v1/alerts",
        device_id: str = "jetson_nano_01",
        confidence_threshold: float = 0.5,
        lost_grace_frames: int = 5,
        clear_frames_threshold: int = 15,
        request_timeout_sec: float = 0.5,
    ):
        self.backend_url = backend_url
        self.device_id = device_id
        self.confidence_threshold = confidence_threshold
        self.lost_grace_frames = lost_grace_frames
        self.clear_frames_threshold = clear_frames_threshold
        self.request_timeout = request_timeout_sec

        self.current_state = TrackingState.IDLE
        self.consecutive_missed_frames = 0
        self.last_detected_label: Optional[str] = None
        self.last_sent_status: Optional[str] = None

        # Ollama LLM integration settings
        self.ollama_url = "http://localhost:11434/api/generate"
        self.ollama_model = "qwen2.5-coder:7b"
        self.current_llm_guidance = ""
        self.last_llm_requested_label: Optional[str] = None
        self.is_llm_generating = False

    def request_llm_guidance(self, obstacle_label: str):
        """Asynchronously queries Ollama LLM in the background without blocking camera inference loop."""
        if self.is_llm_generating or obstacle_label == self.last_llm_requested_label:
            return

        self.last_llm_requested_label = obstacle_label
        threading.Thread(target=self._run_ollama_query, args=(obstacle_label,), daemon=True).start()

    def _run_ollama_query(self, obstacle_label: str):
        self.is_llm_generating = True
        self.current_llm_guidance = f"[{obstacle_label}] AI 분석 중..."
        prompt = (
            f"시각장애인 보행 보조 AI입니다. 전방에 '{obstacle_label}'(이)가 감지되었습니다. "
            f"보행자가 취해야 할 즉각적인 안전 행동 요령을 한국어 1문장(18자 이내)으로 간결하게 답하세요."
        )
        try:
            res = requests.post(
                self.ollama_url,
                json={"model": self.ollama_model, "prompt": prompt, "stream": False},
                timeout=12.0,
            )
            if res.status_code == 200:
                answer = res.json().get("response", "").strip()
                answer = answer.replace('"', "").replace("'", "").replace("\n", " ")
                self.current_llm_guidance = answer[:35]
                logger.info(f"Ollama LLM guidance: {self.current_llm_guidance}")
            else:
                self.current_llm_guidance = f"전방 {obstacle_label} 주의! 서행하십시오."
        except Exception as exc:
            logger.warning(f"Ollama query failed: {exc}")
            self.current_llm_guidance = f"전방 {obstacle_label} 주의! 서행하십시오."
        finally:
            self.is_llm_generating = False

    def send_alert_to_backend(
        self,
        status: str,
        obstacle_type: Optional[str] = None,
        confidence: Optional[float] = None,
        distance_level: str = "WARNING",
        detail: Optional[str] = None,
    ) -> bool:
        """Sends detection status to Raspberry Pi FastAPI backend with network exception handling."""
        payload = {
            "device_id": self.device_id,
            "timestamp": time.time(),
            "status": status,
            "obstacle_type": obstacle_type,
            "confidence": confidence,
            "distance_level": distance_level,
            "detail": detail,
        }

        try:
            response = requests.post(self.backend_url, json=payload, timeout=self.request_timeout)
            if response.status_code == 200:
                self.last_sent_status = status
                logger.info(f"Successfully sent {status} event to backend.")
                return True
            else:
                logger.warning(f"Backend returned status {response.status_code}: {response.text}")
                return False
        except requests.exceptions.Timeout:
            logger.warning(f"Backend request timed out ({self.request_timeout}s).")
            return False
        except requests.exceptions.RequestException as exc:
            # Prevent camera/inference loop from crashing when network drops
            logger.error(f"Network error communicating with backend: {exc}")
            return False

    def process_detection_result(self, hazard_detected: bool, label: Optional[str], score: float):
        """
        Manages tracking state transitions and handles tracking loss exceptions.
        Avoids abrupt oscillation when tracking flickers.
        """
        if hazard_detected:
            self.consecutive_missed_frames = 0
            self.last_detected_label = label
            if label:
                self.request_llm_guidance(label)

            if self.current_state != TrackingState.TRACKING:
                logger.info(f"Target acquired: {label} (confidence: {score:.2f})")
                self.current_state = TrackingState.TRACKING
                self.send_alert_to_backend(
                    status="ALERT",
                    obstacle_type=label,
                    confidence=score,
                    distance_level="DANGER",
                    detail="Target detected in hazard zone",
                )
        else:
            # Target not found in current frame
            if self.current_state == TrackingState.TRACKING:
                self.consecutive_missed_frames += 1

                # If missed beyond grace period, trigger TRACKING_LOST exception handling
                if self.consecutive_missed_frames >= self.lost_grace_frames:
                    logger.warning(
                        f"Target tracking LOST for {self.consecutive_missed_frames} frames. "
                        f"Target was: {self.last_detected_label}"
                    )
                    self.current_state = TrackingState.LOST
                    self.send_alert_to_backend(
                        status="TRACKING_LOST",
                        obstacle_type=self.last_detected_label,
                        distance_level="WARNING",
                        detail=f"Tracking lost after {self.lost_grace_frames} missed frames",
                    )

            elif self.current_state == TrackingState.LOST:
                self.consecutive_missed_frames += 1

                # If absent for extended period, transition to fully CLEAR
                if self.consecutive_missed_frames >= self.clear_frames_threshold:
                    logger.info("Target fully cleared from hazard zone.")
                    self.current_state = TrackingState.IDLE
                    self.send_alert_to_backend(
                        status="CLEAR",
                        obstacle_type=None,
                        detail="Obstacle zone cleared",
                    )
                    self.last_detected_label = None
                    self.current_llm_guidance = ""
                    self.last_llm_requested_label = None

    def run_inference_loop(self, camera_index: int = 0, yolo_model_path: str = "yolo11s.pt"):
        """
        Main execution loop on Jetson Nano.
        Loads YOLO model and processes camera stream.
        """
        try:
            from ultralytics import YOLO

            logger.info(f"Loading YOLO model: {yolo_model_path}")
            model = YOLO(yolo_model_path)
        except ImportError:
            logger.error("ultralytics package not installed. Run: pip install ultralytics")
            return
        except Exception as exc:
            logger.error(f"Failed to load YOLO model: {exc}")
            return

        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            logger.error(f"Failed to open camera source at index {camera_index}")
            return

        self.model_name = yolo_model_path

        logger.info("Starting detection loop. Press 'q' to stop.")
        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    logger.warning("Camera frame read failed. Retrying...")
                    time.sleep(0.1)
                    continue

                results = model(frame, verbose=False)
                hazard_detected = False
                detected_label = None
                max_score = 0.0

                # Analyze detections for hazard objects
                for result in results:
                    for box in result.boxes:
                        score = float(box.conf[0])
                        cls_id = int(box.cls[0])
                        class_name = model.names[cls_id]

                        # Detect obstacle classes (person, chair, car, obstacle)
                        if score >= self.confidence_threshold:
                            # Evaluate proximity or region-of-interest here
                            hazard_detected = True
                            detected_label = class_name
                            max_score = max(max_score, score)
                            break
                    if hazard_detected:
                        break

                self.process_detection_result(hazard_detected, detected_label, max_score)

                # Visual HUD overlay for local webcam testing
                display_frame = results[0].plot() if len(results) > 0 else frame.copy()

                hud_color = (
                    (0, 255, 0)
                    if self.current_state == TrackingState.IDLE
                    else ((0, 0, 255) if self.current_state == TrackingState.TRACKING else (0, 165, 255))
                )
                hud_text = f"MODEL: {self.model_name} | {self.current_state.value} | MISSED: {self.consecutive_missed_frames}"
                cv2.putText(display_frame, hud_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.75, hud_color, 2)

                if self.last_detected_label:
                    cv2.putText(
                        display_frame,
                        f"TARGET: {self.last_detected_label}",
                        (20, 75),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        hud_color,
                        2,
                    )

                if self.current_llm_guidance:
                    h, w = display_frame.shape[:2]
                    overlay = display_frame.copy()
                    cv2.rectangle(overlay, (15, h - 65), (w - 15, h - 15), (20, 20, 20), -1)
                    display_frame = cv2.addWeighted(overlay, 0.7, display_frame, 0.3, 0)
                    display_frame = draw_korean_text(
                        display_frame,
                        f"AI 가이드: {self.current_llm_guidance}",
                        (25, h - 55),
                        font_size=20,
                        color_bgr=(0, 255, 255),
                    )

                cv2.imshow("Walking Aid - Edge AI Detector (Press 'q' to quit)", display_frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    logger.info("Exiting on user keypress 'q'.")
                    break

        except KeyboardInterrupt:
            logger.info("Detection loop interrupted by user.")
        finally:
            cap.release()
            cv2.destroyAllWindows()
            try:
                self.send_alert_to_backend(status="CLEAR", detail="Edge client terminated")
            except Exception:
                pass


if __name__ == "__main__":
    detector = EdgeDetector(
        backend_url="http://localhost:8000/api/v1/alerts",
        lost_grace_frames=5,
        clear_frames_threshold=15,
    )
    detector.run_inference_loop()
