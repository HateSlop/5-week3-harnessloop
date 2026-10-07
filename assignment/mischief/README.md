1. Agent 목적

어떤 문제를 해결하는지: 평소 좋아하는 가수나 노래를 입력하면, 사용자의 음악적 취향을 분석하여 새로운 곡을 추천해주고 나만의 플레이리스트를 만들어주는 에이전트입니다.

주요 기능:

사용자 입력 기반 음악 장르 및 분위기 키워드 분석

취향 맞춤형 곡 추천 (곡명 및 추천 이유 포함)

완성된 플레이리스트를 마크다운(.md) 파일로 자동 저장


2. Tools

analyze_music_taste: 사용자가 입력한 노래/가수 정보를 바탕으로 음악의 주된 장르, 분위기, 가사 특징 등을 분석하여 키워드 형태로 추출합니다.

search_similar_songs: 추출된 취향 키워드를 바탕으로 사용자가 좋아할 만한 새로운 노래 리스트를 검색하고, 각 곡을 추천하는 이유를 함께 반환합니다.

save_playlist_to_file: 추천받은 최종 플레이리스트와 추천 이유를 정리하여 개인 과제 폴더(assignment/bohyun/my_playlist.md)에 파일로 저장합니다.


3. Agent Flow

User Input: 사용자가 좋아하는 음악 정보를 입력합니다.

Model & Tool Call 1: 모델이 입력을 분석하고 analyze_music_taste 도구를 호출하여 취향 키워드를 추출합니다.

Model & Tool Call 2: 추출된 키워드를 이용해 search_similar_songs 도구를 호출하여 맞춤형 곡을 검색합니다.

Model & Tool Call 3: 추천된 곡 목록을 save_playlist_to_file 도구를 호출하여 파일로 저장합니다.

Final Answer: 에이전트가 최종적으로 작업 완료 메시지와 결과물 내용을 사용자에게 반환합니다.


4. Harness

Execution Budget (실행 제한): 에이전트가 무한 루프에 빠지는 것을 방지하기 위해 최대 재실행 횟수를 3회(max_retries = 3)로 제한했습니다.

Error Handling (예외 처리): try-except 구문을 통해 에이전트 실행(invoke) 중 오류가 발생해도 프로그램이 강제 종료되지 않고 안전하게 메시지를 띄우고 종료되도록 구현했습니다.


5. Loop

Verification (검증): 에이전트가 생성한 최종 결과물에 5곡 이상이 포함되어 있는지(결과 텍스트 내 "5." 포함 여부) 확인합니다.

Feedback / Re-run (피드백 및 재실행): 추천된 곡이 5곡 미만일 경우, 에이전트에게 "곡 수가 부족하니 도구 파라미터를 수정하여 5곡 이상을 추천하라"는 피드백을 전달하고 재실행(Re-run)하도록 구현했습니다.


6. 실행 방법

환경 설정:

프로젝트 루트 경로에 .env 파일을 만들고 OPENAI_API_KEY=sk-본인키 를 입력합니다.

터미널에서 필수 패키지를 설치합니다: pip install langchain langchain-openai python-dotenv

에이전트 실행 방법:
터미널에서 아래 명령어를 입력하여 파이썬 파일을 실행합니다.

python assignment/bohyun/agent.py


7. 실행 예시

실행 로그:

--- [실행 1회차 시작] ---
[Tool 실행 - analyze_music_taste] 'Taylor Swift의 august를 제일 좋아해...' 분석 중...
[Tool 실행 - search_similar_songs] 키워드 기반 노래 3곡 검색 중...
[Tool 실행 - save_playlist_to_file] 플레이리스트가 assignment/bohyun/my_playlist.md에 저장되었습니다.

[검증 실패] 추천된 곡이 5곡 미만입니다. 피드백을 반영하여 재실행합니다.

--- [실행 2회차 시작] ---
[Tool 실행 - search_similar_songs] 키워드 기반 노래 5곡 검색 중...
[Tool 실행 - save_playlist_to_file] 플레이리스트가 assignment/bohyun/my_playlist.md에 저장되었습니다.

[검증 성공] 완벽한 5곡 이상의 플레이리스트가 생성되었습니다!
