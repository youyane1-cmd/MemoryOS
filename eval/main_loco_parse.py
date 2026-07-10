import re
import os
import requests
from utils import OpenAIClient, get_timestamp, gpt_personality_analysis, gpt_update_profile
# Initialize OpenAI client
client = OpenAIClient(
    api_key='',
    base_url='https://cn2us02.opapi.win/v1'
)

# Heat threshold
H_THRESHOLD = 5.0
REQUEST_TIMEOUT = int(os.getenv("MEMORYOS_API_TIMEOUT", "600"))

def normalize_api_base_url(url):
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url

API_BASE_URL = normalize_api_base_url(os.getenv("MEMORYOS_API_BASE_URL", "http://127.0.0.1:8000"))

def call_clear_memory(user_id):
    resp = requests.delete(
        f"{API_BASE_URL}/memory/{user_id}",
        timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()

def call_add_memory(user_id, dialogs):
    resp = requests.post(
        f"{API_BASE_URL}/memory/add",
        json={
            "user_id": user_id,
            "dialogs": dialogs,
        },
        timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()

def call_get_response(user_id, qa_pairs):
    payload_qa = [
        {
            "question": qa.get("question", ""),
            "answer": qa.get("answer", ""),
            "adversarial_answer": qa.get("adversarial_answer", ""),
            "category": qa.get("category", ""),
        }
        for qa in qa_pairs
    ]
    resp = requests.post(
        f"{API_BASE_URL}/memory/response",
        json={
            "user_id": user_id,
            "qa": payload_qa,
        },
        timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()

def update_user_profile_from_top_segment(mid_mem, long_mem, sample_id, client):
    """
    Update user profile if heat exceeds threshold and extract assistant knowledge.
    """
    if not mid_mem.heap:
        return
    
    neg_heat, sid = mid_mem.heap[0]
    mid_mem.rebuild_heap()
    current_heat = -neg_heat
    
    if current_heat >= H_THRESHOLD:
        session = mid_mem.sessions.get(sid)
        if not session:
            return
        
        un_analyzed = [p for p in session["details"] if not p.get("analyzed", False)]
        if un_analyzed:
            print(f"Updating user profile: Segment {sid} heat {current_heat:.2f} exceeds threshold, starting profile update...")
            
            old_profile = long_mem.get_raw_user_profile(sample_id)
            
            try:
                result = gpt_personality_analysis(un_analyzed, client)
            except Exception as e:
                print(f"用户画像分析失败，使用空画像结果继续: {e}")
                result = {"profile": "", "private": "None", "assistant_knowledge": ""}
            new_profile = result["profile"]
            new_private = result["private"]
            assistant_knowledge = result["assistant_knowledge"]
            
            if old_profile:
                try:
                    updated_profile = gpt_update_profile(old_profile, new_profile, client)
                except Exception as e:
                    print(f"用户画像合并失败，保留旧画像: {e}")
                    updated_profile = old_profile
            else:
                updated_profile = new_profile
                
            long_mem.update_user_profile(sample_id, updated_profile)
            
            # 修改点：拆分 new_private 并逐个存储
            if new_private and new_private != "- None":
                # 按行拆分，过滤空行和非事实行（如 "【User Data】" 或注释）
                facts = [line.strip() for line in new_private.split("\n")]
                for fact in facts:
                    long_mem.add_knowledge(fact)  # 逐条添加
            
            if assistant_knowledge and assistant_knowledge != "None":
                long_mem.add_assistant_knowledge(assistant_knowledge)
            
            for p in session["details"]:
                p["analyzed"] = True
            session["N_visit"] = 0
            session["L_interaction"] = 0
            session["R_recency"] = 1.0
            session["H_segment"] = 0.0
            session["last_visit_time"] = get_timestamp()
            mid_mem.rebuild_heap()
            mid_mem.save()
            print(f"Update complete: Segment {sid} heat has been reset.")

def generate_system_response_with_meta(query, short_mem, long_mem, retrieval_queue, long_konwledge, client, sample_id, speaker_a, speaker_b, meta_data):
    """
    Generate system response with speaker roles clearly defined.
    """
    history = short_mem.get_all()
    history_text = "\n".join([
        f"{speaker_a}: {qa.get('user_input', '')}\n{speaker_b}: {qa.get('agent_response', '')}\nTime: ({qa.get('timestamp', '')})" 
        for qa in history
    ])
    
    retrieval_text = "\n".join([
        f"【Historical Memory】 {speaker_a}: {page.get('user_input', '')}\n{speaker_b}: {page.get('agent_response', '')}\nTime:({page.get('timestamp', '')})\nConversation chain overview:({page.get('meta_info', '')})\n" 
        for page in retrieval_queue
    ])
    
    profile_obj = long_mem.get_user_profile(sample_id)
    user_profile_text = str(profile_obj.get("data", "None")) if profile_obj else "None"
    
    background = f"【User Profile】\n{user_profile_text}\n\n"
    for kn in long_konwledge:
        background += f"{kn['knowledge']}\n"
    background = re.sub(r'(?i)\buser\b', speaker_a, background)
    background= re.sub(r'(?i)\bassistant\b', speaker_b, background)
    assistant_knowledge = long_mem.get_assistant_knowledge()
    assistant_knowledge_text = "【Assistant Knowledge】\n"
    for ak in assistant_knowledge:
        assistant_knowledge_text += f"- {ak['knowledge']} ({ak['timestamp']})\n"
    #meta_data_text = f"【Conversation Meta Data】\n{json.dumps(meta_data, ensure_ascii=False, indent=2)}\n\n"
    assistant_knowledge_text = re.sub(r'\bI\b', speaker_b, assistant_knowledge_text)
    
    system_prompt = (
        f"You are role-playing as {speaker_b} in a conversation with the user is playing is  {speaker_a}. "
        f"Here are some of your character traits and knowledge:\n{assistant_knowledge_text}\n"
        f"Any content referring to 'User' in the prompt refers to {speaker_a}'s content, and any content referring to 'AI'or 'assiant' refers to {speaker_b}'s content."
        f"Your task is to answer questions about {speaker_a} or {speaker_b} in an extremely concise manner.\n"
        f"When the question is: \"What did the charity race raise awareness for?\", you should not answer in the form of: \"The charity race raised awareness for mental health.\" Instead, it should be: \"mental health\", as this is more concise."
    )
    
    user_prompt = (
        f"<CONTEXT>\n"
        f"Recent conversation between {speaker_a} and {speaker_b}:\n"
        f"{history_text}\n\n"
        f"<MEMORY>\n"
        f"Relevant past conversations:\n"
        f"{retrieval_text}\n\n"
        f"<CHARACTER TRAITS>\n"
        f"Characteristics of {speaker_a}:\n"
        f"{background}\n\n"
        f"the question is: {query}\n"
        f"Your task is to answer questions about {speaker_a} or {speaker_b} in an extremely concise manner.\n"
        f"Please only provide the content of the answer, without including 'answer:'\n"
        f"For questions that require answering a date or time, strictly follow the format \"15 July 2023\" and provide a specific date whenever possible. For example, if you need to answer \"last year,\" give the specific year of last year rather than just saying \"last year.\" Only provide one year, date, or time, without any extra responses.\n"
        f"If the question is about the duration, answer in the form of several years, months, or days.\n"
        f"Generate answers primarily composed of concrete entities, such as Mentoring program, school speech, etc"
    )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    try:
        response = client.chat_completion(model="gpt-4o-mini", messages=messages, temperature=0.7, max_tokens=2000)
    except Exception as e:
        print(f"生成最终回答失败，返回空答案: {e}")
        response = ""
    return response, system_prompt, user_prompt

def process_conversation(conversation_data):
    """
    Process conversation data from locomo10 format into memory system format.
    Handles both text-only and image-containing messages.
    """
    processed = []
    speaker_a = conversation_data["speaker_a"]
    speaker_b = conversation_data["speaker_b"]
    
    # Find all session keys
    session_keys = [key for key in conversation_data.keys() if key.startswith("session_") and not key.endswith("_date_time")]
    
    for session_key in session_keys:
        timestamp_key = f"{session_key}_date_time"
        timestamp = conversation_data.get(timestamp_key, "")
        
        for dialog in conversation_data[session_key]:
            speaker = dialog["speaker"]
            text = dialog["text"]
            
            # Handle image content if present
            if "blip_caption" in dialog and dialog["blip_caption"]:
                text = f"{text} (image description: {dialog['blip_caption']})"
            
            # Alternate between speakers as user and assistant
            if speaker == speaker_a:
                processed.append({
                    "user_input": text,
                    "agent_response": "",
                    "timestamp": timestamp
                })
            else:
                if processed:
                    processed[-1]["agent_response"] = text
                else:
                    processed.append({
                        "user_input": "",
                        "agent_response": text,
                        "timestamp": timestamp
                    })
    
    return processed
