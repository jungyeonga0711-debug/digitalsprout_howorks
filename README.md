# Hiworks approval helper

Google Sheets 정산 양식의 A~Q 비목별 시트를 읽어서 `하이웍스_종합` 시트를 만들고,
체크된 품의/집행 건을 하이웍스에 바로 기안하는 로컬 자동화 프로젝트입니다.

## 현재 1차 범위

- A~Q 시트의 품의/집행 영역을 읽어 표준 행으로 변환
- `하이웍스_종합` 시트 생성/갱신
- 품의/집행 업로드 체크박스 기본 선택
- 체크된 항목만 하이웍스에 바로 기안
- 기안 결과 URL/상태/오류 메시지를 종합시트에 다시 기록

하이웍스는 공식 API가 없으므로 Playwright 브라우저 자동화로 처리합니다.
실제 회사 하이웍스 화면의 CSS selector는 `config/settings.yml`에 채워야 합니다.

## 설치

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item config\settings.example.yml config\settings.yml
```

## Windows 직원용 실행

Windows에서는 `Hiworks_Start.bat` 파일을 더블클릭하면 PowerShell 창이 자동으로 열리고 컨트롤러가 실행됩니다.

- 처음 실행이면 `.venv` 생성, 패키지 설치, Playwright Chromium 설치를 자동으로 진행합니다.
- `config/settings.yml`이 없으면 예시 파일을 복사한 뒤 메모장으로 열어줍니다.
- `client_secret.json`이 없으면 프로젝트 폴더를 열어주고 파일을 넣을 때까지 기다립니다.
- 실행 후 브라우저에서 `http://127.0.0.1:8765/` 컨트롤러가 열립니다.
- PowerShell 창을 닫으면 컨트롤러도 종료됩니다.

## Google Sheets 인증

두 방법 중 하나를 사용합니다.

1. 서비스 계정 JSON 사용
   - Google Cloud에서 서비스 계정 키 JSON을 발급합니다.
   - 대상 스프레드시트를 서비스 계정 이메일에 편집 권한으로 공유합니다.
   - 환경변수를 설정합니다.

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\path\service-account.json"
```

2. 설치형 OAuth 클라이언트 사용
   - `client_secret.json`을 프로젝트 루트에 둡니다.
   - 첫 실행 시 브라우저에서 Google 계정 동의를 진행합니다.

## 하이웍스 제목 생성 및 로그 기록

```powershell
python -m hiworks_sync generate-titles --settings config\settings.yml
```

## 컨트롤 패널 실행

컨트롤 패널에서 `하이웍스 제목생성` 버튼을 누르면 A~Q 시트를 읽어 제목이
비어 있는 입력 행의 `하이웍스 제목` 칸을 채웁니다.

`하이웍스_종합` 시트는 현재 목록표가 아니라 로그 시트로 사용합니다. 제목 생성
버튼을 누른 시점마다 지난 스냅샷과 현재 A~Q 행 전체를 비교해서 생성, 수정,
삭제 이벤트를 누적 기록합니다. 비교용 스냅샷은 `하이웍스_스냅샷` 시트에
저장됩니다.

```powershell
python -m hiworks_sync control-panel --settings config\settings.yml
```

## 선택 항목 기안

```powershell
$env:HIWORKS_ID="your-id"
$env:HIWORKS_PASSWORD="your-password"
python -m hiworks_sync upload-selected --settings config\settings.yml
```

처음에는 하이웍스 셀렉터를 맞춰야 하므로 아래 명령으로 브라우저를 띄운 뒤
로그인하고 실제 기안 작성 화면까지 이동합니다. 지정한 시간이 지나면 입력칸/버튼
후보가 `artifacts/hiworks_elements.txt`에 저장됩니다.

```powershell
python -m hiworks_sync inspect-hiworks --settings config\settings.yml --url "https://YOUR_COMPANY.hiworks.com/" --wait 120
```

## 다음에 필요한 정보

- 하이웍스 로그인 URL
- 기안 작성 화면 URL 또는 메뉴 진입 방식
- 회사 기준 품의/집행 양식의 제목 규칙
- 본문에 들어가야 하는 고정 문구와 필드 순서
- 결재선이 자동 지정되는지, 직접 선택해야 하는지
- 첨부파일 자동 업로드가 필요한지
