import os
import io
import requests
from datetime import datetime, timedelta, timezone
import pytz
from dotenv import load_dotenv

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

import google.generativeai as genai
from PIL import Image

# ====== 환경설정 ======
load_dotenv()
GEMINI_API_KEY = "AIzaSyAYjyhdgdORFmRM_LSvRdb5SUxxncI449k"
#DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Gemini 설정
genai.configure(api_key=GEMINI_API_KEY)
# Free version (High rate limits, standard performance)
model = genai.GenerativeModel('gemini-1.5-flash')

KST = pytz.timezone("Asia/Seoul")

# ====== [중요] 한글 폰트 설정 ======
# ReportLab 기본 폰트(Helvetica)는 한글을 출력하지 못하므로, 시스템에 있는 한글 폰트 경로를 지정해야 합니다.
# 예: Windows -> "C:/Windows/Fonts/malgun.ttf"
# 예: Linux/Mac -> "/usr/share/fonts/truetype/nanum/NanumGothic.ttf" (경로 확인 필요)
# ====== [Auto-Download] 한글 폰트 설정 ======
KOREAN_FONT_URL = "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
KOREAN_FONT_PATH = "NanumGothic.ttf"
FONT_NAME = "NanumGothic"

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

    # 2. Register the font with ReportLab
    try:
        pdfmetrics.registerFont(TTFont(FONT_NAME, KOREAN_FONT_PATH))
        return True
    except Exception as e:
        print(f"Font registration failed: {e}")
        return False

# Run registration
HAS_KOREAN_FONT = register_korean_font()

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


# ----- 2) KMA 이미지 URL 생성 -----
def build_kma_urls(ymd, hhh):
    base_time = f"{ymd}{hhh}"
    
    wv_url = (
        "https://www.weather.go.kr/w/repositary/image/sat/gk2a/EA/"
        f"gk2a_ami_le1b_wv063_ea020lc_{base_time}00.thn.png"
    )

    steps = ["s000", "s012", "s024", "s036", "s048"]

    surf_urls = [
        f"https://www.weather.go.kr/w/repositary/image/cht/img/"
        f"kim_gdps_erly_asia_surfce_ft06_pa4_{s}_{base_time}.png"
        for s in steps
    ]

    gph500_urls = [
        f"https://www.weather.go.kr/w/repositary/image/cht/img/"
        f"kim_gdps_erly_asia_gph500_ft06_pa4_{s}_{base_time}.png"
        for s in steps
    ]

    wnd850_urls = [
        f"https://www.weather.go.kr/w/repositary/image/cht/img/"
        f"kim_gdps_erly_asia_wnd850_ft06_pa4_{s}_{base_time}.png"
        for s in steps
    ]

    return {
        "wv": wv_url,
        "surface": surf_urls,
        "gph500": gph500_urls,
        "wnd850": wnd850_urls,
    }


# ----- 3) 이미지 다운로드 -----
def fetch_image(url, timeout=15):
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            return io.BytesIO(resp.content)
        else:
            return None
    except Exception:
        return None


