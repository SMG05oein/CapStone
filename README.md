# 손목 진동 기반 보행 보조 시스템 (Walking Aid System)

라즈베리파이와 젯슨나노 엣지 디바이스를 연동하여 전방 카메라의 YOLO AI 객체 감지를 통해 보행 장애물을 실시간 인지하고, 손목 진동 피드백으로 시각장애인 및 보행 약자에게 위험을 알려주는 보행 보조 시스템입니다.

---

## 1. 시스템 아키텍처

```
+--------------------------------------------------------------------------+
|                        엣지 디바이스 (Jetson Nano)                       |
|  +--------------------+      +--------------------+      +-------------+ |
|  | 카메라 (USB / CSI) | ---> | YOLOv8 모델 추론   | ---> | 상태 머신 & | |
|  | 실시간 영상 획득   |      | 전방 장애물 감지   |      | 타게팅 관리 | |
|  +--------------------+      +--------------------+      +------+------+ |
+-----------------------------------------------------------------|--------+
                                                                  | HTTP POST /api/v1/alerts
                                                                  v
+--------------------------------------------------------------------------+
|                       백엔드 서버 (Raspberry Pi)                         |
|  +--------------------+      +--------------------+      +-------------+ |
|  | FastAPI ASGI       | ---> | 안전 워치독 제어기 | ---> | 손목 진동   | |
|  | REST API 엔드포인트|      | 상태 검증 및 타임아웃|      | 모터 구동   | |
|  +--------------------+      +--------------------+      +-------------+ |
+--------------------------------------------------------------------------+
```

---

## 2. 기술 스펙 (Technical Specifications)

### 2.1 하드웨어 사양
| 구분 | 디바이스 | 역할 및 상세 스펙 |
| :--- | :--- | :--- |
| **엣지 디바이스** | NVIDIA Jetson Nano | 128코어 NVIDIA Maxwell GPU, Quad-core ARM Cortex-A57 @ 1.43 GHz, 4GB 64-bit LPDDR4 |
| **영상 센서** | USB 웹캠 / CSI 카메라 | 720p / 1080p @ 30fps 실시간 영상 스트림 |
| **백엔드 서버** | Raspberry Pi 4B / 5 | Quad-core ARM Cortex-A72 / A76, 4GB+ RAM, Raspberry Pi OS (64-bit) |
| **진동 액추에이터** | ERM / LRA 진동 모터 | 손목 밴드형 진동 모듈 (NPN 트랜지스터/MOSFET 및 라즈베리파이 GPIO PWM 제어) |

### 2.2 소프트웨어 스택
- **엣지 (Edge)**:
  - 언어: `Python 3.8+`
  - 객체 감지 AI: `Ultralytics YOLO11 Small (yolo11s.pt)` (옵션: yolov8n.pt, yolov8s.pt 지원)
  - 비전 처리: `OpenCV (opencv-python >= 4.8.0)`
  - 네트워크 클라이언트: `Requests (requests >= 2.31.0)`
- **백엔드 (Back)**:
  - 프레임워크: `FastAPI (fastapi >= 0.100.0)`
  - 비동기 서버: `Uvicorn (uvicorn >= 0.23.0)`
  - 데이터 유효성 검증: `Pydantic v2 (pydantic >= 2.0.0)`
  - 하드웨어 핀 제어: `RPi.GPIO` / `gpiozero` (실제 보드 배포 시)

---

## 3. 통신 프로토콜 및 REST API 규격

엣지와 백엔드 서버는 로컬 무선 네트워크 환경에서 RESTful HTTP 통신을 수행합니다.

### 3.1 장애물 경보 전송 엔드포인트 (`POST /api/v1/alerts`)
엣지에서 위험 장애물 감지, 타게팅 소실, 감지 해제 상태가 변경될 때 백엔드로 이벤트를 전송합니다.

**요청 본문 (Request Body - `application/json`):**
```json
{
  "device_id": "jetson_nano_01",
  "timestamp": 1729580400.123,
  "status": "ALERT",
  "obstacle_type": "chair",
  "confidence": 0.88,
  "distance_level": "DANGER",
  "detail": "Target detected in hazard zone"
}
```

- `status` (감지 상태):
  - `ALERT`: 위험 영역 내 객체 감지. 손목 진동을 즉시 트리거합니다.
  - `TRACKING_LOST`: 장애물이 프레임에서 순간적으로 유실됨. 안전을 위해 진동을 차단합니다.
  - `CLEAR`: 전방에 장애물이 완전히 사라짐. 정상 대기 상태로 복귀합니다.
