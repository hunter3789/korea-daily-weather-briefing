import os
import io
import requests
from datetime import datetime, timedelta, timezone
import pytz
from dotenv import load_dotenv
import json
import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as PlatypusImage, PageBreak
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT

# Map JSON keys to Display Names
REGION_MAP = {
    "seoul_metro": "수도권 (Seoul/Metro)",
    "gangwon": "강원도 (Gangwon)",
    "chungcheong": "충청권 (Chungcheong)",
    "jeolla": "전라권 (Jeolla)",
    "gyeongsang": "경상권 (Gyeongsang)",
    "jeju": "제주도 (Jeju)",
    "sea": "해상 (Marine)"
}

import google.generativeai as genai
from PIL import Image

# ====== 환경설정 ======
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Gemini 설정
genai.configure(api_key=GEMINI_API_KEY)
# Free version (High rate limits, standard performance)
model = genai.GenerativeModel('gemini-2.5-pro')

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
def fetch_image(url, timeout=120):
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

* 지상, 500hPa (와도), 850hPa (바람) 차트는 각각 0h, 24h, 48h 예측장입니다. 시계열 변화를 분석에 반영하세요.
**반드시 아래 JSON 포맷으로만 응답하세요.** (Markdown이나 기타 텍스트 금지)

{{
  "title": "한반도 일일 기상 브리핑",
  "synoptic_overview": "종관 개황 내용...",
  "key_features_24h": "24시간 예측 주요 특징...",
  "key_features_48h": "48시간 예측 주요 특징...",
  "sensible_weather": {{
      "seoul_metro": "수도권 날씨...",
      "gangwon": "강원도 날씨...",
      "chungcheong": "충청권...",
      "jeolla": "전라권...",
      "gyeongsang": "경상권...",
      "jeju": "제주도..."
  }},
  "hazards": ["위험기상요소1 주요 특징...", "위험기상요소2 주요 특징..."],
  "uncertainties": "주요 불확실성...",
  "summary": "내부 브리핑 요약 (3줄)"
}}
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

        # 850hPa
        if images_dict["wnd850"][idx]:
            contents.append(f"=== [이미지] 850hPa Chart {label} ===")
            contents.append(bytes_to_pil(images_dict["wnd850"][idx]))            

    # 이미지 사용 후 BytesIO 포인터가 끝으로 이동했을 수 있으므로,
    # 추후 PDF 생성 시 다시 읽을 수 있도록 외부에서 seek(0) 처리가 필요할 수 있음.
    # (여기서는 PIL.Image.open이 복사본을 메모리에 올리므로 원본 BytesIO는 영향이 적으나 안전하게 처리 필요)

    print(contents)
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

    # 폰트 선택
    title_font = FONT_NAME if HAS_KOREAN_FONT else "Helvetica-Bold"
    body_font = FONT_NAME if HAS_KOREAN_FONT else "Helvetica"

    # 1. Cover Page
    c.setFont(title_font, 18)
    c.drawString(margin_x, height - margin_y - 10 * mm, "Daily Briefing – Korea Peninsula")
    c.setFont(body_font, 12)
    c.drawString(
        margin_x,
        height - margin_y - 20 * mm,
        f"Valid: {base_utc.strftime('%Y-%m-%d %H UTC')} (KST {(base_utc + timedelta(hours=9)).strftime('%Y-%m-%d %H시')})"
    )
    c.showPage()

    # 2. Image Pages
    def draw_image_page(title, img_io, caption):
        if img_io: img_io.seek(0) # Reset pointer
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

    # Draw all images
    draw_image_page("GK2A WV 06.3μm", images.get("wv"), urls["wv"])

    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(f"Surface {step_label}", images["surface"][idx], urls["surface"][idx])
    
    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(f"500 hPa {step_label}", images["gph500"][idx], urls["gph500"][idx])

    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(f"850 hPa {step_label}", images["wnd850"][idx], urls["wnd850"][idx])

    # === [CRITICAL FIX] ===
    c.save()  # <--- This was missing! It finalizes the PDF file in the buffer.
    # ======================

    # 3. Text PDF Generation
    buffer2 = io.BytesIO()
    doc = SimpleDocTemplate(buffer2, pagesize=A4,
                            leftMargin=margin_x, rightMargin=margin_x,
                            topMargin=margin_y, bottomMargin=margin_y)
    styles = getSampleStyleSheet()
    style_korean = ParagraphStyle(
        name='KoreanNormal',
        parent=styles['Normal'],
        fontName=body_font,
        fontSize=10,
        leading=16
    )

    story = []
    if not HAS_KOREAN_FONT:
        story.append(Paragraph("[Warning: Korean font not found. Text may appear broken.]", styles["Normal"]))

    for line in briefing_text.split("\n"):
        line = line.strip()
        if line == "":
            story.append(Spacer(1, 4 * mm))
        else:
            clean_line = line.replace("**", "") 
            story.append(Paragraph(clean_line, style_korean))
            story.append(Spacer(1, 1 * mm))

    doc.build(story)

    # 4. Merge PDFs
    from PyPDF2 import PdfReader, PdfWriter
    buffer.seek(0)  # Go to start of the image PDF
    
    writer = PdfWriter()
    
    # Read Image PDF (now it has content)
    reader_main = PdfReader(io.BytesIO(buffer.getvalue()))
    for page in reader_main.pages: writer.add_page(page)

    # Read Text PDF
    reader_text = PdfReader(io.BytesIO(buffer2.getvalue()))
    for page in reader_text.pages: writer.add_page(page)

    final_buffer = io.BytesIO()
    writer.write(final_buffer)
    final_buffer.seek(0)

    # --- [NEW] Save to local file ---
    local_filename = f"Weather_Briefing_{ymd}_{hhh}.pdf"
    with open(local_filename, "wb") as f:
        f.write(final_buffer.read())

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