# ----- 4) Gemini로 한국어 브리핑 텍스트 생성 (멀티모달) -----
def generate_briefing_text(base_utc, images_dict):
    """
    이미지 데이터를 직접 Gemini에게 전달하여 분석을 요청합니다.
    """
    valid_str = base_utc.strftime("%Y.%m.%d.%H UTC")
    kst_str = (base_utc + timedelta(hours=9)).strftime("%Y-%m-%d %H시")

    # 프롬프트 텍스트
    prompt_text = f"""
당신은 한국 기상청 수석 예보관입니다.
첨부된 위성영상(WV), 지상일기도(Surface), 500hPa, 850hPa 차트를 분석하여 일일 브리핑을 작성하세요.

Valid 시간: {valid_str} (KST: {kst_str})

아래 포맷에 맞춰 **한국어**로 작성해 주세요. 
기상학적 전문 용어를 사용하되, 논리적 근거(Reasoning)를 명확히 하세요.

1. 종관 개황 (Synoptic overview)
2. 24–48시간 주요 특징 (Key features for 24–48h)
3. 한반도 체감 날씨 (수도권/강원/충청/전라/경상/제주/해상)
4. 위험 기상 요소 (Hazards - 강풍, 호우, 대설, 풍랑 등)
5. 주요 불확실성 (Uncertainties)
6. 내부 브리핑 요약 (3~5줄)

* 지상, 500hPa, 850hPa 차트는 각각 0h, 24h, 48h 예측장입니다. 시계열 변화를 분석에 반영하세요.
"""

    # Gemini에 보낼 컨텐츠 리스트 구성
    contents = [prompt_text]

    # Helper: BytesIO -> PIL Image 변환
    def bytes_to_pil(b_io):
        if b_io is None: return None
        b_io.seek(0)
        img = Image.open(b_io)
        return img

    # 1. 위성 영상 추가
    if images_dict.get("wv"):
        contents.append("=== [이미지] GK2A 위성 영상 (수증기) ===")
        contents.append(bytes_to_pil(images_dict["wv"]))

    # 2. 주요 스텝(0h, 24h, 48h)만 선별해서 Gemini에게 전달 (토큰 절약 및 핵심 분석)
    # 인덱스: 0(0h), 2(24h), 4(48h)
    target_indices = [0, 2, 4]
    time_labels = ["Initial (00h)", "Forecast (+24h)", "Forecast (+48h)"]

    for idx, label in zip(target_indices, time_labels):
        # Surface
        if images_dict["surface"][idx]:
            contents.append(f"=== [이미지] Surface Chart {label} ===")
            contents.append(bytes_to_pil(images_dict["surface"][idx]))
        
        # 500hPa
        if images_dict["gph500"][idx]:
            contents.append(f"=== [이미지] 500hPa Chart {label} ===")
            contents.append(bytes_to_pil(images_dict["gph500"][idx]))

    # 이미지 사용 후 BytesIO 포인터가 끝으로 이동했을 수 있으므로,
    # 추후 PDF 생성 시 다시 읽을 수 있도록 외부에서 seek(0) 처리가 필요할 수 있음.
    # (여기서는 PIL.Image.open이 복사본을 메모리에 올리므로 원본 BytesIO는 영향이 적으나 안전하게 처리 필요)

    try:
        response = model.generate_content(contents)
        return response.text
    except Exception as e:
        return f"분석 생성 실패: {str(e)}"