- `distance_level` (위험도 수준):
  - `INFO`, `WARNING`, `DANGER`, `CRITICAL`

**응답 본문 (Response - `200 OK`):**
```json
{
  "success": true,
  "action": "vibration_triggered",
  "level": "DANGER"
}
```

### 3.2 헬스체크 및 진동 상태 조회 (`GET /api/v1/health`)
서버 가동 상태 및 현재 진동 모터의 동작 여부를 조회합니다.

**응답 본문 (Response - `200 OK`):**
```json
{
  "status": "ok",
  "service": "walking-aid-backend",
  "vibration": {
    "is_active": true,
    "current_level": "DANGER",
    "last_update_time": 1729580400.123
  }
}
```

### 3.3 진동 수동 강제 정지 (`POST /api/v1/vibration/stop`)
긴급 상황 시 진동을 즉각 중지하는 오버라이드 엔드포인트입니다.

---

## 4. 타게팅 소실(Tracking Loss) 및 안전 워치독 메커니즘

조명 변화, 보행 중 흔들림, 일시적 가려짐으로 인해 YOLO 타게팅이 깜빡일 때 진동이 오작동하거나 멈추지 않는 현상을 방지하기 위해 이중 안전 계층을 적용했습니다.

```
          [ 장애물 획득 ]
   +------------------------------> TRACKING (ALERT 전송)
   |                                   |
   |                                   | 5프레임 이상 연속 미감지
   |                                   v
  IDLE <-------------------------- LOST (TRACKING_LOST 전송)
          15프레임 이상 연속 미감지
          (CLEAR 전송)
```

1. **엣지 유예 프레임 상태 머신**: 타게팅이 1~2프레임 가려졌다고 해서 즉시 경보를 끊지 않고, 5프레임 이상 연속 미감지 시 `TRACKING_LOST` 이벤트를 발생시켜 백엔드 진동을 안전하게 차단합니다. 15프레임 이상 부재 시 최종 `CLEAR`로 전이됩니다.
2. **백엔드 하드웨어 세이프티 워치독**: 네트워크 단절이나 엣지 기기 다운으로 인해 STOP 신호를 받지 못하는 경우를 대비해, 2초 동안 신규 경보 갱신이 없으면 백엔드 자체 타이머가 진동을 강제 차단합니다.
3. **네트워크 예외 방어**: 엣지 클라이언트의 HTTP 요청 타임아웃(0.5초) 및 소켓 통신 예외를 분리 처리하여, 네트워크가 일시 불안정해도 카메라 추론 루프가 다운되지 않고 유지됩니다.

---

## 5. 실행 및 테스트 가이드

### 로컬 PC 시뮬레이션 환경 (웹캠 기반)
실제 젯슨나노/라즈베리파이 하드웨어 없이 일반 PC 웹캠으로 통합 테스트를 수행합니다.

1. **필수 패키지 설치**:
   ```bash
   pip install fastapi uvicorn pydantic requests opencv-python ultralytics
   ```

2. **백엔드 서버 구동**:
   ```bash
   cd Back
   python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. **엣지 감지 클라이언트 구동 (별도 터미널)**:
   ```bash
   cd Edge
   python client.py
   ```
   *참고: 웹캠 화면 상단에 실시간 감지 상태(IDLE, TRACKING, LOST)가 표시되며, 종료는 화면 창에서 `q` 키를 누릅니다.*

### 실제 디바이스 배포 (Production)
- **라즈베리파이 (Back)**: `systemd` 서비스(`walking-aid-back.service`)로 등록하여 부팅 시 8000번 포트에서 자동 실행되도록 설정하고, 진동 모터 드라이버 회로를 GPIO 핀에 결합합니다.
- **젯슨나노 (Edge)**: `Edge/client.py`의 `backend_url`을 라즈베리파이의 고정 IP 주소(`http://<라즈베리파이_IP>:8000/api/v1/alerts`)로 지정한 뒤 자동 실행 데몬으로 등록합니다.

---

## 6. GitHub

원격 저장소: `https://github.com/SMG05oein/CapStone.git`

`.gitignore`가 YOLO 가중치(`*.pt`), `.env`, IDE 설정(`.idea/`, `.cursor/`), 가상환경을 제외합니다. 가중치는 클론 후 각자 `Edge/`에 두거나 Ultralytics가 자동 다운로드하게 둡니다.

