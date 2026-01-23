import os
import io
import requests
from google import genai
from google.genai import types
import time
from datetime import datetime, timedelta, timezone
import pytz
from dotenv import load_dotenv
import emoji
import markdown
import textwrap
import re
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
# For Korean text you will need a CJK-capable font (see comment below)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
import unicodedata

# ====== 환경설정 ======
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_NEWS_WEBHOOK_URL = os.getenv("DISCORD_NEWS_WEBHOOK_URL")

KOREAN_FONT_NAME = "NanumGothic"
EMOJI_FONT_NAME = "Symbola"

pdfmetrics.registerFont(TTFont(KOREAN_FONT_NAME, "NanumGothic.ttf"))
pdfmetrics.registerFont(TTFont(EMOJI_FONT_NAME, "Symbola.ttf"))

KST = pytz.timezone("Asia/Seoul")

# ----- 1) 오늘 00UTC 기준 날짜 문자열 생성 -----
def get_base_time_strings():
    now_kst = datetime.now(KST)
    base_utc = datetime(
        year=now_kst.year,
        month=now_kst.month,
        day=now_kst.day,
        tzinfo=timezone.utc,
    )
    ymd = base_utc.strftime("%Y%m%d")
    hhh = base_utc.strftime("%H")
    return base_utc, ymd, hhh
    
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

def is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF   # Misc Symbols and Pictographs, etc.
        or 0x2600 <= cp <= 0x26FF  # Misc symbols (☀, ☔, etc.)
        or 0x2700 <= cp <= 0x27BF  # Dingbats
    )

def draw_segment_with_emoji(c, x, y, text: str,
                            text_font: str, emoji_font: str,
                            font_size: int = 11) -> float:
    """
    Draw a single text segment (no newlines) at (x, y),
    using text_font for normal chars and emoji_font for emojis.
    Returns the WIDTH advanced (so caller can track cursor).
    """
    cursor_x = x
    buffer = ""

    for ch in text:
        if is_emoji(ch):
            # flush normal buffer
            if buffer:
                c.setFont(text_font, font_size)
                c.drawString(cursor_x, y, buffer)
                cursor_x += pdfmetrics.stringWidth(buffer, text_font, font_size)
                buffer = ""
            # draw emoji
            c.setFont(emoji_font, font_size)
            c.drawString(cursor_x, y, ch)
            cursor_x += pdfmetrics.stringWidth(ch, emoji_font, font_size)
        else:
            buffer += ch

    if buffer:
        c.setFont(text_font, font_size)
        c.drawString(cursor_x, y, buffer)
        cursor_x += pdfmetrics.stringWidth(buffer, text_font, font_size)

    return cursor_x - x  # total width advanced
                                
def draw_markdown_line_with_links(
    c,
    x,
    y,
    line: str,
    text_font: str,
    emoji_font: str,
    font_size: int = 11,
):
    """
    Draw a single line of markdown text:
      - normal text
      - [title](url) → only 'title' is visible, clickable via linkURL
    """
    cursor_x = x
    pos = 0
    pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

    for match in pattern.finditer(line):
        start, end = match.span()
        title = match.group(1)
        url   = match.group(2)

        # text before link
        pre_text = line[pos:start]
        if pre_text:
            w = draw_segment_with_emoji(
                c, cursor_x, y, pre_text,
                text_font=text_font,
                emoji_font=emoji_font,
                font_size=font_size,
            )
            cursor_x += w

        # link text (visible)
        link_x_start = cursor_x
        w = draw_segment_with_emoji(
            c, link_x_start, y, title,
            text_font=text_font,
            emoji_font=emoji_font,
            font_size=font_size,
        )
        link_x_end = link_x_start + w

        # clickable rect
        c.linkURL(
            url,
            (link_x_start, y - 2, link_x_end, y + font_size),
            relative=0,
        )

        cursor_x = link_x_end
        pos = end

    # tail after last link
    tail = line[pos:]
    if tail:
        draw_segment_with_emoji(
            c, cursor_x, y, tail,
            text_font=text_font,
            emoji_font=emoji_font,
            font_size=font_size,
        )