# ----- 5) ReportLab로 단일 컬럼 PDF 생성 -----
def build_pdf(base_utc, urls, images, briefing_text) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    margin_x = 20 * mm
    margin_y = 20 * mm
    usable_width = width - 2 * margin_x

    # 폰트 선택 (한글 폰트가 등록되었으면 사용, 아니면 Helvetica)
    title_font = FONT_NAME if HAS_KOREAN_FONT else "Helvetica-Bold"
    body_font = FONT_NAME if HAS_KOREAN_FONT else "Helvetica"

    # ---- Cover / 제목 페이지 ----
    c.setFont(title_font, 18)
    c.drawString(
        margin_x,
        height - margin_y - 10 * mm,
        "Daily Briefing – Korea Peninsula",
    )
    c.setFont(body_font, 12)
    c.drawString(
        margin_x,
        height - margin_y - 20 * mm,
        f"Valid: {base_utc.strftime('%Y-%m-%d %H UTC')} "
        f"(KST {(base_utc + timedelta(hours=9)).strftime('%Y-%m-%d %H시')})",
    )
    c.showPage()

    # ---- 도판 페이지 ----
    def draw_image_page(title, img_io, caption):
        # BytesIO 리셋 (Gemini에서 읽었을 수 있으므로)
        if img_io: img_io.seek(0)

        c.setFont(title_font, 14)
        c.drawString(margin_x, height - margin_y - 10 * mm, title)

        if img_io is not None:
            img = ImageReader(img_io)
            max_w = usable_width
            max_h = height - 60 * mm
            iw, ih = img.getSize()
            scale = min(max_w / iw, max_h / ih)
            iw_scaled = iw * scale
            ih_scaled = ih * scale

            x = margin_x
            y = (height - margin_y - 20 * mm) - ih_scaled

            c.drawImage(img, x, y, iw_scaled, ih_scaled, preserveAspectRatio=True, anchor='sw')
            text_y = y - 10 * mm
        else:
            text_y = height - margin_y - 20 * mm
            c.setFont(body_font, 11)
            c.drawString(margin_x, text_y, "(이미지 로드 실패)")

        c.setFont(body_font, 10)
        c.drawString(margin_x, text_y - 5 * mm, caption)
        c.showPage()

    # WV
    draw_image_page("GK2A WV 06.3μm", images.get("wv"), urls["wv"])

    # Surface / 500 / 850 (0h, 24h, 48h)
    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(f"Surface {step_label}", images["surface"][idx], urls["surface"][idx])
    
    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(f"500 hPa {step_label}", images["gph500"][idx], urls["gph500"][idx])

    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(f"850 hPa {step_label}", images["wnd850"][idx], urls["wnd850"][idx])

    # ---- 텍스트(브리핑) 페이지 ----
    buffer2 = io.BytesIO()
    doc = SimpleDocTemplate(buffer2, pagesize=A4,
                            leftMargin=margin_x, rightMargin=margin_x,
                            topMargin=margin_y, bottomMargin=margin_y)
    
    styles = getSampleStyleSheet()
    
    # 한글 스타일 생성
    style_korean = ParagraphStyle(
        name='KoreanNormal',
        parent=styles['Normal'],
        fontName=body_font,
        fontSize=10,
        leading=16  # 줄간격
    )

    story = []
    if not HAS_KOREAN_FONT:
        story.append(Paragraph("[Warning: Korean font not found. Text may appear broken.]", styles["Normal"]))

    for line in briefing_text.split("\n"):
        line = line.strip()
        if line == "":
            story.append(Spacer(1, 4 * mm))
        else:
            # Markdown bold(**) 처리 간단 제거 (ReportLab 태그로 변환하거나 제거)
            clean_line = line.replace("**", "") 
            story.append(Paragraph(clean_line, style_korean))
            story.append(Spacer(1, 1 * mm))

    doc.build(story)

    # PDF 병합
    from PyPDF2 import PdfReader, PdfWriter
    buffer.seek(0)
    
    writer = PdfWriter()
    
    # 1. 도판 PDF
    reader_main = PdfReader(io.BytesIO(buffer.getvalue()))
    for page in reader_main.pages:
        writer.add_page(page)

    # 2. 텍스트 PDF
    reader_text = PdfReader(io.BytesIO(buffer2.getvalue()))
    for page in reader_text.pages:
        writer.add_page(page)

    final_buffer = io.BytesIO()
    writer.write(final_buffer)
    final_buffer.seek(0)
    return final_buffer.read()


# ----- 6) Discord로 PDF 업로드 -----
def post_to_discord(pdf_bytes, base_utc):
    if not DISCORD_WEBHOOK_URL:
        print("Discord Webhook URL not set. Skipping upload.")
        return

    filename = f"KP_Daily_Briefing_{base_utc.strftime('%Y%m%d_00UTC')}.pdf"
    content = (
        f"Korea Peninsula Daily Briefing (Powered by Gemini)\n"
        f"Valid: {base_utc.strftime('%Y-%m-%d %H UTC')} "
        f"(KST {(base_utc + timedelta(hours=9)).strftime('%Y-%m-%d %H시')})"
    )

    files = {
        "file": (filename, pdf_bytes, "application/pdf")
    }
    data = {
        "content": content
    }
    
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, data=data, files=files)
        resp.raise_for_status()
        print("Done: PDF sent to Discord.")
    except Exception as e:
        print(f"Failed to upload to Discord: {e}")


def main():
    base_utc, ymd, hhh = get_base_time_strings()
    print(f"Target Time: {ymd}{hhh} (00UTC)")
    
    urls = build_kma_urls(ymd, hhh)

    print("Downloading images...")
    images = {
        "wv": fetch_image(urls["wv"]),
        "surface": [fetch_image(u) for u in urls["surface"]],
        "gph500": [fetch_image(u) for u in urls["gph500"]],
        "wnd850": [fetch_image(u) for u in urls["wnd850"]],
    }
    print(images)

    print("Generating analysis with Gemini...")
    # Gemini에게 이미지를 함께 전달 (텍스트 프롬프트 + 이미지)
    briefing_text = generate_briefing_text(base_utc, images)
    print(briefing_text)

    print("Building PDF...")
    pdf_bytes = build_pdf(base_utc, urls, images, briefing_text)

    #post_to_discord(pdf_bytes, base_utc)


if __name__ == "__main__":
    main()
