import os
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate

# 1. .env 파일에서 환경변수 불러오기 (보안을 위한 처리)
load_dotenv()


# 2. Tool 구현 

@tool
def analyze_music_taste(songs: str) -> str:
    """사용자가 입력한 노래를 바탕으로 음악 장르와 분위기 키워드를 분석합니다."""
    print(f"\n[Tool 실행 - analyze_music_taste] '{songs}' 분석 중...")
    return "어쿠스틱, 감성적인 가사, 인디 팝, 서정적인 분위기"

@tool
def search_similar_songs(keywords: str, required_count: int = 3) -> str:
    """분석된 키워드를 바탕으로 어울리는 노래 리스트와 추천 이유를 반환합니다.
    required_count에 맞춰 곡 수를 조절합니다."""
    print(f"[Tool 실행 - search_similar_songs] 키워드 '{keywords}' 기반 노래 {required_count}곡 검색 중...")
    
    # 가상의 추천 결과 (피드백 루프 작동 확인을 위해 동적으로 반환)
    songs = [
        "1. Taylor Swift - willow (이유: 서정적이고 잔잔한 어쿠스틱 사운드)",
        "2. Olivia Rodrigo - enough for you (이유: 감성적인 보컬과 기타 선율)",
        "3. Conan Gray - Heather (이유: 인디 팝 특유의 우울하고 아름다운 가사)",
        "4. Gracie Abrams - I miss you, I'm sorry (이유: 감정을 자극하는 서정적인 팝)",
        "5. Phoebe Bridgers - Motion Sickness (이유: 잔잔하면서도 깊이 있는 인디 사운드)"
    ]
    return "\n".join(songs[:required_count])

@tool
def save_playlist_to_file(playlist_content: str) -> str:
    """완성된 플레이리스트를 파일로 저장합니다."""

    folder_path = "assignment/mischief.jpg"
    os.makedirs(folder_path, exist_ok=True)
    
    file_path = f"{folder_path}/my_playlist.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(playlist_content)
    
    print(f"[Tool 실행 - save_playlist_to_file] 플레이리스트가 {file_path}에 저장되었습니다.")
    return f"저장 성공: {file_path}"

tools = [analyze_music_taste, search_similar_songs, save_playlist_to_file]


# 3. Agent 구성

# 모델 초기화 
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)

prompt = ChatPromptTemplate.from_messages([
    ("system", "당신은 음악 취향을 분석하고 플레이리스트를 만들어주는 전문 AI 에이전트입니다. "
               "반드시 주어진 도구를 활용하여 취향 분석 -> 곡 검색 -> 파일 저장 순서로 작업을 수행하세요. "
               "피드백이 주어지면 피드백의 요구사항(예: 곡 수)을 반영하여 도구를 다시 호출하세요."),
    ("user", "{input}"),
    ("placeholder", "{agent_scratchpad}"),
])

agent = create_tool_calling_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)


# 4. Harness 및 Loop Engineering

def run_music_agent(user_input: str):
    """Harness와 검증 Loop가 포함된 에이전트 실행 함수"""
    
    # [Harness] Execution Budget: 최대 재실행 횟수 제한
    max_retries = 3 
    attempt = 0
    current_input = user_input
    
    while attempt < max_retries:
        attempt += 1
        print(f"\n--- [실행 {attempt}회차 시작] ---")
        
        try:
            # Agent 실행 (에이전트가 스스로 Tool을 호출)
            result = agent_executor.invoke({"input": current_input})
            final_answer = result["output"]
            
            # [Loop Engineering] Verification: 결과물 검증
            # 검증 조건: "5." 이라는 텍스트가 포함되어 있는지 (5곡 이상인지) 확인
            if "5." not in final_answer:
                print("\n[검증 실패] 추천된 곡이 5곡 미만입니다. 피드백을 반영하여 재실행합니다.[cite: 3]")
                # [Loop Engineering] Feedback: 모델에게 피드백 제공하여 재실행 유도[cite: 3]
                current_input = user_input + "\n(피드백: 이전 결과에서 곡 수가 5곡 미만이었습니다. search_similar_songs 도구의 required_count를 5로 설정하여 반드시 5곡 이상을 추천하고 다시 저장해 주세요.)"
                continue
                
            print("\n[검증 성공] 완벽한 5곡 이상의 플레이리스트가 생성되었습니다![cite: 3]")
            print("================ 최종 결과물 ================\n")
            print(final_answer)
            print("\n============================================")
            break
            
        except Exception as e:
            # [Harness] Error Handling: 에러 발생 시 프로그램 종료 방지[cite: 2]
            print(f"\n[에러 발생] 에이전트 실행 중 문제가 발생했습니다: {e}[cite: 2]")
            break
            
    # [Harness] Stop Condition: 최대 횟수 초과 시
    if attempt == max_retries and "5." not in final_answer:
        print("\n[종료] 최대 실행 횟수를 초과하여 에이전트를 종료합니다.[cite: 2]")


# 5. 실행

if __name__ == "__main__":
    # 실행 테스트
    my_favorite_songs = "Taylor Swift의 all too well를 제일 좋아해. 나만을 위한 플레이리스트를 만들어줘!"
    run_music_agent(my_favorite_songs)