def measure_text_width(text, text_font, emoji_font, font_size=11):
    from reportlab.pdfbase import pdfmetrics

    width = 0
    buffer = ""
    for ch in text:
        if is_emoji(ch):
            # flush buffer
            if buffer:
                width += pdfmetrics.stringWidth(buffer, text_font, font_size)
                buffer = ""
            width += pdfmetrics.stringWidth(ch, emoji_font, font_size)
        else:
            buffer += ch

    # flush buffer end
    if buffer:
        width += pdfmetrics.stringWidth(buffer, text_font, font_size)

    return width

def generate_weather_news_pdf_from_markdown(content_md: str,
                                            base_utc: datetime | None = None) -> bytes:
    if base_utc is None:
        base_utc = datetime.utcnow()

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    width, height = A4
    margin_left = 20 * mm
    margin_right = 20 * mm
    margin_top = 25 * mm
    margin_bottom = 20 * mm
    line_height = 14  # base spacing

    # ------------------------------------
    # 1) Header bar (colored)
    # ------------------------------------
    header_height = 25 * mm

    # Header background
    c.setFillColorRGB(0.08, 0.16, 0.32)  # dark blue
    c.rect(0, height - header_height, width, header_height, stroke=0, fill=1)

    # Title text in header
    c.setFillColorRGB(1, 1, 1)
    header_title = "전세계 주요 기상 뉴스 요약"
    header_sub = base_utc.strftime("발행: %Y-%m-%d %H:%M UTC")

    # Slight padding inside header
    header_x = margin_left
    header_y = height - header_height + 8 * mm

    draw_segment_with_emoji(
        c, header_x, header_y + 8,
        "🌍 " + header_title,
        text_font=KOREAN_FONT_NAME,
        emoji_font=EMOJI_FONT_NAME,
        font_size=13,
    )
    draw_segment_with_emoji(
        c, header_x, header_y - 2,
        header_sub,
        text_font=KOREAN_FONT_NAME,
        emoji_font=EMOJI_FONT_NAME,
        font_size=9,
    )

    # Start text area below header
    c.setFillColorRGB(0, 0, 0)
    y = height - header_height - 10 * mm

    # ------------------------------------
    # text wrapping
    # ------------------------------------
    max_chars_per_line = 55
    wrapper = textwrap.TextWrapper(width=max_chars_per_line)

    in_sources_section = False  # flag when inside "📚 Real Sources"

    for raw_line in content_md.splitlines():
        line = raw_line.rstrip("\n")

        # Horizontal rule / blank → small space
        if line.strip() == "" or line.strip() == "---":
            y -= line_height * 0.7
            continue

        # --------------------------------------------------
        # “📚 Real Sources” heading → styled section label
        # --------------------------------------------------
        if line.strip().startswith("**📚") and "Real Sources" in line:
            in_sources_section = True

            # space before section
            y -= line_height * 0.5
            if y < margin_bottom:
                c.showPage()
                y = height - margin_top

            # draw label with accent color
            label_text = line.strip().strip("*")  # remove **…**
            c.setFillColorRGB(0.15, 0.35, 0.65)  # bluish
            draw_markdown_line_with_links(
                c,
                margin_left,
                y,
                label_text,
                text_font=KOREAN_FONT_NAME,
                emoji_font=EMOJI_FONT_NAME,
                font_size=12,
            )
            c.setFillColorRGB(0, 0, 0)
            y -= line_height * 1.2
            continue

        # --------------------------------------------------
        # H2-style headlines: any line that contains **bold**
        # --------------------------------------------------
        if "**" in line:
            # If there's at least one bold segment, treat the WHOLE line as H2.
            # Remove all **...** markers for rendering.
            if re.search(r"\*\*(.+?)\*\*", line):
                headline_text = re.sub(r"\*\*(.+?)\*\*", r"\1", line).strip()
            else:
                headline_text = line.strip()

            if headline_text:
                # spacing before headline
                y -= line_height * 0.5
                if y < margin_bottom:
                    c.showPage()
                    y = height - margin_top

                h2_font_size = 13

                # Wrap headline in case it’s long
                h2_segments = wrapper.wrap(headline_text) or [""]

                for seg in h2_segments:
                    # Height of this H2 bar
                    pad_x = 4 * mm
                    pad_y = 1.5 * mm
                    rect_h = h2_font_size + pad_y * 2

                    # Page break if needed
                    if y - rect_h < margin_bottom:
                        c.showPage()
                        y = height - margin_top

                    # Measure visible text width (Korean + emoji aware)
                    text_width = measure_text_width(
                        seg,
                        text_font=KOREAN_FONT_NAME,
                        emoji_font=EMOJI_FONT_NAME,
                        font_size=h2_font_size,
                    )

                    # Background rectangle geometry
                    rect_x = margin_left - pad_x
                    rect_y = y - pad_y
                    rect_w = text_width + pad_x * 2

                    # Draw background bar
                    c.setFillColorRGB(0.90, 0.95, 1.00)  # light blue
                    c.roundRect(rect_x, rect_y, rect_w, rect_h, radius=2 * mm,
                                stroke=0, fill=1)

                    # Draw headline text on top
                    c.setFillColorRGB(0.1, 0.1, 0.1)    # dark text
                    draw_markdown_line_with_links(
                        c,
                        margin_left,
                        y,
                        seg,
                        text_font=KOREAN_FONT_NAME,
                        emoji_font=EMOJI_FONT_NAME,
                        font_size=h2_font_size,
                    )

                    y -= rect_h + line_height * 0.2

                # Reset to body text color
                c.setFillColorRGB(0, 0, 0)
                y -= line_height * 0.1
                continue

        # --------------------------------------------------
        # Bullet list: - item / * item
        # --------------------------------------------------
        bullet_match = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet_match:
            text = bullet_match.group(1)

            # If the bullet has a markdown link, avoid wrapping to keep URL hidden.
            if re.search(r'\[([^\]]+)\]\(([^)]+)\)', text):
                if y < margin_bottom:
                    c.showPage()
                    y = height - margin_top

                # Slight indent for bullets
                bullet_prefix = "• "
                # if we're in sources section, make bullet slightly smaller/grey
                if in_sources_section:
                    c.setFillColorRGB(0.2, 0.2, 0.2)
                draw_markdown_line_with_links(
                    c,
                    margin_left + 4 * mm,
                    y,
                    bullet_prefix + text,
                    text_font=KOREAN_FONT_NAME,
                    emoji_font=EMOJI_FONT_NAME,
                    font_size=10 if in_sources_section else 11,
                )
                c.setFillColorRGB(0, 0, 0)
                y -= line_height
            else:
                # bullet without link → normal wrapping
                wrapped = wrapper.wrap(text) or [""]
                for i, seg in enumerate(wrapped):
                    if y < margin_bottom:
                        c.showPage()
                        y = height - margin_top

                    prefix = "• " if i == 0 else "  "
                    draw_markdown_line_with_links(
                        c,
                        margin_left + 4 * mm,
                        y,
                        prefix + seg,
                        text_font=KOREAN_FONT_NAME,
                        emoji_font=EMOJI_FONT_NAME,
                        font_size=11,
                    )
                    y -= line_height
            continue

        # --------------------------------------------------
        # Normal paragraph line (may include links + emoji)
        # --------------------------------------------------
        for seg in wrapper.wrap(line) or [""]:
            if y < margin_bottom:
                c.showPage()
                y = height - margin_top

            draw_markdown_line_with_links(
                c,
                margin_left,
                y,
                seg,
                text_font=KOREAN_FONT_NAME,
                emoji_font=EMOJI_FONT_NAME,
                font_size=11,
            )
            y -= line_height

    c.showPage()
    c.save()

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

if __name__ == "__main__":
    news_update = get_weather_news()
    #print(news_update)
    if news_update:
        post_to_discord(news_update)         
    base_utc, ymd, hhh = get_base_time_strings()     
    pdf_filename = f"Daily_Weather_News_{base_utc.strftime('%Y%m%d_00UTC')}_Gemini.pdf"
    with open(pdf_filename, "wb") as f:
        f.write(generate_weather_news_pdf_from_markdown(news_update))
    print(f"✅ PDF saved: {pdf_filename}")      