```bash
git clone https://github.com/SMG05oein/CapStone.git
cd CapStone
```

### 6.1 브랜치 전략

소규모 팀 + `main` 푸시 시 라즈베리파이 배포 CI를 전제로 한 단순 전략입니다.

| 브랜치 | 용도 |
| :--- | :--- |
| `main` | 배포 기준. CI가 여기 푸시/머지 시 라즈베리파이로 배포 작업을 돌림 |
| `feature/<이름>` | 기능/수정 작업. 예: `feature/vibration-watchdog` |
| `fix/<이름>` | 급한 핫픽스. 예: `fix/alert-timeout` |

규칙:
1. 일상 작업은 `main`에서 `feature/*`를 따서 진행한다.
2. `main`에는 가급적 직접 푸시하지 않고 Pull Request로 머지한다. (실수로 깨진 코드가 바로 파이 배포로 가는 것을 줄임)
3. PR 머지(= `main` 갱신)가 배포 CI 트리거가 된다. 별도 CD 파이프라인은 두지 않는다.
4. 실험/공유용 장기 브랜치가 필요하면 `develop`을 둘 수 있으나, 인원이 적으면 `main` + `feature/*`만으로 충분하다.

로컬에서 새 작업 시작 예시:

```bash
git checkout main
git pull origin main
git checkout -b feature/my-change
# ... edit ...
git add .
git commit -m "feat: short summary"
git push -u origin feature/my-change
# GitHub에서 PR → main
```

### 6.2 main 배포 CI (GitHub Actions + Tailscale)

`main` 푸시(또는 Actions에서 `workflow_dispatch`) 시 GitHub-hosted runner가 Tailscale에 붙고, 파이 Tailscale IP로 `rsync` 배포합니다. 워크플로 파일: `.github/workflows/deploy-main.yml`

파이가 꺼져 있거나 Tailscale/SSH가 안 되면 deploy job은 실패합니다. 파이 켠 뒤 **Re-run failed jobs** 또는 **Run workflow**로 다시 돌리면 됩니다. `.env`와 `*.pt`는 배포에서 제외합니다.

#### 필요한 GitHub Secrets

저장소 → Settings → Secrets and variables → Actions 에 추가합니다.

| Secret | 내용 |
| :--- | :--- |
| `TS_OAUTH_CLIENT_ID` | Tailscale OAuth client id |
| `TS_OAUTH_SECRET` | Tailscale OAuth client secret |
| `PI_SSH_PRIVATE_KEY` | 배포용 SSH 개인키 전체 PEM |
| `PI_HOST` | 파이 Tailscale IP (예: `100.65.108.86`) |
| `PI_USER` | SSH 사용자 (예: `mrlee`) |
| `PI_DEPLOY_PATH` | (선택) 배포 경로. 기본 `/home/mrlee/CapStone` |

#### 1) Tailscale OAuth (CI용)

1. [Tailscale Admin → Trust credentials / OAuth clients](https://login.tailscale.com/admin/settings/oauth) 에서 OAuth client 생성
2. 권한에 Devices Core write, 필요 시 Keys 관련 권한 포함
3. 태그 `tag:ci` 를 쓸 수 있게 ACL에 CI 태그 허용
4. 발급된 client id/secret을 `TS_OAUTH_*` 시크릿에 넣기

ACL 예시(팀 정책에 맞게 조정):

```json
{
  "tagOwners": {
    "tag:ci": ["autogroup:admin"]
  },
  "grants": [
    {
      "src": ["tag:ci"],
      "dst": ["*"],
      "ip": ["22"]
    }
  ]
}
```

#### 2) 배포용 SSH 키

로컬에서 키 생성 후, 공개키를 파이 `~/.ssh/authorized_keys`에 넣고 개인키만 GitHub 시크릿에 넣습니다.

```bash
ssh-keygen -t ed25519 -f capstone_deploy -N ""
# 공개키 → 파이
ssh-copy-id -i capstone_deploy.pub mrlee@100.65.108.86
# 개인키 내용 전체를 PI_SSH_PRIVATE_KEY 시크릿에 붙여넣기
```

파이 쪽 `.env`는 최초 1회 수동 생성합니다. 배포가 `.env`를 덮어쓰지 않습니다.

```bash
cp /home/mrlee/CapStone/.env.example /home/mrlee/CapStone/.env
# DB_URL 등 수정
```
