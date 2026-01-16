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

from openai import OpenAI

# ====== 환경설정 ======
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

client = OpenAI(api_key=OPENAI_API_KEY)

KST = pytz.timezone("Asia/Seoul")

# ----- 1) 오늘 00UTC 기준 날짜 문자열 생성 -----
def get_base_time_strings():
    # 지금 KST 기준으로 "오늘 00 UTC"를 기준 시각으로 사용
    now_kst = datetime.now(KST)
    # KST(UTC+9)에서 날짜만 가져와서 00UTC로 맞추기
    # 예: 2026-01-16 KST → 2026-01-16 00UTC
    base_utc = datetime(
        year=now_kst.year,
        month=now_kst.month,
        day=now_kst.day,
        tzinfo=timezone.utc,
    )
    ymd = base_utc.strftime("%Y%m%d")
    hhh = base_utc.strftime("%H")  # 보통 00
    return base_utc, ymd, hhh


# ----- 2) KMA 이미지 URL 생성 (패턴은 사용자가 제공한 것 그대로) -----
def build_kma_urls(ymd, hhh):
    base_time = f"{ymd}{hhh}"  # 예: 2026011500

    # WV (위성)
    wv_url = (
        "https://www.weather.go.kr/w/repositary/image/sat/gk2a/EA/"
        f"gk2a_ami_le1b_wv063_ea020lc_{base_time}00.thn.png"
    )

    # Surface / 500 / 850 (0,12,24,36,48h)
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


# ----- 3) 이미지 다운로드 (실패 시 None, 요약 페이지에서 표시) -----
def fetch_image(url, timeout=15):
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            return io.BytesIO(resp.content)
        else:
            return None
    except Exception:
        return None


# ----- 4) ChatGPT로 한국어 브리핑 텍스트 생성 -----
def generate_briefing_text(base_utc, urls):
    valid_str = base_utc.strftime("%Y.%m.%d.%H UTC")

    # 이미지 URL들을 텍스트로 함께 전달 (모델이 도판 언급 가능)
    url_text = [
        f"WV: {urls['wv']}",
        "Surface:",
        *urls["surface"],
        "500 hPa:",
        *urls["gph500"],
        "850 hPa:",
        *urls["wnd850"],
    ]
    url_block = "\n".join(url_text)

    prompt = f"""
당신은 한국 기상청 내부용 분석을 작성하는 예보관입니다.
다음은 한반도 및 동아시아 일일 브리핑에 사용할 도판 URL 목록입니다:

{url_block}

Valid 시간은 {valid_str} 입니다 (KST로는 { (base_utc + timedelta(hours=9)).strftime("%Y-%m-%d %H시") }).

아래 항목을 **한국어**로 작성해 주세요. 각 항목은 명확한 소제목을 달고, 내부 브리핑용으로 기술적인 용어 사용을 허용합니다.

1. 종관 개황 (Synoptic overview)
2. 24–48시간 주요 특징 (Key features for 24–48h)
3. 한반도 체감 날씨 (Sensible weather – 권역별: 수도권/서해안/동해안/중부내륙/남부내륙/제주/해상)
4. 위험 기상 요소 (Hazards)
5. 주요 불확실성 (Uncertainties)
6. 내부 브리핑 요약 (3~5줄)

가능하면 위성(WV), 지상, 500hPa, 850hPa 도판에서 보이는 특징을
'근거 → 해석 → 영향' 구조로 간단히 언급해 주세요 (Reasoning A 스타일).
"""

    response = client.responses.create(
        model="gpt-4.1",
        input=prompt,
    )

    text = response.output_text
    return text