def build_stylish_pdf(base_utc, urls, images, data) -> bytes:
    buffer = io.BytesIO()
    
    # 1. Setup Document
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm
    )

    # 2. Define Custom Styles
    styles = getSampleStyleSheet()
    font_main = FONT_NAME if HAS_KOREAN_FONT else "Helvetica"
    font_bold = FONT_NAME if HAS_KOREAN_FONT else "Helvetica-Bold"

    # Title Style
    style_title = ParagraphStyle(
        'BriefingTitle', parent=styles['Heading1'],
        fontName=font_bold, fontSize=20, leading=24,
        textColor=colors.navy, alignment=TA_CENTER, spaceAfter=10
    )
    
    # Metadata Style
    style_meta = ParagraphStyle(
        'BriefingMeta', parent=styles['Normal'],
        fontName=font_main, fontSize=10, textColor=colors.gray,
        alignment=TA_CENTER, spaceAfter=20
    )

    # Section Header Style
    style_h2 = ParagraphStyle(
        'SectionHeader', parent=styles['Heading2'],
        fontName=font_bold, fontSize=13, leading=16,
        textColor=colors.darkblue,
        borderPadding=5, borderWidth=0, spaceBefore=15, spaceAfter=8
    )

    # Body Text Style
    style_body = ParagraphStyle(
        'BodyText', parent=styles['Normal'],
        fontName=font_main, fontSize=10, leading=15,
        alignment=TA_JUSTIFY, spaceAfter=5
    )

    # Hazard/Warning Style
    style_warning = ParagraphStyle(
        'WarningText', parent=styles['Normal'],
        fontName=font_bold, fontSize=10, leading=15,
        textColor=colors.darkblue
    )

    # Summary Box Style
    style_summary_box = ParagraphStyle(
        'SummaryText', parent=style_body,
        fontSize=11, leading=16, textColor=colors.black
    )

    style_warn = ParagraphStyle('Warn', parent=styles['Normal'], textColor=colors.firebrick)    

    story = []

    # ================= HEADER =================
    title_text = data.get("title", "Daily Weather Briefing")
    valid_text = f"Valid: {base_utc.strftime('%Y-%m-%d %H:00 UTC')}  |  Issued by Gemini Forecast System"
    
    story.append(Paragraph(title_text, style_title))
    story.append(Paragraph(valid_text, style_meta))
    
    # ================= SUMMARY BOX =================
    # Create a shaded box for the summary
    summary_content = data.get("summary", "").replace("\n", "<br/>")
    summary_para = Paragraph(f"<b>[요약]</b><br/>{summary_content}", style_summary_box)
    
    summary_table = Table([[summary_para]], colWidths=[170*mm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.aliceblue),
        ('BOX', (0,0), (-1,-1), 1, colors.steelblue),
        ('PADDING', (0,0), (-1,-1), 10),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 3*mm))


    # 1. Synoptic & Key Features
    story.append(Paragraph("1. Synoptic Overview & Key Features", style_h2))
    story.append(Paragraph(f"<b>[Synoptic]</b> {data.get('synoptic_overview', '-')}", style_body))

    # ================= IMAGES (GRID) =================
    # Layout: Satellite (Left) | Surface 0h (Right)
    #         Surface 24h (Left) | 500hPa 24h (Right)
    
    img_list = []
    
    # Helper to resize images for the grid
    def prep_img(img_io, width=85*mm, height=85*mm):
        if img_io:
            img_io.seek(0)
            img = PlatypusImage(img_io, width=width, height=height)
            img.hAlign = 'CENTER'
            return img
        return Paragraph("(No Image)", style_meta)

    # Row 1: Satellite & Surface 00h
    row1 = [
        [prep_img(images['wv']), prep_img(images['surface'][0])],
        [Paragraph("GK2A Satellite (WV)", style_meta), Paragraph("Surface Analysis (00h)", style_meta)]
    ]
    
    # Row 2: 500hPa 00h & 850hPa 00h
    row2 = [
        [prep_img(images['gph500'][0]), prep_img(images['wnd850'][0])],
        [Paragraph("500hPa Analysis (00h)", style_meta), Paragraph("850hPa Analysis (00h)", style_meta)]
    ]

    # Build Image Table
    img_table_data = row1 + row2
    t_img = Table(img_table_data, colWidths=[90*mm, 90*mm])
    t_img.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 2),
        ('RIGHTPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t_img)
    
    #story.append(PageBreak()) # Move text to next page for cleanliness

    # ================= TEXT REPORT =================


    #story.append(Spacer(1, 3*mm))
    #story.append(Paragraph(f"<b>[24-48h Outlook]</b> {data.get('key_features_24_48h', '-')}", style_body))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(f"<b>[24h Outlook]</b> {data.get('key_features_24h', '-')}", style_body))

    # Row 1: 500hPa & Surface 24h
    row1 = [
        [prep_img(images['gph500'][2]), prep_img(images['surface'][2])],
        [Paragraph("500hPa Analysis (+24h)", style_meta), Paragraph("Surface Analysis (+24h)", style_meta)]
    ]
    
    # Build Image Table
    img_table_data = row1
    t_img = Table(img_table_data, colWidths=[90*mm, 90*mm])
    t_img.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 2),
        ('RIGHTPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t_img)

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(f"<b>[48h Outlook]</b> {data.get('key_features_48h', '-')}", style_body))    

    # Row 1: 500hPa & Surface 48h
    row1 = [
        [prep_img(images['gph500'][4]), prep_img(images['surface'][4])],
        [Paragraph("500hPa Analysis (+48h)", style_meta), Paragraph("Surface Analysis (+48h)", style_meta)]
    ]
    
    # Build Image Table
    img_table_data = row1
    t_img = Table(img_table_data, colWidths=[90*mm, 90*mm])
    t_img.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 2),
        ('RIGHTPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t_img)

    # 2. Hazards (Highlighted)
    story.append(Spacer(1, 3*mm))    
    story.append(Paragraph("2. Hazards & Warnings", style_h2))
    hazards = data.get("hazards", [])
    if hazards:
        table_data = []      
        for h in hazards:
            # Check if hazard has a title (e.g., "대설: ...")
            if ":" in h:
                [head, body] = h.split(":")
                #h_fmt = f"<b>{head}:</b> {body}"
                table_data.append([
                    Paragraph(f"<b>{head}</b>", style_body),
                    Paragraph(body, style_body)
                ])                
            else:
                table_data.append([
                    Paragraph("", style_body),
                    Paragraph(h, style_body)
                ])     

        t_regional = Table(table_data, colWidths=[40*mm, 130*mm])
        t_regional.setStyle(TableStyle([
            ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
            ('BACKGROUND', (0,0), (0,-1), colors.whitesmoke), # Shade the region column
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('PADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(t_regional)                              
    else:
        story.append(Paragraph("No significant hazards reported.", style_body))

    # 3. Regional Weather (Table Layout)
    story.append(Paragraph("3. Regional Weather Details", style_h2))
    
    sensible = data.get("sensible_weather", {})
    table_data = []
    
    if isinstance(sensible, dict):
        for key, value in sensible.items():
            region_name = REGION_MAP.get(key, key.upper())
            # Region Column | Description Column
            table_data.append([
                Paragraph(f"<b>{region_name}</b>", style_body),
                Paragraph(value, style_body)
            ])
    
    if table_data:
        t_regional = Table(table_data, colWidths=[40*mm, 130*mm])
        t_regional.setStyle(TableStyle([
            ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
            ('BACKGROUND', (0,0), (0,-1), colors.whitesmoke), # Shade the region column
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('PADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(t_regional)

    # 4. Uncertainties
    story.append(Paragraph("4. Uncertainties", style_h2))
    story.append(Paragraph(data.get("uncertainties", "-"), style_body))

    # Build
    doc.build(story)
    
    final = io.BytesIO()
    final.write(buffer.getvalue())
    final.seek(0)
    return final.read()

def clean_parse_json(text):
    """
    Safely parses JSON from Gemini output, handling both 
    Markdown code blocks (```json ... ```) and raw JSON strings.
    """
    try:
        # 1. Try parsing directly (Best for 'response_mime_type="application/json"')
        return json.loads(text)
    except json.JSONDecodeError:
        pass  # If failed, try cleaning

    try:
        # 2. Extract JSON content using Regex (Handles ```json, ```, and plain text)
        # Looks for the first '{' and the last '}'
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            cleaned_text = match.group(0)
            return json.loads(cleaned_text)
    except (json.JSONDecodeError, AttributeError):
        pass

    # 3. Fallback: Return empty dict or raise specific error
    print(f"❌ JSON Parsing Failed. Raw text preview: {text[:100]}...")
    return {}

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

    print("Generating analysis with Gemini...")
    # Gemini에게 이미지를 함께 전달 (텍스트 프롬프트 + 이미지)
    briefing_text = generate_briefing_text(base_utc, images)
    
    print("Building PDF...")
    pdf_bytes = build_stylish_pdf(base_utc, urls, images, clean_parse_json(briefing_text))    

    post_to_discord(pdf_bytes, base_utc)

    #print("Building Stylish PDF...")
    # CALL THE NEW FUNCTION HERE
    #pdf_bytes = build_stylish_pdf(base_utc, urls, images, json_data)
    
    #pdf_filename = f"Briefing_Stylish_{ymd}_{hhh}.pdf"
    #with open(pdf_filename, "wb") as f:
    #    f.write(pdf_bytes)
    #print(f"✅ PDF saved: {pdf_filename}")

if __name__ == "__main__":
    main()







