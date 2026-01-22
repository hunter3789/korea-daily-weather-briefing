import os
import io
import requests
from google import genai
from google.genai import types
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import emoji
import markdown
from xhtml2pdf import pisa

# ====== 환경설정 ======
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_NEWS_WEBHOOK_URL = os.getenv("DISCORD_NEWS_WEBHOOK_URL")

# ====== [중요] 한글 폰트 설정 ======
# ReportLab 기본 폰트(Helvetica)는 한글을 출력하지 못하므로, 시스템에 있는 한글 폰트 경로를 지정해야 합니다.
# 예: Windows -> "C:/Windows/Fonts/malgun.ttf"
# 예: Linux/Mac -> "/usr/share/fonts/truetype/nanum/NanumGothic.ttf" (경로 확인 필요)
# ====== [Auto-Download] 한글 폰트 설정 ======
KOREAN_FONT_URL = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
KOREAN_FONT_PATH = "NanumGothic.ttf"

def register_korean_font():
    # 1. Check if font exists locally
    if not os.path.exists(KOREAN_FONT_PATH):
        print("Downloading Korean font (NanumGothic)...")
        try:
            resp = requests.get(KOREAN_FONT_URL, timeout=10)
            resp.raise_for_status()
            with open(KOREAN_FONT_PATH, "wb") as f:
                f.write(resp.content)
            print("Font downloaded successfully.")
        except Exception as e:
            print(f"Failed to download font: {e}")
            return False

# Run registration
HAS_KOREAN_FONT = register_korean_font()

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
        requests.post(DISCORD_NEWS_WEBHOOK_URL, json={"content": content})
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
            requests.post(DISCORD_NEWS_WEBHOOK_URL, json={"content": current_chunk})
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
        requests.post(DISCORD_NEWS_WEBHOOK_URL, json={"content": current_chunk})
        print("   -> Sent final part")
    
    print("✅ All parts posted successfully!")

def save_to_pdf(markdown_content, filename="weather_news.pdf"):
    if not markdown_content:
        return

    # --- STEP 1: CREATE A "PDF-SAFE" VERSION OF THE TEXT ---
    # We replace emojis with an empty string so they don't break the PDF.
    # If you prefer text descriptions (e.g. ":sun:"), change replace='' to replace=lambda e, data: e
    clean_content = emoji.replace_emoji(markdown_content, replace='')
    
    # --- STEP 2: CONVERT TO HTML ---
    html_body = markdown.markdown(clean_content)

    # --- STEP 3: PREPARE FONT (Must be a Korean .ttf) ---
    # Ensure this file is in your folder! 
    # Example: 'NanumGothic.ttf' or 'Malgun.ttf'
    font_path = KOREAN_FONT_PATH
    
    # We use a special path format for xhtml2pdf to find the font correctly
    font_url = os.path.abspath(font_path)

    # --- STEP 4: HTML TEMPLATE ---
    html_content = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            @font-face {{
                font-family: 'KoreanFont';
                src: url('{font_url}');
            }}
            body {{
                font-family: 'KoreanFont', sans-serif;
                font-size: 12px;
                line-height: 1.6;
                padding: 30px;
                color: #333;
            }}
            /* Make headlines look professional since we removed emojis */
            h1, h2, h3 {{ 
                color: #2c3e50; 
                border-bottom: 2px solid #eee; 
                padding-bottom: 10px;
            }}
            a {{ color: #3498db; text-decoration: none; }}
            strong {{ color: #e74c3c; }}
            .footer {{
                margin-top: 40px;
                font-size: 10px;
                color: #7f8c8d;
                border-top: 1px solid #eee;
                padding-top: 10px;
            }}
        </style>
    </head>
    <body>
        <h1>Weather News Briefing</h1>
        {html_body}
        <div class="footer">
            Generated by Gemini AI • {os.path.basename(filename)}
        </div>
    </body>
    </html>
    """

    # --- STEP 5: GENERATE PDF ---
    try:
        with open(filename, "wb") as pdf_file:
            pisa_status = pisa.CreatePDF(
                src=html_content, 
                dest=pdf_file,
                encoding='utf-8'
            )
        print(f"✅ PDF saved (Emojis removed for compatibility): {filename}")
    except Exception as e:
        print(f"❌ PDF Error: {e}")

if __name__ == "__main__":
    news_update = get_weather_news()
    #print(news_update)
    if news_update:
        post_to_discord(news_update)                
    save_to_pdf(news_update, "Daily_Weather_News.pdf")