# ----- 5) ReportLab로 단일 컬럼 PDF 생성 -----
def build_pdf(base_utc, urls, images, briefing_text) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    margin_x = 20 * mm
    margin_y = 20 * mm
    usable_width = width - 2 * margin_x

    # ---- Cover / 제목 페이지 ----
    c.setFont("Helvetica-Bold", 18)
    c.drawString(
        margin_x,
        height - margin_y - 10 * mm,
        "Daily Briefing – Korea Peninsula",
    )
    c.setFont("Helvetica", 12)
    c.drawString(
        margin_x,
        height - margin_y - 20 * mm,
        f"Valid: {base_utc.strftime('%Y-%m-%d %H UTC')} "
        f"(KST {(base_utc + timedelta(hours=9)).strftime('%Y-%m-%d %H시')})",
    )
    c.showPage()

    # ---- 도판 페이지: WV + Surface/500/850 일부 샘플 ----
    def draw_image_page(title, img_bytes, caption):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin_x, height - margin_y - 10 * mm, title)

        if img_bytes is not None:
            img = ImageReader(img_bytes)
            # 최대 높이/폭 비율 유지
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
            c.setFont("Helvetica-Oblique", 11)
            c.drawString(margin_x, text_y, "(이미지 로드 실패)")

        c.setFont("Helvetica", 10)
        c.drawString(margin_x, text_y - 5 * mm, caption)
        c.showPage()

    # WV
    draw_image_page(
        "GK2A WV 06.3μm",
        images.get("wv"),
        urls["wv"],
    )

    # Surface 0/24/48h 샘플
    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(
            f"Surface {step_label}",
            images["surface"][idx],
            urls["surface"][idx],
        )

    # 500 hPa 0/24/48h
    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(
            f"500 hPa {step_label}",
            images["gph500"][idx],
            urls["gph500"][idx],
        )

    # 850 hPa 0/24/48h
    for idx, step_label in zip([0, 2, 4], ["0h", "24h", "48h"]):
        draw_image_page(
            f"850 hPa {step_label}",
            images["wnd850"][idx],
            urls["wnd850"][idx],
        )

    # ---- 텍스트(브리핑) 페이지 ----
    # 간단하게 여러 페이지로 나누어 출력
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    buffer2 = io.BytesIO()
    doc = SimpleDocTemplate(buffer2, pagesize=A4,
                            leftMargin=margin_x, rightMargin=margin_x,
                            topMargin=margin_y, bottomMargin=margin_y)
    styles = getSampleStyleSheet()
    styleN = styles["Normal"]
    styleN.fontName = "Helvetica"
    story = []
    for line in briefing_text.split("\n"):
        if line.strip() == "":
            story.append(Spacer(1, 4 * mm))
        else:
            story.append(Paragraph(line.replace("  ", " "), styleN))
            story.append(Spacer(1, 2 * mm))

    doc.build(story)

    # buffer2의 페이지들을 원래 canvas 뒤에 붙이기
    from PyPDF2 import PdfReader, PdfWriter

    buffer.seek(0)
    main_pdf = buffer.getvalue()
    writer = PdfWriter()

    # 기존(도판) PDF
    reader_main = PdfReader(io.BytesIO(main_pdf))
    for page in reader_main.pages:
        writer.add_page(page)

    # 텍스트 PDF
    reader_text = PdfReader(io.BytesIO(buffer2.getvalue()))
    for page in reader_text.pages:
        writer.add_page(page)

    final_buffer = io.BytesIO()
    writer.write(final_buffer)
    final_buffer.seek(0)
    return final_buffer.read()


# ----- 6) Discord로 PDF 업로드 -----
def post_to_discord(pdf_bytes, base_utc):
    filename = f"KP_Daily_Briefing_{base_utc.strftime('%Y%m%d_00UTC')}.pdf"
    content = (
        f"Korea Peninsula Daily Briefing\n"
        f"Valid: {base_utc.strftime('%Y-%m-%d %H UTC')} "
        f"(KST {(base_utc + timedelta(hours=9)).strftime('%Y-%m-%d %H시')})"
    )

    files = {
        "file": (filename, pdf_bytes, "application/pdf")
    }
    data = {
        "content": content
    }
    resp = requests.post(DISCORD_WEBHOOK_URL, data=data, files=files)
    resp.raise_for_status()


def main():
    base_utc, ymd, hhh = get_base_time_strings()
    urls = build_kma_urls(ymd, hhh)

    # 이미지 다운로드
    images = {
        "wv": fetch_image(urls["wv"]),
        "surface": [fetch_image(u) for u in urls["surface"]],
        "gph500": [fetch_image(u) for u in urls["gph500"]],
        "wnd850": [fetch_image(u) for u in urls["wnd850"]],
    }

    # 브리핑 텍스트 생성
    briefing_text = generate_briefing_text(base_utc, urls)

    # PDF 생성
    pdf_bytes = build_pdf(base_utc, urls, images, briefing_text)

    # Discord 업로드
    post_to_discord(pdf_bytes, base_utc)

    print("Done: PDF sent to Discord.")


if __name__ == "__main__":
    main()
