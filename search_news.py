import os
import io
import requests
from google import genai
from google.genai import types
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# ====== 환경설정 ======
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_NEWS_WEBHOOK_URL = os.getenv("DISCORD_NEWS_WEBHOOK_URL")

def get_weather_news():
    print("🔍 Searching for weather news...")
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # Prompt: We ask for the summary, but we don't ask it to write the URLs.
    # We will attach the URLs ourselves from the metadata to ensure they are real.
    prompt = (
        "Search for the most impactful worldwide weather news from the last 24 hours. "
        "Select the top 3-5 major events. "
        "Write a short, engaging summary for Discord in Markdown. "
        "Do not invent URLs. Just write the news summaries with bold headlines. "
        "Start with a greeting and the current date. "        
        "Write in Korean, please."
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_modalities=["TEXT"]
            )
        )
        
        # 1. Get the Main Text
        main_text = response.text
        
        # 2. Extract REAL URLs from Grounding Metadata
        # This is the secret sauce to avoid fake links.
        sources_text = "\n\n**📚 Real Sources:**\n"
        
        # Check if we have grounding metadata
        if (response.candidates[0].grounding_metadata and 
            response.candidates[0].grounding_metadata.grounding_chunks):
            
            unique_links = set()
            
            for chunk in response.candidates[0].grounding_metadata.grounding_chunks:
                # In the new SDK, 'web' holds the source info
                if chunk.web and chunk.web.uri and chunk.web.title:
                    title = chunk.web.title
                    url = chunk.web.uri
                    
                    if url not in unique_links:
                        sources_text += f"- [{title}]({url})\n"
                        unique_links.add(url)
        else:
            sources_text += "(No specific source links returned by Google Search)"

        # Combine text + real links
        final_message = main_text + sources_text
        return final_message

    except Exception as e:
        print(f"Error: {e}")
        return None

def post_to_discord(content):
    """
    Splits long messages into chunks <= 2000 chars and sends them sequentially.
    """
    if not content: return

    # Discord limit
    LIMIT = 2000
    
    # If it fits in one message, just send it
    if len(content) <= LIMIT:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content})
        print("✅ Posted to Discord (Single message)")
        return

    # --- SMART SPLITTING LOGIC ---
    print(f"⚠️ Content length ({len(content)}) exceeds limit. Splitting...")
    
    lines = content.split('\n')
    current_chunk = ""
    
    for line in lines:
        # Check if adding this line (plus a newline) would exceed the limit
        if len(current_chunk) + len(line) + 1 > LIMIT:
            # Send the current chunk
            requests.post(DISCORD_WEBHOOK_URL, json={"content": current_chunk})
            print("   -> Sent part")
            
            # Reset chunk to the current line
            current_chunk = line + "\n"
            
            # Sleep briefly to be nice to Discord's servers
            time.sleep(1)
        else:
            # Add line to current chunk
            current_chunk += line + "\n"
            
    # Send any remaining text
    if current_chunk:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": current_chunk})
        print("   -> Sent final part")
    
    print("✅ All parts posted successfully!")

if __name__ == "__main__":
    news_update = get_weather_news()
    #print(news_update)
    if news_update:
        post_to_discord(news_update)                
