# 배포 — 메인 PC 상시 워커

단일 메인 PC에서 워커를 상시 실행하기 위한 설정입니다.

## Linux (systemd user service, 권장)

```bash
# 1) 레포 클론 + 가상환경 + 설치
git clone <this-repo> ~/Knowledge_DB_Repository
cd ~/Knowledge_DB_Repository
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # NOTION_API_KEY / OPENAI_API_KEY 입력

# 2) 서비스 등록
mkdir -p ~/.config/systemd/user
cp deploy/systemd/knowledge-db.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now knowledge-db

# 3) 재부팅 후에도 자동 시작(선택)
loginctl enable-linger "$USER"

# 로그 확인 / 재시작 / 중지
journalctl --user -u knowledge-db -f
systemctl --user restart knowledge-db
systemctl --user stop knowledge-db
```

경로가 다르면 `knowledge-db.service`의 `WorkingDirectory`, `EnvironmentFile`,
`ExecStart`를 실제 경로로 수정하세요.

## macOS / Windows

- **macOS**: `launchd` plist를 만들거나, 간단히 `nohup knowledge-db run &` 또는
  `tmux`/`screen` 세션에서 상시 실행.
- **Windows**: 작업 스케줄러에 "로그온 시 시작 / 프로그램: `knowledge-db.exe run`"
  등록, 또는 [NSSM](https://nssm.cc/)으로 서비스화.

## cron으로 주기 배치(상시 실행 대신)

상시 프로세스 대신 주기적으로 큐를 비우고 싶다면 `run --once`를 cron에 겁니다.
리스(Lease) 덕분에 이전 실행이 겹쳐도 안전합니다.

```cron
*/5 * * * * cd $HOME/Knowledge_DB_Repository && .venv/bin/knowledge-db run --once >> ~/knowledge-db.log 2>&1
```
